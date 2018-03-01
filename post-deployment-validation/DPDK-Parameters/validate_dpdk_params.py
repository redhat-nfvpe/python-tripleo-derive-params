import json
import math
import os
import paramiko
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
def get_introspection_data(node_uuid):
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
    return ','.join([str(thread) for thread in sorted(dpdk_core_list)])


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

    return ','.join([str(thread) for thread in sorted(host_cpus_list)])


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
    kernel_args = {}
    if not is_supported_default_hugepages(hw_data):
        raise Exception("default huge page size 1GB is not supported")

    total_memory = hw_data.get('inventory', {}).get('memory', {}).get('physical_mb', 0)
    hugepages = int(float((total_memory / 1024) - 4) * (float(hugepage_alloc_perc) / float(100)))
    iommu_info = ''
    cpu_model = hw_data.get('inventory', {}).get('cpu', '').get('model_name', '')
    if cpu_model.startswith('Intel'):
        kernel_args['intel_iommu'] = 'on'
    kernel_args['default_hugepagesz'] = '1GB'
    kernel_args['hugepagesz'] = '1G'
    kernel_args['hugepages'] = str(hugepages)
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


# Converts range format cpus into number list format
def convert_range_to_number_list(range_list):
    num_list = []                                                               
    exclude_num_list = []                                                       
    if isinstance(range_list, str):
       range_list = range_list.strip('[]').replace('\'', '').replace(' ', '')   
    if not isinstance(range_list, list):                                        
        range_list = range_list.split(',')                                      
    try:                                                                        
       for val in range_list:                                                   
           val = val.strip(' ')                                                 
           if '^' in val:                                                       
               exclude_num_list.append(int(val[1:]))                            
           elif '-' in val:                                                     
               split_list = val.split("-")                                      
               range_min = int(split_list[0])                                   
               range_max = int(split_list[1])                                   
               num_list.extend(range(range_min, (range_max + 1)))               
           else:                                                                
               num_list.append(int(val))                                        
    except ValueError as exc:                                                   
        err_msg = ("Invalid number in input param "                             
                   "'range_list': %s" % exc)                                    
        raise Exception(err_msg)                                                
                                                                                
    # here, num_list is a list of integers                                      
    return ','.join([str(num) for num in num_list if num not in exclude_num_list])


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


# gets the instance UUID by node UUID
def get_instance_uuid(node_uuid):
    instance_uuid = ''
    cmd = "ironic --json node-list"
    output = subprocess.check_output(cmd, shell=True)
    node_list = json.loads(output)
    for node in node_list:
        if node["uuid"] == node_uuid:
            instance_uuid = node["instance_uuid"] 
            break
    return instance_uuid.strip()


# gets the host ip address from instance UUID
def get_host_ip(instance_uuid):
    cmd = 'nova show '+ instance_uuid + ' | grep "ctlplane network"'
    output = subprocess.check_output(cmd, shell=True)
    host_ip = output.replace('ctlplane network', '').strip(' |\n')
    return host_ip


# gets the DPDK parameters value from deployed env
def get_parameters_value_from_env(host_ip):
    deployed_parameters = {}
    print('Collects the parameters from node: %s' % host_ip)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.load_system_host_keys()
    client.connect(host_ip, username='heat-admin')
    client.invoke_shell()
    pmd_cpus_list = get_pmd_cpus_from_env(client)
    host_cpus_list = get_host_cpus_from_env(client)
    dpdk_socket_mem = get_dpdk_socket_memory_from_env(client)
    nova_reserved_host_mem = get_nova_reserved_host_mem_from_env(client)
    nova_cpus = get_nova_cpus_from_env(client)
    host_isolated_cpus = get_host_isolated_cpus(client)
    dpdk_mem_channels = get_dpdk_mem_channels_from_env(client)
    kernel_args = get_kernel_args_from_env(client)
    client.close()
    deployed_parameters['NeutronDpdkCoreList'] = '\'' + pmd_cpus_list + '\''
    deployed_parameters['HostCpusList'] = '\'' + host_cpus_list + '\''
    deployed_parameters['NeutronDpdkSocketMemory'] = dpdk_socket_mem
    deployed_parameters['NeutronDpdkMemoryChannels'] = '\"' + dpdk_mem_channels +'\"'
    if not '[' in nova_cpus:
        nova_cpus = '\'' + nova_cpus + '\''
    deployed_parameters['NovaVcpuPinSet'] = nova_cpus
    deployed_parameters['NovaReservedHostMemory'] = nova_reserved_host_mem
    deployed_parameters['HostIsolatedCoreList'] = '\'' + host_isolated_cpus + '\''
    deployed_parameters['ComputeKernelArgs'] = kernel_args
    return deployed_parameters


