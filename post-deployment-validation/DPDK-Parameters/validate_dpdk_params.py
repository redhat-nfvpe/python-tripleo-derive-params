import argparse
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


# Gets the flavor name for role name
def get_flavor_name(role_name):
    flavor_name = ''
    param_key = 'Overcloud' + role_name + 'Flavor'
    cmd = "mistral run-action tripleo.parameters.get"
    output = subprocess.check_output(cmd,shell=True)
    result = json.loads(output)
    if result and result.get('result', {}):
        env = result.get('result', {}).get('mistral_environment_parameters', {})
        if not env:
            env = result.get('result', {}).get('environment_parameters', {})
        if env:
            flavor_name = env.get(param_key, '')
    return flavor_name


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


# Gets the DPDK NIC's mapping with NIC physical name and driver info for the given MAC.
def get_dpdk_nics_mapping(client, mac):
    cmd = "sudo cat /var/lib/os-net-config/dpdk_mapping.yaml"
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read())
    dpdk_nics_map = yaml.load(output)
    for dpdk_nic_map in dpdk_nics_map:
        if dpdk_nic_map['mac_address'] == mac:
            return dpdk_nic_map
    else:
        msg = ("Unable to determine DPDK NIC Mapping for MAC: '%(mac)s'" % {'mac':mac})
        raise Exception(msg)


# Gets the DPDK NIC's NUMA info
def get_dpdk_nics_info(client):
    dpdk_nics_info = []
    dpdk_nics = []
    cmd = "sudo ovs-vsctl --columns=name,type,admin_state --format=json list interface"
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read())
    nics = json.loads(output)
    for nic in nics.get('data', []):
        if nic and str(nic[1]) == 'dpdk' and str(nic[2]) == 'up':
           dpdk_nics.append(str(nic[0]))
    if dpdk_nics:
        cmd = "sudo ovs-vsctl --column=mac-in-use,mtu,status --format=json list interface " + ' '.join(dpdk_nics)
        stdin, stdout, stderr = client.exec_command(cmd)
        output = str(stdout.read())
        nics_info = json.loads(output)
        for nic_info in nics_info.get('data', []):
            data = {}
            data['mac'] = nic_info[0]
            data['mtu'] = nic_info[1]
            for field in nic_info[2][1]:
                if field[0] == 'numa_id':
                    data['numa_node'] = int(field[1])
            dpdk_nic_map = get_dpdk_nics_mapping(client, nic_info[0])
            data['nic'] = dpdk_nic_map['name']
            data['pci'] = dpdk_nic_map['pci_address']
            dpdk_nics_info.append(data)
    return dpdk_nics_info


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
def get_dpdk_socket_memory(client, dpdk_nics_numa_info, numa_nodes, minimum_socket_memory=1500):
    dpdk_socket_memory_list = []
    overhead = 800
    packet_size_in_buffer = 4096 * 64
    for node in numa_nodes:
        socket_mem = calculate_node_socket_memory(
            node, dpdk_nics_numa_info, overhead,
            packet_size_in_buffer,
            minimum_socket_memory)
        dpdk_socket_memory_list.append(socket_mem)

    return "\'"+','.join([str(sm) for sm in dpdk_socket_memory_list])+"\'"


# Gets the installed osp release.
def get_osp_release(client):
    cmd = 'sudo cat /etc/rhosp-release | grep -v ^#'
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read())
    if output:
        return output
    else:
        msg = "Unable to determine 'OVS Version'"
        raise Exception(msg)
          

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


# Gets the tuned active profile
def get_tuned_active_profile(client):
    cmd ="sudo tuned-adm active"
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read())
    if output:
        return output.split(':')[1].strip(' \n')
    else:
        msg = "Unable to determine tuned active profile"
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
    hugepages = int(float((total_memory / 1024) - 4) * (hugepage_alloc_perc / float(100)))
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


# returns whether containers based overcloud deployment.
def is_containers_based_deployment(client):
    containers_based_deployment = False
    cmd = 'ls -d /var/lib/kolla/config_files/'
    stdin, stdout, stderr = client.exec_command(cmd)
    if not str(stderr.read()):
        containers_based_deployment = True
    return containers_based_deployment


