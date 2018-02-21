import json
import math
import re
import subprocess
import sys
import yaml

# Gets the profile name for flavor name
def get_profile_name(flavor_name):
    cmd = "openstack flavor show " + flavor_name
    output = subprocess.check_output(cmd,shell=True)
    properties = ''
    for line in output.split('\n'):
        if 'properties' in line:
            properties = line
    profile = ''
    if properties:
        profile_index = properties.index('capabilities:profile=')
        if profile_index >=0:
            profile_start_index = profile_index + len('capabilities:profile=') + 1
            profile_end_index = properties.index('\'', profile_start_index, len(properties))
            profile = properties[profile_start_index:profile_end_index]
    return profile


# Gets the first matching node UUID for flavor name
def get_node_uuid(flavor_name):
    node_uuid = ''
    profile_name = get_profile_name(flavor_name)
    cmd = "openstack overcloud profiles list -f json"
    output = subprocess.check_output(cmd,shell=True)
    profiles_list = json.loads(output)
    for profile in profiles_list:
        if profile["Current Profile"] == profile_name:
            node_uuid = profile["Node UUID"]
            break
    return node_uuid.strip()


# Gets the hardware data for the give node UUID
def get_introspection_data(flavor_name):
    node_uuid = get_node_uuid(flavor_name)
    cmd = "openstack baremetal introspection data save " + node_uuid
    output = subprocess.check_output(cmd,shell=True)
    hw_data = json.loads(output)
    return hw_data


def natural_sort_key(s):
    nsre = re.compile('([0-9]+)')
    return [int(text) if text.isdigit() else text
            for text in re.split(nsre, s)]


def is_embedded_nic(nic):
    if (nic.startswith('em') or nic.startswith('eth') or
        nic.startswith('eno')):
        return True
    return False


# Sorting the NIC's like os-net-config logic
def ordered_nics(interfaces):
    embedded_nics = []
    nics = []
    for iface in interfaces:
        nic = iface.get('name', '')
        if is_embedded_nic(nic):
             embedded_nics.append(nic)
        else:
            nics.append(nic)
    active_nics = (sorted(
        embedded_nics, key=natural_sort_key) +
        sorted(nics, key=natural_sort_key))
    return active_nics

# Gets the ordered active interfaces list
def get_interfaces_list(hw_data):
    interfaces = hw_data.get('inventory', {}).get('interfaces', [])
    # Checks whether inventory interfaces information is not available
    # in introspection data.
    if not interfaces:
        msg = 'Introspection data does not have inventory.interfaces'
        raise Exception(msg)
    active_interfaces = [iface for iface in interfaces
                         if iface.get('has_carrier', False)]
    # Checks whether active interfaces are not available
    if not active_interfaces:
        msg = 'Unable to determine active interfaces (has_carrier)'
        return Exception(msg)    
    return ordered_nics(active_interfaces)


# Gets the DPDK PMD core list
# Find the right logical CPUs to be allocated along with its
# siblings for the PMD core list
def get_dpdk_core_list(hw_data, dpdk_nics_numa_info,
                       dpdk_nic_numa_cores_count):
    dpdk_core_list = []
    nics = hw_data.get('numa_topology', {}).get('nics', {})
    cpus = hw_data.get('numa_topology', {}).get('cpus', {})
    dpdk_nics_numa_nodes = [dpdk_nic['numa_node']
                            for dpdk_nic in dpdk_nics_numa_info]

    if not nics:
       raise Exception('Introspection data does not '
                       'have numa_topology.nics')
    numa_cores = {}
    if not cpus:
       raise Exception('Introspection data does not '
                       'have numa_topology.cpus')

    numa_nodes = get_numa_nodes(hw_data)
    for node in numa_nodes:
        if node in dpdk_nics_numa_nodes:
            numa_cores[node] = dpdk_nic_numa_cores_count
        else:
            numa_cores[node] = 1

    numa_nodes_threads = {};

    for cpu in cpus:
        if not cpu['numa_node'] in numa_nodes_threads:
            numa_nodes_threads[cpu['numa_node']] = []
        numa_nodes_threads[cpu['numa_node']].extend(cpu['thread_siblings'])

    for node, node_cores_count in numa_cores.items():
        numa_node_min = min(numa_nodes_threads[node])
        cores_count = node_cores_count
        for cpu in cpus:
            if cpu['numa_node'] == node:
                # Adds threads from core which is not having least thread
                if numa_node_min not in cpu['thread_siblings']:
                    dpdk_core_list.extend(cpu['thread_siblings'])
                    cores_count -= 1
                    if cores_count == 0:
                        break
    return ','.join([str(thread) for thread in dpdk_core_list])


