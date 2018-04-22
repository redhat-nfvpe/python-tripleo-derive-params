import json
import math
import os
import paramiko
import re
import subprocess
import sys
import yaml
from prettytable import PrettyTable

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


# Gets the physical and logical cpus info for all numa nodes.
def get_nodes_cores_info(client):
    dict_cpus = {}
    cmd = "sudo lscpu -p=NODE,CORE,CPU | grep -v ^#"
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read())
    for line in output.split('\n'):
        if line:
            cpu_info = line.split(',')
            node = int(cpu_info[0])
            cpu = int(cpu_info[1])
            thread = int(cpu_info[2])
            # CPU and NUMA node together forms a unique value, as cpu is
            # specific to a NUMA node
            # NUMA node id and cpu id tuple is used for unique key
            dict_key = node, cpu
            if dict_key in dict_cpus:
                if thread not in dict_cpus[dict_key]['thread_siblings']:
                    dict_cpus[dict_key]['thread_siblings'].append(thread)
            else:
                cpu_item = {}
                cpu_item['thread_siblings'] = [thread]
                cpu_item['cpu'] = cpu
                cpu_item['numa_node'] = node
                dict_cpus[dict_key] = cpu_item
    return dict_cpus


# Gets the total physical memory.
def get_physical_memory(client):
    mem_total_kb = 0
    # cmd = "sudo cat /proc/meminfo | grep 'MemTotal' | grep -v ^#"
    cmd ="sudo dmidecode --type memory | grep 'Size' | grep '[0-9]'"
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read())
    for line in output.split('\n'):
        if line:
            mem_info = line.split(':')[1].strip()
            mem_val = mem_info.split(' ')
            mem_unit = mem_val[1].strip(' ').lower()
            if mem_unit == 'kb':
                memory_kb = int(mem_val[0].strip(' '))
            elif mem_unit == 'mb':
                memory_kb = (int(mem_val[0].strip(' ')) * 1024)
            mem_total_kb += memory_kb
    return (mem_total_kb / 1024)


# Gets the numa nodes list
def get_numa_nodes(client):
    nodes = []
    cmd = "sudo lscpu -p=NODE | grep -v ^#"
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read())
    for line in output.split('\n'):
        if line:
            node = int(line.strip(' '))
            if node not in nodes:
                nodes.append(node)
    return nodes


# Gets the CPU model
def get_cpu_model(client):
    cmd ="sudo lscpu | grep 'Model name'"
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read())
    if output:
        return output.split(':')[1].strip(' \n')
    else:
        msg = "Unable to determine 'CPU Model name'"
        raise Exception(msg)


# Gets the CPU flages
def get_cpu_flags(client):
    cmd = "sudo lscpu | grep 'Flags'"
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read())
    if output:
        return output.split(':')[1].strip(' \n').split(' ')
    else:
        msg = "Unable to determine 'CPU Flags'"
        raise Exception(msg)    


# Derives kernel_args parameter
def get_kernel_args(client, hugepage_alloc_perc):
    kernel_args = {}
    cpu_flags = get_cpu_flags(client)
    if not is_supported_default_hugepages(cpu_flags):
        raise Exception("default huge page size 1GB is not supported")

    total_memory = get_physical_memory(client)
    hugepages = int(float((total_memory / 1024) - 4) * (float(hugepage_alloc_perc) / float(100)))
    iommu_info = ''
    cpu_model = get_cpu_model(client)
    if cpu_model.startswith('Intel'):
        kernel_args['intel_iommu'] = 'on'
    kernel_args['iommu'] = 'pt'
    kernel_args['default_hugepagesz'] = '1GB'
    kernel_args['hugepagesz'] = '1G'
    kernel_args['hugepages'] = str(hugepages)
    return kernel_args


# Checks default 1GB hugepages support
def is_supported_default_hugepages(flags):
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
    return [num for num in num_list if num not in exclude_num_list]


# Validates the user inputs
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
    cmd = 'nova show ' + instance_uuid + ' | grep "ctlplane network"'
    output = subprocess.check_output(cmd, shell=True)
    host_ip = output.replace('ctlplane network', '').strip(' |\n')
    return host_ip