# gets the PMD cpus from deployed env
def get_pmd_cpus_from_env(client):
    pmd_cpus_list = ''
    cmd = 'sudo ovs-vsctl --no-wait get Open_vSwitch . other_config:pmd-cpu-mask'
    stdin, stdout, stderr = client.exec_command(cmd)
    mask_val = str(stdout.read()).strip('\"\n')
    if mask_val:
        pmd_cpus_list = get_cpus_list_from_mask_value(mask_val)
    return pmd_cpus_list


# gets the PMD cpus from hiera data
def get_pmd_cpus_from_hiera(client, containers_based_dep):
    pmd_cpus_list = ''
    cmd = ''
    if containers_based_dep:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.json'
        stdin, stdout, stderr = client.exec_command(cmd)
        hiera_json = json.loads(str(stdout.read()))
        pmd_cpus_list = hiera_json["vswitch::dpdk::pmd_core_list"]
        if pmd_cpus_list:
            pmd_cpus_list = "\'" + pmd_cpus_list + "\'"
    else:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.yaml | grep "vswitch::dpdk::core_list" | grep -v ^#'
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
    return "\'"+dpdk_scoket_mem+"\'"


# gets the DPDK socket memory from hiera data
def get_dpdk_socket_memory_from_hiera(client, containers_based_dep):
    dpdk_scoket_mem = ''
    cmd = ''
    if containers_based_dep:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.json'
        stdin, stdout, stderr = client.exec_command(cmd)
        hiera_json = json.loads(str(stdout.read()))
        dpdk_scoket_mem = hiera_json["vswitch::dpdk::socket_mem"]
        if dpdk_scoket_mem:
            dpdk_scoket_mem = "\'" + dpdk_scoket_mem + "\'"
    else:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.yaml | grep "vswitch::dpdk::socket_mem" | grep -v ^#'
        stdin, stdout, stderr = client.exec_command(cmd)
        dpdk_scoket_mem = str(stdout.read()).replace('vswitch::dpdk::socket_mem:', '').strip(' \n')
    return dpdk_scoket_mem


# gets the nova reserved host memory from deployed env.
def get_nova_reserved_host_mem_from_env(client, containers_based_dep):
    nova_reserved_host_mem = 0
    cmd = 'sudo cat /etc/nova/nova.conf | grep "reserved_host_memory_mb" | grep -v ^#'
    if containers_based_dep:
        cmd = 'sudo cat /var/lib/config-data/nova_libvirt/etc/nova/nova.conf | grep "reserved_host_memory_mb" | grep -v ^#'
    stdin, stdout, stderr = client.exec_command(cmd)
    mem = str(stdout.read()).replace('reserved_host_memory_mb=', '').strip(' \"\n')
    nova_reserved_host_mem = int(mem)
    return nova_reserved_host_mem


# gets the nova reserved host memory from hiera.
def get_nova_reserved_host_mem_from_hiera(client, containers_based_dep):
    nova_reserved_host_mem = 0
    cmd = ''
    if containers_based_dep:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.json'
        stdin, stdout, stderr = client.exec_command(cmd)
        hiera_json = json.loads(str(stdout.read()))
        nova_reserved_host_mem = hiera_json["nova::compute::reserved_host_memory"]
    else:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.yaml | grep "nova::compute::reserved_host_memory" | grep -v ^#' 
        stdin, stdout, stderr = client.exec_command(cmd)
        mem = str(stdout.read()).replace('nova::compute::reserved_host_memory:', '').strip(' \"\n')
        nova_reserved_host_mem = int(mem)
    return nova_reserved_host_mem


# gets the nova cpus from deployed env
def get_nova_cpus_from_env(client, containers_based_dep):
    nova_cpus = ''
    cmd = 'sudo cat /etc/nova/nova.conf | grep "vcpu_pin_set" | grep -v ^#'
    if containers_based_dep:
        cmd = 'sudo cat /var/lib/config-data/nova_libvirt/etc/nova/nova.conf | grep "vcpu_pin_set" | grep -v ^#'
    stdin, stdout, stderr = client.exec_command(cmd)
    nova_cpus = str(stdout.read()).replace('vcpu_pin_set=', '').strip(' \"\n')
    return nova_cpus


