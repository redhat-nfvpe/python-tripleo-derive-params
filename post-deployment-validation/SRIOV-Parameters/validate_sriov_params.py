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


# validates the user inputs
def vaildate_user_input(user_input):
    print(json.dumps(user_input))

    if not 'flavor' in user_input.keys():
        raise Exception("Flavor is missing in user input!");

    for key in user_input.keys():
        if not key in ['flavor',
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
    cmd = 'nova show 0dccbea6-6d34-4d1e-a1c8-80ffbe3a6e40 | grep "ctlplane network"'
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
    nova_reserved_host_mem = get_nova_reserved_host_mem_from_env(client)
    nova_cpus = get_nova_cpus_from_env(client)
    host_isolated_cpus = get_host_isolated_cpus(client)
    kernel_args = get_kernel_args_from_env(client)
    client.close()
    if not '[' in nova_cpus:
        nova_cpus = '\'' + nova_cpus + '\''
    deployed_parameters['NovaVcpuPinSet'] = nova_cpus
    deployed_parameters['NovaReservedHostMemory'] = nova_reserved_host_mem
    deployed_parameters['HostIsolatedCoreList'] = '\'' + host_isolated_cpus + '\''
    deployed_parameters['ComputeKernelArgs'] = kernel_args
    return deployed_parameters


# gets the nova reserved host memory from deployed env.
def get_nova_reserved_host_mem_from_env(client):
    # nova_reserved_host_mem = 0
    cmd = 'sudo cat /etc/nova/nova.conf | grep "reserved_host_memory_mb"'
    stdin, stdout, stderr = client.exec_command(cmd)
    mem = str(stdout.read()).replace('reserved_host_memory_mb=', '').strip(' \"\n')
    if not mem:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.yaml | grep "nova::compute::reserved_host_memory"'
        stdin, stdout, stderr = client.exec_command(cmd)
        mem = str(stdout.read()).replace('nova::compute::reserved_host_memory:').strip(' \"\n')
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
        nova_cpus = str(stdout.read()).replace('nova::compute::vcpu_pin_set:').strip(' \"\n')
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
    print('\nCamparison result between derived and deployed DPDK parameters values.')
    print('\nDifferences:')
    for msg in diff_params:
        print(msg)
    print('\nNo differences:')
    for msg in equal_params:
        print(msg)


# derives the DPDK parameters
def get_derive_parameters(node_uuid, user_input,
                          hugepage_alloc_perc):
    parameters = {}
    hw_data = get_introspection_data(node_uuid)
    host_cpus = get_host_cpus_list(hw_data)
    nova_cpus = get_nova_cpus_list(hw_data, host_cpus)
    host_mem = 4096
    isol_cpus = convert_number_to_range_list(nova_cpus)
    kernel_args = get_kernel_args(hw_data, hugepage_alloc_perc)
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
        print('Deriving SRIOV parameters based on '
              'flavor: %s' % user_input['flavor'])
        hugepage_alloc_perc = user_input.get(
            "huge_page_allocation_percentage", 50)
        node_uuid = get_node_uuid(user_input['flavor'])
        derived = get_derive_parameters(node_uuid, user_input,
                                        hugepage_alloc_perc)
        instance_uuid = get_instance_uuid(node_uuid)
        host_ip = get_host_ip(instance_uuid)
        deployed = get_parameters_value_from_env(host_ip)
        compare_parameters(deployed, derived)
    except Exception as exc:
        print("Error: %s" % exc)
