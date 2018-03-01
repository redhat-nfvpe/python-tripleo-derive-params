# OSP10 DPDK Derive Parameters

Python scripts ‘dpdk_derive_params.py’ is used to auto generate the DPDK
parameters based on the baremetal node using the user inputs UUID,
DPDK NIC’s info, num_phy_cores_per_numa_node_for_pmd and
huge_page_allocation_percentage.

We can derive DPDK parameters for any role which uses DPDK feature, but need
to run derive params python scripts for each role separately with associated
flavor and other inputs.

The following is the list of parameters can be derived automatically for
DPDK feature based on introspection hardware data of first baremetal node
which is matching the provided flavor.

```
NeutronDpdkCoreList
HostCpusList
NeutronDpdkSocketMemory
NeutronDpdkMemoryChannels
NovaReservedHostMemory
NovaVcpuPinSet
HostIsolatedCoreList
ComputeKernelArgs
```

Once DPDK parameters are derived, copy the auto generated parameters manually
from scripts output to the corresponding role parameters section in
network-environment.yaml file and deploy the overcloud with updated
network-environment.yaml file.

## Prerequisites
* During undercloud installation, the bare metal service hardware
  inspection extras (inspection_extras in undercloud.conf) should be enabled to
  retrieve hardware details.
* Once undercloud installation is completed, baremetal nodes for the overcloud
  should be registered and introspection process should be completed for
  the registered baremetal nodes.

  To register baremetal nodes using instackenv.json file,
  ```
  $ openstack overcloud node import ~/instackenv.json
  ```
  To introspect all the registered baremetal nodes,
  ```
  $ openstack overcloud node introspect --all-manageable --provide
  ```
* Tripleo-heat-templates should be copied and updated in the undercloud
  environment to deploy overcloud nodes.
* Capture the list of roles with associated node uuid which are using OVS-DPDK
  feature to derive the DPDK parameters.
  Find flavor name referring the property Overcloud[RoleName]Flavor value in
  network-environment.yaml file for any role.
  ```
  OvercloudControlFlavor: control
  OvercloudComputeOvsDpdkFlavor: computeovsdpdk
  ```
* Capture the list of DPDK NIC's (NIC numbering like nic1, nic2 ...) with MTU
  based on hardware spec.

## Parameters Default Value
* NovaReservedHostMemory parameter is 4096.
* NeutronDpdkMemoryChannels parameter is 4.

Based on the environment, operator can update the default value when copying.

## User Inputs

#### flavor:                                                                    
This input parameter specifies the flavor name associated to the role to        
identify the first baremetal node and DPDK parameters are derived based on      
that node hardware data. 

#### dpdk_nics_info:
This input parameter specifies the list of dpdk nics with MTU.

#### num_phy_cores_per_numa_node_for_pmd:
This input parameter specifies the required minimum number of cores for
the NUMA node associated with the DPDK NIC and default value is one
physical core. One physical core is assigned for the other NUMA nodes not
associated with DPDK NIC. This parameter should be set to 1.

#### huge_page_allocation_percentage:
This input parameter specifies the required percentage of total memory
(excluding NovaReservedHostMemory) that can be configured as huge pages.
The KernelArgs parameter is derived using the calculated huge pages based
on the huge_page_allocation_percentage specified. This parameter should be
set to 50.

## Usage

```
$ python dpdk_derive_params.py user_inputs.json
user_inputs.json format:
{
"flavor": "flavor name",
"dpdk_nics": [{"nic": "nic_id", "mtu": MTU}],
"num_phy_cores_per_numa_node_for_pmd": 1,
"huge_page_allocation_percentage": 50
}
```

## Example

```
$  python dpdk_derive_params.py '{"flavor": "compute", "dpdk_nics": [{"nic": "nic1", "mtu": 1500}], "num_phy_cores_per_numa_node_for_pmd": 1, "huge_page_allocation_percentage": 50}'
Validating user inputs..
{"flavor": "compute", "huge_page_allocation_percentage": 50, "num_phy_cores_per_numa_node_for_pmd": 1, "dpdk_nics": [{"nic": "nic1", "mtu": 1500}]}
NIC's and NUMA node mapping:
NIC nic1 => NUMA node 0, pCPU's: [27, 20, 16, 10, 3, 28, 21, 24, 17, 11, 4, 0, 25, 18, 12, 5, 8, 1, 26, 19, 9, 2]

Deriving DPDK parameters based on flavor: compute
NovaReservedHostMemory: 4096
NeutronDpdkCoreList: "'40,84,33,77'"
ComputeKernelArgs: "default_hugepagesz=1GB hugepagesz=1G hugepages=126 intel_iommu=on"
NovaVcpuPinSet: ['2-32','34-39','41-43','46-76','78-83','85-87']
HostIsolatedCoreList: "2-43,46-87"
# Memory channels recommended value (4) is hard coded here.
# Operator can use the memory channels value based on hardware manual.
NeutronDpdkMemoryChannels: "4"
NeutronDpdkSocketMemory: "'2048,2048'"
HostCpusList: "'0,44,1,45'"

# Overrides role-specific parameters using hiera variables
# Optional this section, copy if any parameters are needed to override for this role
# Copy required parameters to the <RoleName>ExtraConfig section
nova::compute::vcpu_pin_set: ['2-32','34-39','41-43','46-76','78-83','85-87']
nova::compute::reserved_host_memory: 4096
# Memory channels recommended value (4) is hard coded here.
# Operator can use the memory channels value based on hardware manual.
vswitch::dpdk::memory_channels: "4"
vswitch::dpdk::socket_mem: "'2048,2048'"
vswitch::dpdk::core_list: "'40,84,33,77'"
```

## Note

This python scripts can also be used to derive the parameters automatically when
any role uses both DPDK and SRIOV features.