# gets the nova cpus from hiera data
def get_nova_cpus_from_hiera(client, containers_based_dep):
    nova_cpus = ''
    cmd = ''
    if containers_based_dep:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.json'
        stdin, stdout, stderr = client.exec_command(cmd)
        hiera_json = json.loads(str(stdout.read()))
        nova_cpus = hiera_json["nova::compute::vcpu_pin_set"]
        if nova_cpus:
            if isinstance(nova_cpus, str):
                nova_cpus = str(nova_cpus)
            else:
                nova_cpus = str([str(nova_cpu) for nova_cpu in nova_cpus])
    else:
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


# gets the DPDK memory channels from deployed env.
def get_dpdk_mem_channels_from_env(client):
    dpdk_mem_channels = '4'
    cmd = 'sudo ovs-vsctl --no-wait get Open_vSwitch . other_config:dpdk-extra'
    stdin, stdout, stderr = client.exec_command(cmd)
    output = str(stdout.read()).strip('\"\n')
    if '-n' in output:
        extra_fields = output.split(' ')
        dpdk_mem_channels = extra_fields[extra_fields.index('-n')+1]
    return dpdk_mem_channels


# gets the DPDK memory channels from hiera data.
def get_dpdk_mem_channels_from_hiera(client, containers_based_dep):
    dpdk_mem_channels = '4'
    cmd = ''
    if containers_based_dep:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.json'
        stdin, stdout, stderr = client.exec_command(cmd)
        hiera_json = json.loads(str(stdout.read()))
        dpdk_mem_channels = hiera_json["vswitch::dpdk::memory_channels"]
        if dpdk_mem_channels:
            dpdk_mem_channels =  dpdk_mem_channels
    else:
        cmd = 'sudo cat /etc/puppet/hieradata/service_configs.yaml | grep "vswitch::dpdk::memory_channels" | grep -v ^#'
        stdin, stdout, stderr = client.exec_command(cmd)
        dpdk_mem_channels = str(stdout.read()).replace('vswitch::dpdk::memory_channels:', '').strip(' \n')
    return '\"' +dpdk_mem_channels + '\"'


# gets the kernel args from deployed env
def get_kernel_args_from_env(client, containers_based_dep):
    kernel_args = {}
    cmd = 'sudo cat /etc/default/grub | grep "GRUB_CMDLINE_LINUX=" | grep -v ^#'
    if containers_based_dep:
        cmd = 'sudo cat /etc/default/grub | grep "TRIPLEO_HEAT_TEMPLATE_KERNEL_ARGS" | grep -v ^#'
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


# gets the kernel args from deployed env
def get_grub_update_status_from_env(client):
    grub_update_status = False
    cmd = 'sudo sudo cat /proc/cmdline | grep -v ^#'
    stdin, stdout, stderr = client.exec_command(cmd)
    cmd_line = str(stdout.read())
    if cmd_line:
        cmd_args = cmd_line.split(' ')
        for arg in cmd_args:
            if ('hugepages' in arg or 'intel_iommu'in arg or
                'iommu' in arg):
                grub_update_status = True
    return grub_update_status


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


# gets the DPDK parameters value from deployed env
def get_parameters_value_from_env(client,
                                  containers_based_dep,
                                  host_ip):
    deployed_parameters = {}
    print('Collects the deployed value for parameters from node: %s' % host_ip)
    pmd_cpus_list = get_pmd_cpus_from_env(client)
    host_cpus_list = get_host_cpus_from_env(client)
    dpdk_socket_mem = get_dpdk_socket_memory_from_env(client)
    nova_reserved_host_mem = get_nova_reserved_host_mem_from_env(client,
                                                                 containers_based_dep)
    nova_cpus = get_nova_cpus_from_env(client, containers_based_dep)
    host_isolated_cpus = get_host_isolated_cpus_from_env(client)
    dpdk_mem_channels = get_dpdk_mem_channels_from_env(client)
    kernel_args = get_kernel_args_from_env(client, containers_based_dep)
    tuned = get_tuned_active_profile(client)
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
    deployed_parameters['tuned'] = tuned
    return deployed_parameters


