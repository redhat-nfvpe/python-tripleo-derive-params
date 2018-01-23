import json
import subprocess
import sys
import yaml

# Gets the profile name for flavor name.
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


# Gets the node UUID for flavor name.
def get_node_uuid(flavor_name):
    node_uuid = ''
    profile_name = get_profile_name(flavor_name)
    cmd = "openstack overcloud profiles list"
    output = subprocess.check_output(cmd,shell=True)
    lines = output.split('\n')
    heading_list = lines[1].split('|')
    for heading in heading_list:
        if 'Node UUID' in heading:
            node_uuid_index = heading_list.index(heading)
        if 'Current Profile' in heading:
            current_profile_index = heading_list.index(heading)
    for line in lines[2:len(lines)]:
        column_list = line.split('|')
        if len(column_list) > 1:
            if column_list[current_profile_index].strip() == profile_name:
                node_uuid = column_list[node_uuid_index]
    return node_uuid.strip()


# Gets the hardware data for the give node UUID
def get_introspection_data(flavor_name):
    node_uuid = get_node_uuid(flavor_name)
    cmd = "openstack baremetal introspection data save " + node_uuid
    output = subprocess.check_output(cmd,shell=True)
    hw_data = json.loads(output)
    return hw_data


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


# Gets nova cpus
def get_nova_cpus_list(hw_data, host_cpus):
    nova_cpus_list = []
    cpus = hw_data.get('numa_topology', {}).get('cpus', {})
    threads = []
    # Creates a list for all available threads in each NUMA nodes
    for cpu in cpus:
        threads.extend(cpu['thread_siblings'])
    exclude_cpus_list = host_cpus.split(',')
    for thread in threads:
        if not str(thread) in exclude_cpus_list:
            nova_cpus_list.append(thread)
    
    return ','.join([str(thread) for thread in nova_cpus_list])


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

    for key in user_input.keys():
        if not key in ['flavor',
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

        hugepage_alloc_perc = user_input.get(
            "huge_page_allocation_percentage", 50)

        print("Deriving SRIOV parameters based on "
              "flavor: %s" % user_input['flavor'])
        hw_data = get_introspection_data(user_input['flavor'])
        host_cpus = get_host_cpus_list(hw_data)
        nova_cpus = get_nova_cpus_list(hw_data, host_cpus)
        host_mem = 4096
        isol_cpus = convert_number_to_range_list(nova_cpus)
        kernel_args = get_kernel_args(hw_data, hugepage_alloc_perc)
        parameters['NovaVcpuPinSet'] = convert_number_to_range_list(nova_cpus, True)
        parameters['NovaReservedHostMemory'] = host_mem
        parameters['HostIsolatedCoreList'] = isol_cpus
        parameters['ComputeKernelArgs'] = kernel_args
        # prints the derived DPDK parameters
        for key, val in parameters.items():
            if key == "NovaVcpuPinSet":
                print('%(key)s: %(val)s' % {"key": key, "val": val})
            else:
                print('%(key)s: \"%(val)s\"' % {"key": key, "val": val})
    except Exception as exc:
        print("Error: %s" % exc)