# gets the nova reserved host memory from deployed env.
def get_nova_reserved_host_mem_from_env(client):
    nova_reserved_host_mem = 0
    cmd = 'sudo cat /etc/nova/nova.conf | grep "reserved_host_memory_mb" | grep -v ^#'
    stdin, stdout, stderr = client.exec_command(cmd)
    mem = str(stdout.read()).replace('reserved_host_memory_mb=', '').strip(' \"\n')
    nova_reserved_host_mem = int(mem)
    return nova_reserved_host_mem


# gets the nova reserved host memory from hiera.
def get_nova_reserved_host_mem_from_hiera(client):
    nova_reserved_host_mem = 0
    cmd = 'sudo cat /etc/puppet/hieradata/service_configs.yaml | grep "nova::compute::reserved_host_memory" | grep -v ^#'
    stdin, stdout, stderr = client.exec_command(cmd)
    mem = str(stdout.read()).replace('nova::compute::reserved_host_memory:', '').strip(' \"\n')
    nova_reserved_host_mem = int(mem)
    return nova_reserved_host_mem


# gets the nova cpus from deployed env
def get_nova_cpus_from_env(client):
    nova_cpus = ''
    cmd = 'sudo cat /etc/nova/nova.conf | grep "vcpu_pin_set" | grep -v ^#'
    stdin, stdout, stderr = client.exec_command(cmd)
    nova_cpus = str(stdout.read()).replace('vcpu_pin_set=', '').strip(' \"\n')
    return nova_cpus


# gets the nova cpus from hiera data
def get_nova_cpus_from_hiera(client):
    nova_cpus = ''
    cmd = 'sudo cat /etc/puppet/hieradata/service_configs.yaml | grep -v ^#'
    stdin, stdout, stderr = client.exec_command(cmd)
    for line in str(stdout.read()).split('\n'):
        if 'nova::compute::vcpu_pin_set:' in line and '[' in line:
            nova_cpus = line.replace('nova::compute::vcpu_pin_set:', '').strip(' ')
        elif 'nova::compute::vcpu_pin_set:' in line and '[' not in line:
            nova_cpus = line.replace('nova::compute::vcpu_pin_set:', '').strip(' ')
        elif '[' in nova_cpus and ']' not in nova_cpus:
             nova_cpus += line.strip(' ')
    return nova_cpus


# gets the host isolated cpus from deployed env.
def get_host_isolated_cpus_from_env(client):
    host_isolated_cpus = ''
    cmd ='sudo cat /etc/tuned/cpu-partitioning-variables.conf | grep "isolated_cores" | grep -v ^#'
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read()).strip(' \"')
    for line in output.split('\n'):
        if line.startswith('isolated_cores='):
            host_isolated_cpus = line.replace('isolated_cores=', '').strip(' \"\n')
    return host_isolated_cpus


# gets the kernel args from deployed env
def get_kernel_args_from_env(client):
    kernel_args = {}
    cmd = 'sudo cat /etc/default/grub | grep "GRUB_CMDLINE_LINUX=" | grep -v ^#'
    stdin, stdout, stderr = client.exec_command(cmd)
    cmd_line = str(stdout.read()).replace('GRUB_CMDLINE_LINUX=', '').strip(' \"\n')
    if cmd_line:
        cmd_args = cmd_line.split(' ')
        for arg in cmd_args:
            if ('hugepages' in arg or 'intel_iommu'in arg or 
                'iommu' in arg):
                boot_param = arg.split('=')
                kernel_args[boot_param[0]] = boot_param[1]
    return kernel_args


# gets the SRIOV parameters value from deployed env
def get_parameters_value_from_env(client, host_ip):
    deployed_parameters = {}
    print('Collects the deployed value for parameters from node: %s' % host_ip)
    nova_reserved_host_mem = get_nova_reserved_host_mem_from_env(client)
    nova_cpus = get_nova_cpus_from_env(client)
    host_isolated_cpus = get_host_isolated_cpus_from_env(client)
    kernel_args = get_kernel_args_from_env(client)
    if not '[' in nova_cpus:
        nova_cpus = '\'' + nova_cpus + '\''
    deployed_parameters['NovaVcpuPinSet'] = nova_cpus
    deployed_parameters['NovaReservedHostMemory'] = nova_reserved_host_mem
    deployed_parameters['HostIsolatedCoreList'] = '\'' + host_isolated_cpus + '\''
    deployed_parameters['ComputeKernelArgs'] = kernel_args
    return deployed_parameters


