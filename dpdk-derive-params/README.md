# OSP10 DPDK Derive Parameters

Python scripts ‘dpdk_derive_params.py’ is used to auto generate the DPDK
parameters based on the baremetal node using the user inputs UUID,
DPDK NIC’s info, num_phy_cores_per_numa_node_for_pmd and
huge_page_allocation_percentage.

We can derive DPDK parameters for any role which uses DPDK feature, but need
to run derive params python scripts for each role separately with matching
node UUID and other inputs.

The following is the list of parameters can be derived automatically for
DPDK feature based on introspection hardware data of provided node.

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
  #### Steps to get node UUID for any role:
   1. Find flavor name referring the property Overcloud[RoleName]Flavor value in
      network-environment.yaml file.
      ```
      OvercloudControlFlavor: control
      OvercloudComputeOvsDpdkFlavor: computeovsdpdk
      ```
   1. Find profile name for the flavor name
      ```
      openstack flavor show [flavor-name]
      ```
      Lists the properties associated for flavor and also comma-separated,
      where capabilities:profile property value is the associated profile for
      the flavor name.

      ```
      capabilities:boot_option='local', capabilities:profile='computeovsdpdk', cpu_arch='x86_64'
      ```
      here 'computeovsdpdk' is the profile name

   1. Find node UUID using profile name
      ```
      openstack overcloud profiles list
      ```
      Lists the node UUID and associated profile name for all the available
      baremetal nodes.
      Capture the first node matching required profile name for that role to
      run the DPDK derive params scripts.
* Capture the list of DPDK NIC's name with MTU based on hardware spec.

## Parameters Default Value
* NovaReservedHostMemory parameter is 4096.
* NeutronDpdkMemoryChannels parameter is 4.

Based on the environment, operator can update the default value when copying.

## User Inputs

#### node_uuid:
This input parameter specifies UUID of the node is used to identify the
baremetal node and DPDK parameters are derived based on that node
hardware data.

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
"node_uuid": "Baremetal node UUID",
"dpdk_nics": [{"nic": "nic_name", "mtu": MTU}],
"num_phy_cores_per_numa_node_for_pmd": 1,
"huge_page_allocation_percentage": 50
}
```

## Example

```
$ python dpdk_derive_params.py '{"node_uuid": "89c50fce-d6ac-4027-ba54-7ee222b946df",
"dpdk_nics": [{"nic": "enp132s0f0", "mtu": 1500}],
"num_phy_cores_per_numa_node_for_pmd": 1, "huge_page_allocation_percentage": 50}'
Validating user inputs..
{"huge_page_allocation_percentage": 50, "node_uuid": "89c50fce-d6ac-4027-ba54-7ee222b946df",
"num_phy_cores_per_numa_node_for_pmd": 1, "dpdk_nics": [{"nic": "enp132s0f0", "mtu": 1500}]}
Deriving DPDK parameters based on node: 89c50fce-d6ac-4027-ba54-7ee222b946df
ComputeKernelArgs: intel_iommu=on default_hugepagesz=1GB hugepagesz=1G hugepages=126
HostCpusList: 0-1,44-45
HostIsolatedCoreList: 2-43,46-87
NeutronDpdkCoreList: 33,40,77,84
NeutronDpdkMemoryChannels: 4
NeutronDpdkSocketMemory: 2048,2048
NovaReservedHostMemory: 4096
NovaVcpuPinSet: 2-32,34-39,41-43,46-76,78-83,85-87
```