# Gets host cpus
def get_host_cpus_list(hw_data):
    host_cpus_list = []
    cpus = hw_data.get('numa_topology', {}).get('cpus', [])
    # Checks whether numa topology cpus information is not available
    # in introspection data.
    if not cpus:
        msg = 'Introspection data does not have numa_topology.cpus'
        raise Exception(msg)

    numa_nodes_threads = {}
    # Creates a list for all available threads in each NUMA nodes
    for cpu in cpus:
        if not cpu['numa_node'] in numa_nodes_threads:
            numa_nodes_threads[cpu['numa_node']] = []
        numa_nodes_threads[cpu['numa_node']].extend(
            cpu['thread_siblings'])

    for numa_node in sorted(numa_nodes_threads.keys()):
        node = int(numa_node)
        # Gets least thread in NUMA node
        numa_node_min = min(numa_nodes_threads[numa_node])
        for cpu in cpus:
            if cpu['numa_node'] == node:
                # Adds threads from core which is having least thread
                if numa_node_min in cpu['thread_siblings']:
                    host_cpus_list.extend(cpu['thread_siblings'])
                    break

    return ','.join([str(thread) for thread in host_cpus_list])


# Computes round off MTU value in bytes
# example: MTU value 9000 into 9216 bytes
def roundup_mtu_bytes(mtu):
    max_div_val = int(math.ceil(float(mtu) / float(1024)))
    return (max_div_val * 1024)


# Calculates socket memory for a NUMA node
def calculate_node_socket_memory(numa_node, dpdk_nics_numa_info,
                                 overhead, packet_size_in_buffer,
                                 minimum_socket_memory):
    distinct_mtu_per_node = []
    socket_memory = 0

    # For DPDK numa node
    for nics_info in dpdk_nics_numa_info:
        if (numa_node == nics_info['numa_node'] and
                not nics_info['mtu'] in distinct_mtu_per_node):
            distinct_mtu_per_node.append(nics_info['mtu'])
            roundup_mtu = roundup_mtu_bytes(nics_info['mtu'])
            socket_memory += (((roundup_mtu + overhead)
                              * packet_size_in_buffer) /
                              (1024 * 1024))

    # For Non DPDK numa node
    if socket_memory == 0:
        socket_memory = minimum_socket_memory
    # For DPDK numa node
    else:
        socket_memory += 512

    socket_memory_in_gb = int(socket_memory / 1024)
    if socket_memory % 1024 > 0:
        socket_memory_in_gb += 1
    return (socket_memory_in_gb * 1024)


# Gets the socket memory
def get_dpdk_socket_memory(hw_data, dpdk_nics_numa_info,
                           minimum_socket_memory=1500):
    dpdk_socket_memory_list = []
    overhead = 800
    packet_size_in_buffer = 4096 * 64
    numa_nodes = get_numa_nodes(hw_data)

    for node in numa_nodes:
        socket_mem = calculate_node_socket_memory(
            node, dpdk_nics_numa_info, overhead,
            packet_size_in_buffer,
            minimum_socket_memory)
        dpdk_socket_memory_list.append(socket_mem)

    return "\'"+','.join([str(sm) for sm in dpdk_socket_memory_list])+"\'"


# Gets nova cpus
def get_nova_cpus_list(hw_data, dpdk_cpus, host_cpus):
    nova_cpus_list = []
    cpus = hw_data.get('numa_topology', {}).get('cpus', {})
    threads = []
    # Creates a list for all available threads in each NUMA nodes
    for cpu in cpus:
        threads.extend(cpu['thread_siblings'])
    exclude_cpus_list = dpdk_cpus.split(',')
    exclude_cpus_list.extend(host_cpus.split(','))
    for thread in threads:
        if not str(thread) in exclude_cpus_list:
            nova_cpus_list.append(thread)
    
    return ','.join([str(thread) for thread in nova_cpus_list])