# gets the DPDK parameters value from deployed env
def get_parameters_value_from_hiera(client, containers_based_dep, host_ip):
    hiera_parameters = {}
    print('Collects the hiera value for parameters from node: %s' % host_ip)
    pmd_cpus_list = get_pmd_cpus_from_hiera(client, containers_based_dep)
    dpdk_socket_mem = get_dpdk_socket_memory_from_hiera(client,
                                                        containers_based_dep)
    nova_reserved_host_mem = get_nova_reserved_host_mem_from_hiera(client,
                                                                   containers_based_dep)
    nova_cpus = get_nova_cpus_from_hiera(client, containers_based_dep)
    dpdk_mem_channels = get_dpdk_mem_channels_from_hiera(client, containers_based_dep)
    hiera_parameters['NeutronDpdkCoreList'] =  pmd_cpus_list
    hiera_parameters['NeutronDpdkSocketMemory'] = dpdk_socket_mem
    hiera_parameters['NeutronDpdkMemoryChannels'] =  dpdk_mem_channels
    hiera_parameters['NovaVcpuPinSet'] = nova_cpus
    hiera_parameters['NovaReservedHostMemory'] = nova_reserved_host_mem
    return hiera_parameters


# displays DPDK NICS NUMA info
def display_dpdk_nics_numa_info(cpus, dpdk_nics_info):
    print('DPDK NIC\'s and NUMA node mapping:')
    for dpdk_nic in dpdk_nics_info:
        numa_node_cpus = []
        for cpu in cpus:
            if cpu['numa_node'] == dpdk_nic['numa_node']:
                numa_node_cpus.append(cpu['cpu'])
        print('NIC \"%(nic)s\": NUMA node %(node)d, '
              'Physical CPU\'s: %(node_cpus)s' % {"nic": dpdk_nic['nic'],
                                    "node": dpdk_nic['numa_node'],
                                    "node_cpus": sorted(numa_node_cpus)})
    print('')


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


# Gets the numa nodes list which are having DPDK NIC's
def get_dpdk_nics_numa_nodes(dpdk_nics_numa_info):
    dpdk_nics_numa_nodes = []
    for nics_info in dpdk_nics_numa_info:
        if nics_info['numa_node'] not in dpdk_nics_numa_nodes:
            dpdk_nics_numa_nodes.append(nics_info['numa_node'])
    return dpdk_nics_numa_nodes


# Validation for DPDK core list (PMD cores)
def validate_dpdk_core_list(dict_cpus, dpdk_core_list, host_cpus,
                            numa_nodes, dpdk_nics_numa_nodes, dpdk_nic_numa_cores_count):
    msg = ''
    dpdk_cores = []
    dpdk_cpus = dpdk_core_list.strip('\"\' ').split(',')
    host_cpus = host_cpus.strip('\"\' ').split(',')
    dup_host_cpus = []
    for dpdk_cpu in dpdk_cpus:
        if dpdk_cpu in host_cpus:
            dup_host_cpus.append(dpdk_cpu)
        for key, cpu in dict_cpus.items():
            if int(dpdk_cpu) in cpu['thread_siblings']:
                if key not in dpdk_cores:
                    dpdk_cores.append(key)
                for thread in cpu['thread_siblings']:
                    if str(thread) not in dpdk_cpus:
                        msg += ('Missing thread siblings for thread: ' + dpdk_cpu + ' in PMD cores,'
                                '\n thread siblings: ' + str(cpu['thread_siblings'])+'.\n')
        
    if dup_host_cpus:
        msg += 'Duplicated in host CPU\'s: ' + str(dup_host_cpus) + '.\n'
    if dpdk_cores:
        for node in numa_nodes:
            core_count = 0
            for dpdk_core in dpdk_cores:
                if node == dpdk_core[0]:
                    core_count += 1
            if node in dpdk_nics_numa_nodes:
                if core_count < dpdk_nic_numa_cores_count:
                    msg += ('Number of physical cores for DPDK NIC NUMA node('+ str(node) +') is less than'
                            '\n recommended cores \'' + str(dpdk_nic_numa_cores_count) +'\'.\n')
                elif core_count > dpdk_nic_numa_cores_count:
                    msg += ('Number of physical cores for DPDK NIC NUMA node('+ str(node) +') is greater'
                            '\n than recommended cores \'' + str(dpdk_nic_numa_cores_count) +'\'.\n')
            else:
                if core_count == 0:
                    msg += 'Missing physical cores for NUMA node: \'' + str(node) + '\' in PMD cores.\n'
    if not msg:
        msg = 'valid.\n'
    return msg