# gets the PMD cpus from deployed env
def get_pmd_cpus_from_env(client):
    pmd_cpus_list = ''
    cmd = 'sudo ovs-vsctl --no-wait get Open_vSwitch . other_config:pmd-cpu-mask'
    stdin, stdout, stderr = client.exec_command(cmd)
    mask_val = str(stdout.read()).strip('\"\n')
    if mask_val:
        pmd_cpus_list = get_cpus_list_from_mask_value(mask_val)
    else:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.yaml | grep "vswitch::dpdk::core_list"'
        stdin, stdout, stderr = client.exec_command(cmd)
        pmd_cpus_list = str(stdout.read()).replace('vswitch::dpdk::core_list:', '').strip(' \"\n')
    return pmd_cpus_list


# gets the host cpus from deployed env
def get_host_cpus_from_env(client):
    host_cpus_list = ''
    cmd = 'sudo ovs-vsctl --no-wait get Open_vSwitch . other_config:dpdk-lcore-mask'
    stdin, stdout, stderr = client.exec_command(cmd)
    mask_val = str(stdout.read()).strip('\"\n')
    if mask_val:
        host_cpus_list = get_cpus_list_from_mask_value(mask_val)
    return host_cpus_list
 

# gets the DPDK socket memory from deployed env
def get_dpdk_socket_memory_from_env(client):
    dpdk_scoket_mem = ''
    cmd = 'sudo ovs-vsctl --no-wait get Open_vSwitch . other_config:dpdk-socket-mem'
    stdin, stdout, stderr = client.exec_command(cmd)
    dpdk_scoket_mem = str(stdout.read()).strip('\"\n')
    if not dpdk_scoket_mem:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.yaml | grep "vswitch::dpdk::socket_mem"'
        stdin, stdout, stderr = client.exec_command(cmd)
        dpdk_scoket_mem = str(stdout.read()).replace('vswitch::dpdk::socket_mem:', '').strip(' \"\n')
    return "\'"+dpdk_scoket_mem+"\'"


# gets the nova reserved host memory from deployed env.
def get_nova_reserved_host_mem_from_env(client):
    # nova_reserved_host_mem = 0
    cmd = 'sudo cat /etc/nova/nova.conf | grep "reserved_host_memory_mb"'
    stdin, stdout, stderr = client.exec_command(cmd)
    mem = str(stdout.read()).replace('reserved_host_memory_mb=', '').strip(' \"\n')
    if not mem:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.yaml | grep "nova::compute::reserved_host_memory"'
        stdin, stdout, stderr = client.exec_command(cmd)
        mem = str(stdout.read()).replace('nova::compute::reserved_host_memory:', '').strip(' \"\n')
    nova_reserved_host_mem = int(mem)
    return nova_reserved_host_mem


# gets the nova cpus from deployed env
def get_nova_cpus_from_env(client):
    nova_cpus = ''
    cmd = 'sudo cat /etc/nova/nova.conf | grep "vcpu_pin_set"'
    stdin, stdout, stderr = client.exec_command(cmd)
    nova_cpus = str(stdout.read()).replace('vcpu_pin_set=', '').strip(' \"\n')
    if not nova_cpus:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.yaml | grep "nova::compute::vcpu_pin_set"'
        stdin, stdout, stderr = client.exec_command(cmd)
        nova_cpus = str(stdout.read()).replace('nova::compute::vcpu_pin_set:'), ''.strip(' \"\n')
    return nova_cpus