# Gets host isolated cpus
def get_host_isolated_cpus_list(dpdk_cpus, nova_cpus):
    host_isolated_cpus_list = dpdk_cpus.split(',')
    host_isolated_cpus_list.extend(nova_cpus.split(','))
    return ','.join([str(thread) for thread in host_isolated_cpus_list])


def get_physical_iface_name(ordered_nics, nic_name):
    if nic_name.startswith('nic'):
         # Nic numbering, find the actual interface name
         nic_number = int(nic_name.replace('nic', ''))
         if nic_number > 0:
             iface_name = ordered_nics[nic_number - 1]
             return iface_name
    return nic_name


# Gets NUMA info like NIC name, node and MTU for DPDK NICs
def get_dpdk_nics_numa_info(hw_data, ordered_nics, dpdk_nics_info):
    dpdk_nics_numa_info = []
    nics = hw_data.get('numa_topology', {}).get('nics', [])
    for dpdk_nic in dpdk_nics_info:
        valid_dpdk_nic = False
        for nic in nics:
            phy_nic_name = get_physical_iface_name(ordered_nics,
                                                   dpdk_nic['nic'])
            if phy_nic_name == nic['name']:
                valid_dpdk_nic = True
                dpdk_nic_info = {'nic_id': dpdk_nic['nic'],
                                 'name': phy_nic_name,
                                 'numa_node': nic['numa_node'],
                                 'mtu': dpdk_nic['mtu']}
                dpdk_nics_numa_info.append(dpdk_nic_info);
        if not valid_dpdk_nic:
            raise Exception("Invalid DPDK NIC "
                            "'%(nic)s'" % {'nic': dpdk_nic['nic']})
    return dpdk_nics_numa_info


def display_nics_numa_info(hw_data, dpdk_nics_info):
    cpus = hw_data.get('numa_topology', {}).get('cpus', {})
    if not cpus:
       raise Exception('Introspection data does not '
                       'have numa_topology.cpus')
    print('NIC\'s and NUMA node mapping:')
    for dpdk_nic in dpdk_nics_info:
        numa_node_cpus = []
        for cpu in cpus:
            if cpu['numa_node'] == dpdk_nic['numa_node']:
               numa_node_cpus.append(cpu['cpu'])
        print('NIC %(nic)s => NUMA node %(node)d, '
              'pCPU\'s: %(cpu)s' % {"nic": dpdk_nic['nic_id'],
                                    "node": dpdk_nic['numa_node'],
                                    "cpu": numa_node_cpus})
    print('')


# Gets distinct NUMA nodes in sorted order
def get_numa_nodes(hw_data):
    nics = hw_data.get('numa_topology', {}).get('nics', [])
    numa_nodes = []
    for nic in nics:
        if not nic['numa_node'] in numa_nodes:
            numa_nodes.append(nic['numa_node'])
    return sorted(numa_nodes)


# Derives kernel_args parameter
def get_kernel_args(hw_data, hugepage_alloc_perc):
    if not is_supported_default_hugepages(hw_data):
        raise Exception("default huge page size 1GB is not supported")

    total_memory = hw_data.get('inventory', {}).get('memory', {}).get('physical_mb', 0)
    hugepages = int(float((total_memory / 1024) - 4) * (float(hugepage_alloc_perc) / float(100)))
    iommu_info = ''
    cpu_model = hw_data.get('inventory', {}).get('cpu', '').get('model_name', '')
    if cpu_model.startswith('Intel'):
        iommu_info = ' intel_iommu=on'
    kernel_args = ('default_hugepagesz=1GB hugepagesz=1G '
                   'hugepages=%(hugepages)d' % {'hugepages': hugepages})
    kernel_args += iommu_info
    return kernel_args


# Checks default 1GB hugepages support
def is_supported_default_hugepages(hw_data):
    flags = hw_data.get('inventory', {}).get('cpu', {}).get('flags', [])
    return ('pdpe1gb' in flags)


