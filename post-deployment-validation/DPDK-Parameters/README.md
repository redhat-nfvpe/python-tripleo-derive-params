# OSP10 Post Deployment DPDK Parameters Validation

Python scripts 'validate-dpdk-params.py' is used to debug and validate the
DPDK parameters applied on the environment automatically based on user inputs
flavor name, num_phy_cores_per_numa_node_for_pmd and
huge_page_allocation_percentage in post deployment.

Deployed value is validated for each DPDK parameters and displays
the differences and helps to identify the parameters related issues.

We can validate DPDK parameters for any role which uses DPDK feature, but need
to run validation python scripts for each role separately with associated
flavor and other inputs.

The following is the list of parameters can be validated for the DPDK features
on the deployed environment based on first baremetal node which is matching
the provided flavor.

```
NeutronDpdkCoreList
HostCpusList
NeutronDpdkSocketMemory
NeutronDpdkMemoryChannels
NovaReservedHostMemory
NovaVcpuPinSet
HostIsolatedCoreList
ComputeKernelArgs
tuned
```

## Prerequisites
* During undercloud installation, the bare metal service hardware
  inspection extras (inspection_extras in undercloud.conf) should be enabled to
  retrieve hardware details.
* Once undercloud installation is completed and baremetal nodes for the overcloud
  should be registered.

  To register baremetal nodes using instackenv.json file,
  ```
  $ openstack overcloud node import ~/instackenv.json
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

## Parameters Recommended Value
* NovaReservedHostMemory parameter is 4096.
* NeutronDpdkMemoryChannels parameter is "4".

Based on the environment, operator can update the recommended value to validate.

## User Inputs

#### flavor:                                                                    
This input parameter specifies the flavor name associated to the role to        
identify the first baremetal node and helps to derive and gets deployed
DPDK parameters.

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
$ python validate_dpdk_params.py --flavor "flavor name" --num_phy_cores_per_numa_node_for_pmd 1 --huge_page_allocation_percentage 50

```

## Example

