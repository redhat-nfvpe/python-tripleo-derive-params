# OSP10 Post Deployment SRIOV Parameters Validation

Python scripts 'validate-dpdk-params.py' is used to debug and validate the
SRIOV parameters applied on the environment with the auto generated SRIOV
parameters value based on the baremetal node using the user inputs flavor and
huge_page_allocation_percentage in post deployment.

Derived and deployed values are compared for each SRIOV parameters and displays
the differences and helps to identify the parameters related issues.

We can validate SRIOV parameters for any role which uses SRIOV feature, but
need to run validation python scripts for each role separately with associated
flavor and other inputs.

The following is the list of parameters can be validated for the SRIOV feature
on the deployed environment based on introspection hardware data of first
baremetal node which is matching the provided flavor.

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
  OvercloudComputeSriovFlavor: computesriov
  ```
* Capture the list of DPDK NIC's (NIC numbering like nic1, nic2 ...) with MTU
  based on hardware spec.

## Parameters Default Value
* NovaReservedHostMemory parameter is 4096.

Based on the environment, operator can update the default value to validate.

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
$ python validate_sriov_params.py user_inputs.json
user_inputs.json format:
{
"flavor": "flavor name",
"huge_page_allocation_percentage": 50
}
```

## Example

```
$ python validate_sriov_params.py '{"flavor": "compute", "huge_page_allocation_percentage": 50}'
Validating user inputs..
{"flavor": "compute", "huge_page_allocation_percentage": 50}
Deriving SRIOV parameters based on flavor: compute
Collects the parameters from node: 172.18.0.24

Camparison result between derived and deployed DPDK parameters values.

Differences:
ComputeKernelArgs - derived: {'default_hugepagesz': '1GB', 'intel_iommu': 'on', 'hugepages': '126', 'hugepagesz': '1G'}, deployed: {'default_hugepagesz': '1GB', 'hugepagesz': '1G', 'hugepages': '64', 'intel_iommu': 'on'}
NovaVcpuPinSet - derived: ['2-43','46-87'], deployed: '12-21,24-87'
HostIsolatedCoreList - derived: '2-43,46-87', deployed: '10-87'

No differences:
NovaReservedHostMemory - derived: 4096, deployed: 4096

```