# Converts number format cpus into range format
def convert_number_to_range_list(num_list, array_format = False):
    num_list = [int(num.strip(' '))
                for num in num_list.split(",")]
    num_list.sort()
    range_list = []
    range_min = num_list[0]
    for num in num_list:
        next_val = num + 1
        if next_val not in num_list:
            if range_min != num:
                range_list.append(str(range_min) + '-' + str(num))
            else:
                range_list.append(str(range_min))
            next_index = num_list.index(num) + 1
            if next_index < len(num_list):
                range_min = num_list[next_index]

    if array_format:
        return '['+','.join([("\'"+thread+"\'") for thread in range_list])+']'
    else:
        return ','.join(range_list)


# Validates the user inputs
def vaildate_user_input(user_input):
    print(json.dumps(user_input))

    if not 'flavor' in user_input.keys():
        raise Exception("Flavor is missing in user input!");

    if not 'dpdk_nics' in user_input.keys():
        raise Exception("DPDK NIC's and MTU info are missing in user input!");
    elif type(user_input['dpdk_nics']) is not list:
        raise Exception("DPDK NIC's and MTU info is invalid!")

    for key in user_input.keys():
        if not key in ['flavor', 'dpdk_nics',
                       'num_phy_cores_per_numa_node_for_pmd',
                       'huge_page_allocation_percentage']:
            raise Exception("Invalid user input '%(key)s'" % {'key': key})


if __name__ == '__main__':
    parameters = {}
    try:
        print("Validating user inputs..")        
        if len(sys.argv) != 2:
            raise Exception("Unable to determine params, user "
                            "input JSON data is missing!");

        user_input = json.loads(sys.argv[1])
        vaildate_user_input(user_input)

        dpdk_nic_numa_cores_count = user_input.get(
            "num_phy_cores_per_numa_node_for_pmd", 1)

        hugepage_alloc_perc = user_input.get(
            "huge_page_allocation_percentage", 50)

        hw_data = get_introspection_data(user_input['flavor'])
        ordered_nics = get_interfaces_list(hw_data)
        dpdk_nics_info = get_dpdk_nics_numa_info(hw_data, ordered_nics,
                                                 user_input['dpdk_nics'])
        display_nics_numa_info(hw_data, dpdk_nics_info)

        print("Deriving DPDK parameters based on "
              "flavor: %s" % user_input['flavor'])
        dpdk_cpus = get_dpdk_core_list(hw_data, dpdk_nics_info,
                                       dpdk_nic_numa_cores_count) 
        host_cpus = get_host_cpus_list(hw_data)
        dpdk_socket_memory = get_dpdk_socket_memory(hw_data, dpdk_nics_info)
        nova_cpus = get_nova_cpus_list(hw_data, dpdk_cpus, host_cpus)
        isol_cpus = get_host_isolated_cpus_list(dpdk_cpus, nova_cpus)
        mem_channels = 4
        host_mem = 4096
        isol_cpus = convert_number_to_range_list(isol_cpus)
        kernel_args = get_kernel_args(hw_data, hugepage_alloc_perc)
        parameters['NeutronDpdkCoreList'] = ("\'%(dpdk_cpus)s\'" % {"dpdk_cpus": dpdk_cpus})
        parameters['HostCpusList'] = ("\'%(host_cpus)s\'" % {"host_cpus": host_cpus})
        parameters['NeutronDpdkSocketMemory'] = dpdk_socket_memory
        parameters['NeutronDpdkMemoryChannels'] = mem_channels
        parameters['NovaVcpuPinSet'] = convert_number_to_range_list(nova_cpus, True)
        parameters['NovaReservedHostMemory'] = host_mem
        parameters['HostIsolatedCoreList'] = isol_cpus
        parameters['ComputeKernelArgs'] = kernel_args
        # prints the derived DPDK parameters
        for key, val in parameters.items():
            if key == "NeutronDpdkMemoryChannels":
                print('# Memory channels recommended value (4) is hard coded here.')
                print('# Operator can use the memory channels value based on hardware manual.');
            if key == "NovaVcpuPinSet":
                print('%(key)s: %(val)s' % {"key": key, "val": val})
            else:
                print('%(key)s: \"%(val)s\"' % {"key": key, "val": val})
    except Exception as exc:
        print("Error: %s" % exc)
