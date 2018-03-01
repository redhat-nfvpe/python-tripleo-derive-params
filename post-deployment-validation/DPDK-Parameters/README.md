# OSP10 Post Deployment DPDK Parameters Validation

Python scripts 'validate-dpdk-params.py' is used to debug and validate the
DPDK parameters applied on the environment with the auto generated DPDK
parameters value based on the baremetal node using the user inputs UUID,
DPDK NIC’s info, num_phy_cores_per_numa_node_for_pmd and
huge_page_allocation_percentage in post deployment.

Derived and deployed values are compared for each DPDK parameters and displays
the differences and helps to identify the parameters related issues.

We can validate DPDK parameters for any role which uses DPDK feature, but need
to run validation python scripts for each role separately with associated
flavor and other inputs.

The following is the list of parameters can be validated for the DPDK features
on the deployed environment based on introspection hardware data of first
baremetal node which is matching the provided flavor.

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
  environment and complete the overcloud deployment.
  ```
  openstack overcloud deploy --templates
  ----
  ``` 
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

Based on the environment, operator can update the default value to validate.

## User Inputs

#### flavor:                                                                    
This input parameter specifies the flavor name associated to the role to        
identify the first baremetal node and helps to derive and gets deployed
DPDK parameters.

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
$ python validate_dpdk_params.py user_inputs.json
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
$ python validate_dpdk_params.py '{"flavor": "compute", "dpdk_nics": [{"nic": "nic1", "mtu": 1500}], "num_phy_cores_per_numa_node_for_pmd": 1, "huge_page_allocation_percentage": 50}'
Validating user inputs..
{"flavor": "compute", "huge_page_allocation_percentage": 50, "num_phy_cores_per_numa_node_for_pmd": 1, "dpdk_nics": [{"nic": "nic1", "mtu": 1500}]}
Deriving DPDK parameters based on flavor: compute
NIC's and NUMA node mapping:
NIC nic1 => NUMA node 0, pCPU's: [27, 20, 16, 10, 3, 28, 21, 24, 17, 11, 4, 0, 25, 18, 12, 5, 8, 1, 26, 19, 9, 2]

Collects the parameters from node: 172.18.0.24

Camparison result between derived and deployed parameters values.

Differences:
NeutronDpdkCoreList - derived: '33,40,77,84', deployed: '10,11,22,23'
ComputeKernelArgs - derived: {'default_hugepagesz': '1GB', 'intel_iommu': 'on', 'hugepages': '126', 'hugepagesz': '1G'}, deployed: {'default_hugepagesz': '1GB', 'hugepagesz': '1G', 'hugepages': '64', 'intel_iommu': 'on'}
NovaVcpuPinSet - derived: ['2-32','34-39','41-43','46-76','78-83','85-87'], deployed: '12-21,24-87'
HostIsolatedCoreList - derived: '2-43,46-87', deployed: '10-87'
NeutronDpdkMemoryChannels - derived: "4", deployed: "8"
HostCpusList - derived: '0,1,44,45', deployed: '0,1,2,3,4,5,6,7,8,9'

No differences:
NovaReservedHostMemory - derived: 4096, deployed: 4096
NeutronDpdkSocketMemory - derived: '2048,2048', deployed: '2048,2048'

```

## Note

This python scripts can also be used to validate the parameters automatically
when any role uses both DPDK and SRIOV features.