```
$ python validate_dpdk_params.py --flavor "computeovsdpdk" --num_phy_cores_per_numa_node_for_pmd 1 --huge_page_allocation_percentage 50
Validating user inputs..
{"flavor": "computeovsdpdk", "huge_page_allocation_percentage": "50", "num_phy_cores_per_numa_node_for_pmd": "1"}
Collects the deployed value for parameters from node: 172.18.0.31
Collects the hiera value for parameters from node: 172.18.0.31
DPDK NIC's and NUMA node mapping:
NIC "p1p2": NUMA node 1, Physical CPU's: [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35, 37, 39, 41, 43]
NIC "p1p1": NUMA node 1, Physical CPU's: [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35, 37, 39, 41, 43]

+---------------------------+-----------------------------------------------------------------+-------------------+-----------------------------------------------------------------------------------------+
| Parameters                | Deployment Value                                                | Hiera Data        | Validation Messages                                                                     |
+---------------------------+-----------------------------------------------------------------+-------------------+-----------------------------------------------------------------------------------------+
| HostCpusList              | '0,1,2,3,4,5,6,7,8,9'                                           | NA                | expected: 0,1,44,45.                                                                    |
|                           |                                                                 |                   |                                                                                         |
| NeutronDpdkCoreList       | '10,11,22,23'                                                   | '10,11,22,23'     | Missing thread siblings for thread: 10 in PMD cores, thread siblings: [10, 54].         |
|                           |                                                                 |                   | Missing thread siblings for thread: 11 in PMD cores, thread siblings: [11, 55].         |
|                           |                                                                 |                   | Missing thread siblings for thread: 22 in PMD cores, thread siblings: [22, 66].         |
|                           |                                                                 |                   | Missing thread siblings for thread: 23 in PMD cores, thread siblings: [23, 67].         |
|                           |                                                                 |                   | Number of physical cores for DPDK NIC NUMA node(1) is less than recommended cores '1'.  |
|                           |                                                                 |                   |                                                                                         |
| NeutronDpdkSocketMemory   | '2048,2048'                                                     | '2048,2048'       | valid.                                                                                  |
|                           |                                                                 |                   |                                                                                         |
| NovaReservedHostMemory    | 4096                                                            | 4096              | valid.                                                                                  |
|                           |                                                                 |                   |                                                                                         |
| NovaVcpuPinSet            | '12-21,24-87'                                                   | ["12-21","24-87"] | Missing thread siblings for thread: 44 in nova cpus, thread siblings: [0, 44].          |
|                           |                                                                 |                   | Missing thread siblings for thread: 45 in nova cpus, thread siblings: [1, 45].          |
|                           |                                                                 |                   | Missing thread siblings for thread: 46 in nova cpus, thread siblings: [2, 46].          |
|                           |                                                                 |                   | Missing thread siblings for thread: 47 in nova cpus, thread siblings: [3, 47].          |
|                           |                                                                 |                   | Missing thread siblings for thread: 48 in nova cpus, thread siblings: [4, 48].          |
|                           |                                                                 |                   | Missing thread siblings for thread: 49 in nova cpus, thread siblings: [5, 49].          |
|                           |                                                                 |                   | Missing thread siblings for thread: 50 in nova cpus, thread siblings: [6, 50].          |
|                           |                                                                 |                   | Missing thread siblings for thread: 51 in nova cpus, thread siblings: [7, 51].          |
|                           |                                                                 |                   | Missing thread siblings for thread: 52 in nova cpus, thread siblings: [8, 52].          |
|                           |                                                                 |                   | Missing thread siblings for thread: 53 in nova cpus, thread siblings: [9, 53].          |
|                           |                                                                 |                   | Missing thread siblings for thread: 54 in nova cpus, thread siblings: [10, 54].         |
|                           |                                                                 |                   | Missing thread siblings for thread: 55 in nova cpus, thread siblings: [11, 55].         |
|                           |                                                                 |                   | Missing thread siblings for thread: 66 in nova cpus, thread siblings: [22, 66].         |
|                           |                                                                 |                   | Missing thread siblings for thread: 67 in nova cpus, thread siblings: [23, 67].         |
|                           |                                                                 |                   | Duplicated physical cores in host CPU's: [44, 45].                                      |
|                           |                                                                 |                   |                                                                                         |
| HostIsolatedCoreList      | '10-87'                                                         | NA                | Missing thread siblings for thread: 44 in host isolated cpus, thread siblings: [0, 44]. |
|                           |                                                                 |                   | Missing thread siblings for thread: 45 in host isolated cpus, thread siblings: [1, 45]. |
|                           |                                                                 |                   | Missing thread siblings for thread: 46 in host isolated cpus, thread siblings: [2, 46]. |
|                           |                                                                 |                   | Missing thread siblings for thread: 47 in host isolated cpus, thread siblings: [3, 47]. |
|                           |                                                                 |                   | Missing thread siblings for thread: 48 in host isolated cpus, thread siblings: [4, 48]. |
|                           |                                                                 |                   | Missing thread siblings for thread: 49 in host isolated cpus, thread siblings: [5, 49]. |
|                           |                                                                 |                   | Missing thread siblings for thread: 50 in host isolated cpus, thread siblings: [6, 50]. |
|                           |                                                                 |                   | Missing thread siblings for thread: 51 in host isolated cpus, thread siblings: [7, 51]. |
|                           |                                                                 |                   | Missing thread siblings for thread: 52 in host isolated cpus, thread siblings: [8, 52]. |
|                           |                                                                 |                   | Missing thread siblings for thread: 53 in host isolated cpus, thread siblings: [9, 53]. |
|                           |                                                                 |                   | Duplicated in host CPU's: [44, 45].                                                     |
|                           |                                                                 |                   |                                                                                         |
| ComputeKernelArgs         | default_hugepagesz=1GB hugepages=1G hugepages=64 intel_iommu=on | NA                | expected: default_hugepagesz=1GB hugepages=1G hugepages=126 intel_iommu=on iommu=pt     |
|                           |                                                                 |                   |                                                                                         |
| NeutronDpdkMemoryChannels | "4"                                                             | 4                 | Recommended value is "4" but it should be configured based on hardware spec.            |
| tuned                     | cpu-partitioning                                                | NA                | enabled.                                                                                |
|                           |                                                                 |                   |                                                                                         |
+---------------------------+-----------------------------------------------------------------+-------------------+-----------------------------------------------------------------------------------------+
```

## Note

This python scripts can also be used to validate the parameters automatically
when any role uses both DPDK and SRIOV features.