# gets the host isolated cpus from deployed env.
def get_host_isolated_cpus(client):
    host_isolated_cpus = ''
    cmd ='sudo cat /etc/tuned/cpu-partitioning-variables.conf | grep "isolated_cores"'
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read()).strip(' \"')
    for line in output.split('\n'):
        if line.startswith('isolated_cores='):
            host_isolated_cpus = line.replace('isolated_cores=', '').strip(' \"\n')
    return host_isolated_cpus


# gets the DPDK memory channels from deployed env.
def get_dpdk_mem_channels_from_env(client):
    dpdk_mem_channels = '4'
    cmd = 'sudo ovs-vsctl --no-wait get Open_vSwitch . other_config:dpdk-extra'
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read()).strip('\"\n')
    if '-n' in output:
        extra_fields = output.split(' ')
        dpdk_mem_channels = extra_fields[extra_fields.index('-n')+1]
        if not dpdk_mem_channels:
            cmd = 'sudo cat /etc/puppet/hieradata/service_configs.yaml | grep "vswitch::dpdk::memory_channels"'
            stdin, stdout, stderr = client.exec_command(cmd)
            dpdk_mem_channels = str(stdout.read()).replace('vswitch::dpdk::memory_channels:', '').strip(' \"\n')
    return dpdk_mem_channels


# gets the kernel args from deployed env
def get_kernel_args_from_env(client):
    kernel_args = {}
    cmd = 'sudo cat /etc/default/grub | grep "GRUB_CMDLINE_LINUX="'
    stdin, stdout, stderr = client.exec_command(cmd)
    cmd_line = str(stdout.read()).replace('GRUB_CMDLINE_LINUX=', '').strip(' \"\n')
    if cmd_line:
        cmd_args = cmd_line.split(' ')
        for arg in cmd_args:
            if ('hugepages' in arg or 'intel_iommu' in arg):
                hugepage_param = arg.split('=')
                kernel_args[hugepage_param[0]] = hugepage_param[1]
    return kernel_args


# gets the cpus list from mask value
def get_cpus_list_from_mask_value(mask_val):
    cpus_list = []
    int_mask_val = int(mask_val, 16)
    bin_mask_val = bin(int_mask_val)
    bin_mask_val = str(bin_mask_val).replace('0b', '')
    rev_bin_mask_val = bin_mask_val[::-1]    
    thread = 0
    for bin_val in rev_bin_mask_val:
        if bin_val == '1':
            cpus_list.append(thread)
        thread += 1
    return ','.join([str(thread) for thread in cpus_list])


# compares the parameters between deployed and derived values.
def compare_parameters(deployed, derived):
    equal_params = []
    diff_params = []
    for derived_key in derived.keys():
        if derived_key in deployed.keys():
            for deployed_key in deployed.keys():
                if derived_key == deployed_key:
                    msg = ('%(key)s - derived: %(val1)s, deployed: '
                           '%(val2)s' % {'key': derived_key,
                                         'val1': derived[derived_key],
                                         'val2': deployed[deployed_key]})
                    if derived_key == 'NovaVcpuPinSet':
                        derived_val = convert_range_to_number_list(derived[derived_key])
                        deployed_val = convert_range_to_number_list(deployed[derived_key])
                        if derived_val == deployed_val:
                            equal_params.append(msg)
                        else:
                             diff_params.append(msg)
                    elif derived_key == 'ComputeKernelArgs':
                        derived_kernel_args = derived['ComputeKernelArgs']
                        deployed_kernel_args = deployed['ComputeKernelArgs']
                        if (derived_kernel_args['intel_iommu'] == deployed_kernel_args['intel_iommu'] and
                            derived_kernel_args['default_hugepagesz'] == deployed_kernel_args['default_hugepagesz'] and
                            derived_kernel_args['hugepagesz'] == deployed_kernel_args['hugepagesz'] and
                            derived_kernel_args['hugepages'] == deployed_kernel_args['hugepages']):
                            equal_params.append(msg)
                        else:
                            diff_params.append(msg)
                    else:
                        msg = ('%(key)s - derived: %(val1)s, deployed: '
                              '%(val2)s' % {'key': derived_key,
                                            'val1': derived[derived_key],
                                            'val2': deployed[deployed_key]})

                        if derived[derived_key] == deployed[deployed_key]:
                            equal_params.append(msg)
                        else:
                            diff_params.append(msg)
        else:
            msg = ('%(key)s - derived: %(val)s, deployed: '
                  '<not configured>' % {'key': derived_key,
                                        'val': derived[derived_key]})
            diff_params.append(msg)
    print('\nCamparison result between derived and deployed parameters values.')
    print('\nDifferences:')
    for msg in diff_params:
        print(msg)
    print('\nNo differences:')
    for msg in equal_params:
        print(msg)