# Validation for host cpus list
def validate_host_cpus(host_cpus_env, host_cpus):
    msg = 'expected: ' + host_cpus + '.\n'
    if host_cpus.strip('\'"') == host_cpus_env.strip('\'"'):
        msg = 'valid.\n'
    return msg


# Validation for tuned status
def validate_tuned_status(tuned_profile_env):
    msg = 'expected: cpu-partitioning.\n'
    if tuned_profile_env == 'cpu-partitioning':
        msg = 'enabled.\n'
    return msg


# Validation for DPDK socket memory
def validate_dpdk_socket_memory(dpdk_socket_memory_env, dpdk_socket_memory):
    msg = 'expected: ' + dpdk_socket_memory + '.\n'
    if dpdk_socket_memory == dpdk_socket_memory_env:
        msg = 'valid.\n'
    return msg


# Validation for nova reserved host memory
def validate_nova_reserved_host_memory(nova_reserved_host_mem_env):
    host_mem = 4096
    msg = 'expected: ' + str(host_mem) + '.\n'
    if nova_reserved_host_mem_env == host_mem:
        msg = 'valid.\n'
    return msg


# Validation for nova cpus
def validate_nova_cpus(dict_cpus, nova_cpus_env, dpdk_cpus_env, host_cpus, numa_nodes):
    msg = ''
    nova_cores = []
    nova_cpus = convert_range_to_number_list(nova_cpus_env)
    dpdk_cpus = dpdk_cpus_env.strip('\"\' ').split(',')
    host_cpus = host_cpus.strip('\"\' ').split(',')
    dup_dpdk_cpus = []
    dup_host_cpus = []
    for nova_cpu in nova_cpus:
        if str(nova_cpu) in host_cpus:
            dup_host_cpus.append(nova_cpu)
        if str(nova_cpu) in dpdk_cpus:
            dup_dpdk_cpus.append(nova_cpu)
        for key, cpu in dict_cpus.items():
            if nova_cpu in cpu['thread_siblings']:
                if key not in nova_cores:
                    nova_cores.append(key)
                for thread in cpu['thread_siblings']:
                    if thread not in nova_cpus:
                        msg += ('Missing thread siblings for thread: ' + str(nova_cpu) + ' in nova cpus,'
                                '\n thread siblings: ' + str(cpu['thread_siblings'])+'.\n')

    if dup_host_cpus:
        msg += 'Duplicated physical cores in host CPU\'s: ' + str(dup_host_cpus) + '.\n'
    if dup_dpdk_cpus:
        msg += 'Duplicated physical cores in PMD cores: ' + str(dup_dpdk_cpus) + '.\n'
    if nova_cores:
        for node in numa_nodes:
            core_count = 0
            for nova_core in nova_cores:
                if node == nova_core[0]:
                    core_count += 1
            if core_count == 0:
                msg += 'Missing physical cores for NUMA node: \'' + str(node) + '\' in nova cpus.\n'
    if not msg:
        msg = 'valid.\n'
    return msg


# Validation for host isolated cpus
def validate_isol_cpus(dict_cpus, isol_cpus_env, host_cpus, numa_nodes):
    msg = ''
    isol_cores = []
    if not isol_cpus_env.strip('"\''):
        msg = 'Missing host isolated cpus.\n'
        return msg

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
                        msg += ('Missing thread siblings for thread: ' + str(isol_cpu) + ' in host isolated cpus,'
                                '\n thread siblings: ' + str(cpu['thread_siblings'])+'.\n')

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
    if not msg:
        msg = 'valid.\n'
    return msg