# gets the SRIOV parameters value from deployed env
def get_parameters_value_from_hiera(client, host_ip):
    hiera_parameters = {}
    print('Collects the hiera value for parameters from node: %s' % host_ip)
    nova_reserved_host_mem = get_nova_reserved_host_mem_from_hiera(client)
    nova_cpus = get_nova_cpus_from_hiera(client)
    hiera_parameters['NovaVcpuPinSet'] = nova_cpus
    hiera_parameters['NovaReservedHostMemory'] = nova_reserved_host_mem
    return hiera_parameters


# Gets host cpus
def get_host_cpus_list(cpus):
    host_cpus_list = []
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


# Validation for nova reserved host memory
def validate_nova_reserved_host_memory(nova_reserved_host_mem_env):
    host_mem = 4096
    msg = 'expected: ' + str(host_mem) + '.\n'
    if nova_reserved_host_mem_env == host_mem:
        msg = 'valid.\n'
    return msg


# Validation for nova cpus
def validate_nova_cpus(dict_cpus, nova_cpus_env, host_cpus, numa_nodes):
    msg = ''
    nova_cores = []
    nova_cpus = convert_range_to_number_list(nova_cpus_env)
    host_cpus = host_cpus.strip('\"\' ').split(',')
    dup_host_cpus = []
    for nova_cpu in nova_cpus:
        if str(nova_cpu) in host_cpus:
            dup_host_cpus.append(nova_cpu)
        for key, cpu in dict_cpus.items():
            if nova_cpu in cpu['thread_siblings']:
                if key not in nova_cores:
                    nova_cores.append(key)
                for thread in cpu['thread_siblings']:
                    if thread not in nova_cpus:
                        msg += 'Missing thread siblings for thread: ' + str(nova_cpu) + ' in nova cpus, thread siblings: ' + str(cpu['thread_siblings'])+'.\n'

    if dup_host_cpus:
        msg += 'Duplicated physical cores in host CPU\'s: ' + str(dup_host_cpus) + '.\n'
    if nova_cores:
        for node in numa_nodes:
            core_count = 0
            for nova_core in nova_cores:
                if node == nova_core[0]:
                    core_count += 1
            if core_count == 0:
                msg += 'Missing physical cores for NUMA node: \'' + str(node) + '\' in nova cpus.\n'
    return msg


# Validation for host isolated cpus
def validate_isol_cpus(dict_cpus, isol_cpus_env, host_cpus, numa_nodes):
    msg = ''
    isol_cores = []
    isol_cpus = convert_range_to_number_list(isol_cpus_env)
    host_cpus = host_cpus.strip('\"\' ').split(',')
    dup_host_cpus = []
    for isol_cpu in isol_cpus:
        if str(isol_cpu) in host_cpus:
            dup_host_cpus.append(isol_cpu)
        for key, cpu in dict_cpus.items():
            if isol_cpu in cpu['thread_siblings']:
                if key not in isol_cores:
                    isol_cores.append(key)
                for thread in cpu['thread_siblings']:
                    if thread not in isol_cpus:
                        msg += 'Missing thread siblings for thread: ' + str(isol_cpu) + ' in host isolated cpus, thread siblings: ' + str(cpu['thread_siblings'])+'.\n'

    if dup_host_cpus:
        msg += 'Duplicated in host CPU\'s: ' + str(dup_host_cpus) + '.\n'
    if isol_cores:
        for node in numa_nodes:
            core_count = 0
            for isol_core in isol_cores:
                if node == isol_core[0]:
                    core_count += 1
            if core_count == 0:
                msg += 'Missing physical cores for NUMA node: \'' + str(node) + '\' in host isolated cpus.\n'
    return msg


# Validation for kernel args
def validate_kernel_args(deployed_kernel_args, derived_kernel_args):
    msg = ('expected: default_hugepagesz=' + derived_kernel_args['default_hugepagesz'] +
           ' hugepages='+ derived_kernel_args['hugepagesz'] +
           ' hugepages=' + derived_kernel_args['hugepages'] +
           ' intel_iommu=' + derived_kernel_args['intel_iommu'] + 
           ' iommu=' + derived_kernel_args['iommu'] + '\n')
    if (derived_kernel_args['intel_iommu'] == deployed_kernel_args['intel_iommu'] and
        derived_kernel_args['default_hugepagesz'] == deployed_kernel_args['default_hugepagesz'] and
        derived_kernel_args['hugepagesz'] == deployed_kernel_args['hugepagesz'] and
        derived_kernel_args['hugepages'] == deployed_kernel_args['hugepages']):
        msg = "valid.\n"
    return msg