# derives the DPDK parameters
def get_derive_parameters(node_uuid, user_input,
                          dpdk_nic_numa_cores_count,
                          hugepage_alloc_perc):
    parameters = {}
    hw_data = get_introspection_data(node_uuid)
    ordered_nics = get_interfaces_list(hw_data)
    dpdk_nics_info = get_dpdk_nics_numa_info(hw_data, ordered_nics,
                                             user_input['dpdk_nics'])
    display_nics_numa_info(hw_data, dpdk_nics_info)

    dpdk_cpus = get_dpdk_core_list(hw_data, dpdk_nics_info,
                                   dpdk_nic_numa_cores_count)
    host_cpus = get_host_cpus_list(hw_data)
    dpdk_socket_memory = get_dpdk_socket_memory(hw_data, dpdk_nics_info)
    nova_cpus = get_nova_cpus_list(hw_data, dpdk_cpus, host_cpus)
    isol_cpus = get_host_isolated_cpus_list(dpdk_cpus, nova_cpus)
    mem_channels = '4'
    host_mem = 4096
    isol_cpus = convert_number_to_range_list(isol_cpus)
    kernel_args = get_kernel_args(hw_data, hugepage_alloc_perc)
    parameters['NeutronDpdkCoreList'] = '\'' + dpdk_cpus + '\''
    parameters['HostCpusList'] = '\'' + host_cpus + '\''
    parameters['NeutronDpdkSocketMemory'] = dpdk_socket_memory
    parameters['NeutronDpdkMemoryChannels'] = '\"' + mem_channels + '\"'
    parameters['NovaVcpuPinSet'] = convert_number_to_range_list(nova_cpus, True)
    parameters['NovaReservedHostMemory'] = host_mem
    parameters['HostIsolatedCoreList'] = '\'' + isol_cpus + '\''
    parameters['ComputeKernelArgs'] = kernel_args
    return parameters


if __name__ == '__main__':
    try:
        print("Validating user inputs..")
        if len(sys.argv) != 2:
            raise Exception("Unable to determine params, user "
                            "input JSON data is missing!")

        user_input = json.loads(sys.argv[1])
        vaildate_user_input(user_input)
        print('Deriving DPDK parameters based on '
              'flavor: %s' % user_input['flavor'])
        dpdk_nic_numa_cores_count = user_input.get(
            "num_phy_cores_per_numa_node_for_pmd", 1)
        hugepage_alloc_perc = user_input.get(
            "huge_page_allocation_percentage", 50)
        node_uuid = get_node_uuid(user_input['flavor'])
        derived = get_derive_parameters(node_uuid, user_input,
            dpdk_nic_numa_cores_count, hugepage_alloc_perc)
        instance_uuid = get_instance_uuid(node_uuid)
        host_ip = get_host_ip(instance_uuid)
        deployed = get_parameters_value_from_env(host_ip)
        compare_parameters(deployed, derived)
    except Exception as exc:
        print("Error: %s" % exc)