# Validation for kernel args
def validate_kernel_args(deployed_kernel_args, derived_kernel_args, grub_update_status):
    msg = ('expected: default_hugepagesz=' + derived_kernel_args['default_hugepagesz'] +
           '\n hugepages='+ derived_kernel_args['hugepagesz'] +
           '\n hugepages=' + derived_kernel_args['hugepages'] +
           '\n intel_iommu=' + derived_kernel_args['intel_iommu'] + 
           '\n iommu=' + derived_kernel_args['iommu'] + '\n')
    if (derived_kernel_args['intel_iommu'] == deployed_kernel_args['intel_iommu'] and
        derived_kernel_args['default_hugepagesz'] == deployed_kernel_args['default_hugepagesz'] and
        derived_kernel_args['hugepagesz'] == deployed_kernel_args['hugepagesz'] and
        derived_kernel_args['hugepages'] == deployed_kernel_args['hugepages']):
        msg = "valid.\n"
    if not grub_update_status:
        msg += "node is not restarted.\n"
    return msg

# Gets osp parameters name in different osp releases.
def get_osp_params_name(client):
    osp_params = {}
    osp_release = get_osp_release(client)
    if ('10' in osp_release or '11' in osp_release):
        osp_params['dpdk_cpus'] = 'NeutronDpdkCoreList'
        osp_params['socket_mem'] = 'NeutronDpdkSocketMemory'
        osp_params['isol_cpus'] = 'HostIsolatedCoreList'
        osp_params['mem_channels'] = 'NeutronDpdkMemoryChannels'
        osp_params['kernel_args'] = 'ComputeKernelArgs'
    else:
        osp_params['dpdk_cpus'] = 'OvsPmdCoreList'
        osp_params['socket_mem'] = 'OvsDpdkSocketMemory'
        osp_params['isol_cpus'] = 'IsolCpusList'
        osp_params['mem_channels'] = 'OvsDpdkMemoryChannels'
        osp_params['kernel_args'] = 'KernelArgs'
    return osp_params


# Validates the DPDK parameters
def validate_dpdk_parameters(client, deployed, hiera, node_uuid, dpdk_nic_numa_cores_count,
                          hugepage_alloc_perc):
    messages = {}
    osp_params = get_osp_params_name(client)

    dict_cpus = get_nodes_cores_info(client)
    cpus = list(dict_cpus.values())
    dpdk_nics_numa_info = get_dpdk_nics_info(client)
    display_dpdk_nics_numa_info(cpus, dpdk_nics_numa_info)
    numa_nodes = get_numa_nodes(client)
    dpdk_nics_numa_nodes = get_dpdk_nics_numa_nodes(dpdk_nics_numa_info)
    host_cpus = get_host_cpus_list(cpus)
    messages['host_cpus'] = validate_host_cpus(deployed['HostCpusList'], host_cpus)
    messages['dpdk_cpus'] = validate_dpdk_core_list(dict_cpus, deployed['NeutronDpdkCoreList'], host_cpus,
       numa_nodes, dpdk_nics_numa_nodes, dpdk_nic_numa_cores_count)
    dpdk_socket_memory = get_dpdk_socket_memory(client, dpdk_nics_numa_info, numa_nodes)
    messages['socket_mem'] = validate_dpdk_socket_memory(deployed['NeutronDpdkSocketMemory'],
                                                         dpdk_socket_memory)
    messages['reserved_host_mem'] = validate_nova_reserved_host_memory(deployed['NovaReservedHostMemory'])
    messages['nova_cpus'] = validate_nova_cpus(dict_cpus, deployed['NovaVcpuPinSet'],
                                               deployed['NeutronDpdkCoreList'], host_cpus, numa_nodes) 
    messages['isol_cpus'] = validate_isol_cpus(dict_cpus, deployed['HostIsolatedCoreList'], host_cpus, numa_nodes)
    derived_kernel_args = get_kernel_args(client, hugepage_alloc_perc)
    grub_update_status = get_grub_update_status_from_env(client)
    messages['kernel_args'] = validate_kernel_args(deployed['ComputeKernelArgs'],
                                                   derived_kernel_args,
                                                   grub_update_status)
    messages['tuned'] = validate_tuned_status(deployed['tuned'])
    validation_messages(deployed, hiera, osp_params, messages)


