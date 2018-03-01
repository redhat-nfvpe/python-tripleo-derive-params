# OSP10 SRIOV Derive Parameters

Python scripts ‘sriov_derive_params.py’ is used to auto generate the
SRIOV parameters based on the baremetal node using the user inputs flavor and
huge_page_allocation_percentage.

We can derive SRIOV parameters for any role which uses SRIOV feature, but
need to run derive params python scripts for each role separately with
associated flavor and huge_page_allocation_percentage.

The following is the list of parameters can be derived automatically for
SRIOV feature based on introspection hardware data of first baremetal node
which is matching the provided flavor.

```
NovaReservedHostMemory
NovaVcpuPinSet
HostIsolatedCoreList
ComputeKernelArgs
```

Once SRIOV parameters are derived, copy the auto generated parameters manually
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
* Capture the list of roles with associated flavor name which are using SRIOV
  feature to derive the SRIOV parameters.
  Find flavor name referring the property Overcloud[RoleName]Flavor value in
  network-environment.yaml file for any role.
  ```
  OvercloudControlFlavor: control
  OvercloudComputeSriovFlavor: computesriov
  ```

## Parameters Default Value
* NovaReservedHostMemory parameter is 4096.

Based on the environment, operator can update the default value when copying.

## User Inputs

#### flavor:
This input parameter specifies the flavor name associated to the role to
identify the first baremetal node and SRIOV parameters are derived based on
that node hardware data.

#### huge_page_allocation_percentage:
This input parameter specifies the required percentage of total memory
(excluding NovaReservedHostMemory) that can be configured as huge pages.
The KernelArgs parameter is derived using the calculated huge pages based
on the huge_page_allocation_percentage specified. This parameter should be
set to 50.

## Usage

```
$ python sriov_derive_params.py user_inputs.json
user_inputs.json format:
{
"flavor": "flavor name",
"huge_page_allocation_percentage": 50
}
```

## Example

```
$  python sriov_derive_params.py '{"flavor": "compute", "huge_page_allocation_percentage": 50}'
Validating user inputs..
{"flavor": "compute", "huge_page_allocation_percentage": 50}
Deriving SRIOV parameters based on flavor: compute
ComputeKernelArgs: "default_hugepagesz=1GB hugepagesz=1G hugepages=126 intel_iommu=on"
NovaVcpuPinSet: ['2-43','46-87']
NovaReservedHostMemory: 4096
HostIsolatedCoreList: "2-43,46-87"

# Overrides role-specific parameters using hiera variables
# Optional this section, copy if any parameters are needed to override for this role
# Copy required parameters to the <RoleName>ExtraConfig section
nova::compute::vcpu_pin_set: ['2-32','34-39','41-43','46-76','78-83','85-87']
nova::compute::reserved_host_memory: 4096

```