# Validates the SRIOV parameters
def validate_sriov_parameters(client, deployed, hiera, node_uuid,
                              hugepage_alloc_perc):
    messages = {}
    dict_cpus = get_nodes_cores_info(client)
    cpus = list(dict_cpus.values())
    numa_nodes = get_numa_nodes(client)
    host_cpus = get_host_cpus_list(cpus)
    messages['reserved_host_mem'] = validate_nova_reserved_host_memory(deployed['NovaReservedHostMemory'])
    messages['nova_cpus'] = validate_nova_cpus(dict_cpus, deployed['NovaVcpuPinSet'],
                                               host_cpus, numa_nodes) 
    messages['isol_cpus'] = validate_isol_cpus(dict_cpus, deployed['HostIsolatedCoreList'], host_cpus, numa_nodes)
    derived_kernel_args = get_kernel_args(client, hugepage_alloc_perc)
    messages['kernel_args'] = validate_kernel_args(deployed['ComputeKernelArgs'], derived_kernel_args)
    validation_messages(deployed, hiera, messages)


# Displays validation messages
def validation_messages(deployed, hiera, messages):
    t = PrettyTable(['Parameters', 'Deployment Value', 'Hiera Data', 'Validation Messages'])
    t.align["Parameters"] = "l"
    t.align["Deployment Value"] = "l"
    t.align["Hiera Data"] ="l"
    t.align["Validation Messages"] = "l"
    t.add_row(['NovaReservedHostMemory', deployed['NovaReservedHostMemory'], hiera['NovaReservedHostMemory'], messages['reserved_host_mem']])
    t.add_row(['NovaVcpuPinSet', deployed['NovaVcpuPinSet'], hiera['NovaVcpuPinSet'], messages['nova_cpus']])
    t.add_row(['HostIsolatedCoreList', deployed['HostIsolatedCoreList'], 'NA', messages['isol_cpus']])
    deployed_kernel_args = deployed['ComputeKernelArgs']
    kernel_args = ('default_hugepagesz=' + deployed_kernel_args['default_hugepagesz'] +
           ' hugepages='+ deployed_kernel_args['hugepagesz'] +
           ' hugepages=' + deployed_kernel_args['hugepages'] +
           ' intel_iommu='+ deployed_kernel_args['intel_iommu'])
    t.add_row(['ComputeKernelArgs', kernel_args, 'NA', messages['kernel_args']])
    mem_channels_msg = 'Recommended value is "4" but it should be configured based on hardware spec.'
    print(t)


# Gets environment parameters value and validates.
def validate():
   try:
        user_input= get_args(sys.argv)
        print("Validating user inputs..")
        if len(user_input.keys()) != 2:
            raise Exception("Unable to validate post deployment parameters, user "
                            "inputs format is invalid.")

        vaildate_user_input(user_input)
        hugepage_alloc_perc = user_input.get(
            "huge_page_allocation_percentage", 50)
        node_uuid = get_node_uuid(user_input['flavor'])
        instance_uuid = get_instance_uuid(node_uuid)
        host_ip = get_host_ip(instance_uuid)
        # SSH access
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.load_system_host_keys()
        client.connect(host_ip, username='heat-admin')
        client.invoke_shell()

        deployed = get_parameters_value_from_env(client, host_ip)
        hiera = get_parameters_value_from_hiera(client, host_ip)
        validate_sriov_parameters(client, deployed, hiera, node_uuid, 
                                  hugepage_alloc_perc)
        client.close()
   except Exception as exc:
        print("Error: %s" % exc)


# Gets the user input as dictionary.
def get_args(argv):
    args = {}
    while argv:
        if argv[0].startswith('--'):
            if len(argv) == 1 or argv[1].startswith('--'):
                raise Exception("Unable to validate post deployment parameters, user "
                                "inputs format is invalid.")
            else:
                args[argv[0].strip('- ')] = argv[1].strip(' ')
        argv = argv[1:] 
    return args


if __name__ == '__main__':
    validate()