# Displays validation messages
def validation_messages(deployed, hiera, osp_params, messages):
    t = PrettyTable(['Parameters', 'Deployment Value', 'Hiera Data', 'Validation Messages'])
    t.align["Parameters"] = "l"
    t.align["Deployment Value"] = "l"
    t.align["Hiera Data"] ="l"
    t.align["Validation Messages"] = "l"
    t.add_row(['HostCpusList', deployed['HostCpusList'], 'NA', messages['host_cpus']]) 
    t.add_row([osp_params['dpdk_cpus'], deployed['NeutronDpdkCoreList'], hiera['NeutronDpdkCoreList'], messages['dpdk_cpus']])
    t.add_row([osp_params['socket_mem'], deployed['NeutronDpdkSocketMemory'], hiera['NeutronDpdkSocketMemory'], messages['socket_mem']])
    t.add_row(['NovaReservedHostMemory', deployed['NovaReservedHostMemory'], hiera['NovaReservedHostMemory'], messages['reserved_host_mem']])
    t.add_row(['NovaVcpuPinSet', deployed['NovaVcpuPinSet'], hiera['NovaVcpuPinSet'], messages['nova_cpus']])
    t.add_row([osp_params['isol_cpus'], deployed['HostIsolatedCoreList'], 'NA', messages['isol_cpus']])
    deployed_kernel_args = deployed['ComputeKernelArgs']
    kernel_args = ('default_hugepagesz=' + deployed_kernel_args['default_hugepagesz'] + '\n'+
           ' hugepages='+ deployed_kernel_args['hugepagesz'] + '\n' +
           ' hugepages=' + deployed_kernel_args['hugepages'] + '\n' +
           ' intel_iommu='+ deployed_kernel_args['intel_iommu'])
    t.add_row([osp_params['kernel_args'], kernel_args, 'NA', messages['kernel_args']])
    mem_channels_msg = 'Recommended value is "4" but it should be configured based on hardware spec.\n'
    t.add_row([osp_params['mem_channels'], deployed['NeutronDpdkMemoryChannels'], hiera['NeutronDpdkMemoryChannels'], mem_channels_msg])
    t.add_row(['tuned', deployed['tuned'], 'NA', messages['tuned']])
    print(t)


# Gets environment parameters value and validates.
def validate():
   try:
        opts = parse_opts(sys.argv)
        print("Validating user inputs..")
        validate_user_input(opts)
        dpdk_nic_numa_cores_count = int(opts.num_phy_cores_per_numa_node_for_pmd)
        hugepage_alloc_perc = float(opts.huge_page_allocation_percentage)
        flavor = get_flavor_name(opts.role_name)
        node_uuid = get_node_uuid(flavor)
        instance_uuid = get_instance_uuid(node_uuid)
        host_ip = get_host_ip(instance_uuid)
        # SSH access
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.load_system_host_keys()
        client.connect(host_ip, username='heat-admin')
        client.invoke_shell()
        containers_based_dep = is_containers_based_deployment(client)
        deployed = get_parameters_value_from_env(client,
                                                 containers_based_dep,
                                                 host_ip)
        hiera = get_parameters_value_from_hiera(client,
                                                containers_based_dep,
                                                host_ip)
        validate_dpdk_parameters(client, deployed, hiera, node_uuid, 
            dpdk_nic_numa_cores_count, hugepage_alloc_perc)
        client.close()
   except Exception as exc:
        print("Error: %s" % exc)


# Validates the user inputs
def validate_user_input(inputs):
    print(json.dumps({"role_name": inputs.role_name,
                      "num_phy_cores_per_numa_node_for_pmd": int(inputs.num_phy_cores_per_numa_node_for_pmd),
                      "huge_page_allocation_percentage":int( inputs.huge_page_allocation_percentage)}))
    if not inputs.role_name:
        raise Exception("Role name is missing in user input!");


# Gets the user input as dictionary.
def parse_opts(argv):
    parser = argparse.ArgumentParser(
        description='Interactive tool')
    parser.add_argument('-r', '--role_name',
                        metavar='ROLE NAME',
                        help="""role name.""",
                        default='')
    parser.add_argument('-n', '--num_phy_cores_per_numa_node_for_pmd',
                        metavar='NUMBER OF PHYSICAL CORES PER NUMA FOR PMD',
                        help="""number of physical cores per numa node for pmd.""",
                        default=1)
    parser.add_argument('-m', '--huge_page_allocation_percentage',
                        metavar='HUGEPAGE ALLOCATION PERCENTAGE',
                        help="""hugepage allocation percentage""",
                        default=50)
    opts = parser.parse_args(argv[1:])
    return opts


if __name__ == '__main__':
    validate()
