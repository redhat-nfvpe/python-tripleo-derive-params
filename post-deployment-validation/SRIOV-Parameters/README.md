# OSP10 Post Deployment SRIOV Parameters Validation

Python scripts 'validate-sriov-params.py' is used to debug and validate the
SRIOV parameters applied on the environment automatically based on user inputs
flavor and huge_page_allocation_percentage in post deployment.

Deployed value is validated for each SRIOV parameters and displays
the differences and helps to identify the parameters related issues.

We can validate SRIOV parameters for any role which uses SRIOV feature, but
need to run validation python scripts for each role separately with associated
flavor and huge_page_allocation_percentage inputs.

The following is the list of parameters can be validated for the SRIOV feature
on the deployed environment based on first baremetal node which is matching
the provided flavor.

```
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
* Capture the list of roles with associated node uuid which are using SRIOV
  feature to validate the SRIOV parameters.
  Find flavor name referring the property Overcloud[RoleName]Flavor value in
  network-environment.yaml file for any role.
  ```
  OvercloudControlFlavor: control
  OvercloudComputeSriovFlavor: computesriov
  ```

## Parameters Recommended Value
* NovaReservedHostMemory parameter is 4096.

Based on the environment, operator can update the recommended value to validate.

## User Inputs

#### flavor:                                                                    
This input parameter specifies the flavor name associated to the role to        
identify the first baremetal node and helps to derive and gets deployed
SRIOV parameters.

#### huge_page_allocation_percentage:
This input parameter specifies the required percentage of total memory
(excluding NovaReservedHostMemory) that can be configured as huge pages.
The KernelArgs parameter is derived using the calculated huge pages based
on the huge_page_allocation_percentage specified. This parameter should be
set to 50.

## Usage

```
$ python validate_sriov_params.py user_inputs.json --flavor "flavor name" --huge_page_allocation_percentage 50
```

## Example

```
$ python validate_sriov_params.py --flavor "computeovsdpdk" --huge_page_allocation_percentage 50
Validating user inputs..
{"flavor": "computesriov", "huge_page_allocation_percentage": "50"}
Collects the deployed value for parameters from node: 172.18.0.31
Collects the hiera value for parameters from node: 172.18.0.31
+------------------------+-----------------------------------------------------------------+-------------------+-----------------------------------------------------------------------------------------+
| Parameters             | Deployment Value                                                | Hiera Data        | Validation Messages                                                                     |
+------------------------+-----------------------------------------------------------------+-------------------+-----------------------------------------------------------------------------------------+
| NovaReservedHostMemory | 4096                                                            | 4096              | valid.                                                                                  |
|                        |                                                                 |                   |                                                                                         |
| NovaVcpuPinSet         | '12-21,24-87'                                                   | ["12-21","24-87"] | Missing thread siblings for thread: 44 in nova cpus, thread siblings: [0, 44].          |
|                        |                                                                 |                   | Missing thread siblings for thread: 45 in nova cpus, thread siblings: [1, 45].          |
|                        |                                                                 |                   | Missing thread siblings for thread: 46 in nova cpus, thread siblings: [2, 46].          |
|                        |                                                                 |                   | Missing thread siblings for thread: 47 in nova cpus, thread siblings: [3, 47].          |
|                        |                                                                 |                   | Missing thread siblings for thread: 48 in nova cpus, thread siblings: [4, 48].          |
|                        |                                                                 |                   | Missing thread siblings for thread: 49 in nova cpus, thread siblings: [5, 49].          |
|                        |                                                                 |                   | Missing thread siblings for thread: 50 in nova cpus, thread siblings: [6, 50].          |
|                        |                                                                 |                   | Missing thread siblings for thread: 51 in nova cpus, thread siblings: [7, 51].          |
|                        |                                                                 |                   | Missing thread siblings for thread: 52 in nova cpus, thread siblings: [8, 52].          |
|                        |                                                                 |                   | Missing thread siblings for thread: 53 in nova cpus, thread siblings: [9, 53].          |
|                        |                                                                 |                   | Missing thread siblings for thread: 54 in nova cpus, thread siblings: [10, 54].         |
|                        |                                                                 |                   | Missing thread siblings for thread: 55 in nova cpus, thread siblings: [11, 55].         |
|                        |                                                                 |                   | Missing thread siblings for thread: 66 in nova cpus, thread siblings: [22, 66].         |
|                        |                                                                 |                   | Missing thread siblings for thread: 67 in nova cpus, thread siblings: [23, 67].         |
|                        |                                                                 |                   | Duplicated physical cores in host CPU's: [44, 45].                                      |
|                        |                                                                 |                   |                                                                                         |
| HostIsolatedCoreList   | '10-87'                                                         | NA                | Missing thread siblings for thread: 44 in host isolated cpus, thread siblings: [0, 44]. |
|                        |                                                                 |                   | Missing thread siblings for thread: 45 in host isolated cpus, thread siblings: [1, 45]. |
|                        |                                                                 |                   | Missing thread siblings for thread: 46 in host isolated cpus, thread siblings: [2, 46]. |
|                        |                                                                 |                   | Missing thread siblings for thread: 47 in host isolated cpus, thread siblings: [3, 47]. |
|                        |                                                                 |                   | Missing thread siblings for thread: 48 in host isolated cpus, thread siblings: [4, 48]. |
|                        |                                                                 |                   | Missing thread siblings for thread: 49 in host isolated cpus, thread siblings: [5, 49]. |
|                        |                                                                 |                   | Missing thread siblings for thread: 50 in host isolated cpus, thread siblings: [6, 50]. |
|                        |                                                                 |                   | Missing thread siblings for thread: 51 in host isolated cpus, thread siblings: [7, 51]. |
|                        |                                                                 |                   | Missing thread siblings for thread: 52 in host isolated cpus, thread siblings: [8, 52]. |
|                        |                                                                 |                   | Missing thread siblings for thread: 53 in host isolated cpus, thread siblings: [9, 53]. |
|                        |                                                                 |                   | Duplicated in host CPU's: [44, 45].                                                     |
|                        |                                                                 |                   |                                                                                         |
| ComputeKernelArgs      | default_hugepagesz=1GB hugepages=1G hugepages=64 intel_iommu=on | NA                | expected: default_hugepagesz=1GB hugepages=1G hugepages=126 intel_iommu=on iommu=pt     |
|                        |                                                                 |                   |                                                                                         |
+------------------------+-----------------------------------------------------------------+-------------------+-----------------------------------------------------------------------------------------+
```
