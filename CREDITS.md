# Credits

OpenCore Launcher stands on the work of the Hackintosh community. **This repository contains no third-party
code or binaries.** Every component below is downloaded at run time from its original source and keeps its
own licence. Some projects have no licence file; for those we never redistribute files, we only link to or
download from the author's repository.

## Downloaded and used at run time

| Project | Author | Licence | Used for |
|---------|--------|---------|----------|
| [OpenCorePkg](https://github.com/acidanthera/OpenCorePkg) | acidanthera | BSD-3-Clause | The bootloader, `Sample.plist`, `ocvalidate`, `macserial`, `macrecovery.py`, ACPI samples |
| [Lilu](https://github.com/acidanthera/Lilu), [VirtualSMC](https://github.com/acidanthera/VirtualSMC), [WhateverGreen](https://github.com/acidanthera/WhateverGreen), [AppleALC](https://github.com/acidanthera/AppleALC), [RestrictEvents](https://github.com/acidanthera/RestrictEvents), [NVMeFix](https://github.com/acidanthera/NVMeFix), [CryptexFixup](https://github.com/acidanthera/CryptexFixup), [IntelMausi](https://github.com/acidanthera/IntelMausi), [AirportBrcmFixup](https://github.com/acidanthera/AirportBrcmFixup), [BrcmPatchRAM](https://github.com/acidanthera/BrcmPatchRAM), [VoodooPS2](https://github.com/acidanthera/VoodooPS2), [BrightnessKeys](https://github.com/acidanthera/BrightnessKeys) | acidanthera and contributors | see each repository (mostly BSD-3-Clause) | macOS drivers |
| [ECEnabler](https://github.com/averycblack/ECEnabler) | averycblack | see repository | Battery status on laptops |
| [VoodooI2C](https://github.com/VoodooI2C/VoodooI2C) | VoodooI2C team | see repository | I2C touchpads |
| [CpuTopologyRebuild](https://github.com/b00t0x/CpuTopologyRebuild) | b00t0x | see repository | Alder/Raptor Lake hybrid cores |
| [NootedRed](https://github.com/ChefKissInc/NootedRed) | ChefKissInc | see repository | Experimental AMD APU graphics |
| [OcBinaryData](https://github.com/acidanthera/OcBinaryData) | acidanthera | no licence file | `apfs_aligned.efi` for macOS Tahoe |
| [itlwm / AirportItlwm](https://github.com/OpenIntelWireless/itlwm), [IntelBluetoothFirmware](https://github.com/OpenIntelWireless/IntelBluetoothFirmware) | OpenIntelWireless | GPL-2.0 | Intel Wi-Fi and Bluetooth |
| [RealtekRTL8111](https://github.com/Mieze/RTL8111_driver_for_OS_X), [LucyRTL8125Ethernet](https://github.com/Mieze/LucyRTL8125Ethernet), [AtherosE2200Ethernet](https://github.com/Mieze/AtherosE2200Ethernet) | Mieze | see each repository | Ethernet |
| [AppleIGC](https://github.com/SongXiaoXi/AppleIGC) | SongXiaoXi | see repository | Intel I225/I226 Ethernet |
| [AMD_Vanilla](https://github.com/AMD-OSX/AMD_Vanilla) | AMD-OSX | no licence file | Kernel patches for AMD CPUs |
| [SimpleHDA](https://github.com/laobamac/SimpleHDA) | laobamac | no licence file | Optional, experimental analog audio on macOS Tahoe |
| [AppleMCEReporterDisabler](https://github.com/acidanthera/bugtracker/files/3703498/AppleMCEReporterDisabler.kext.zip) | acidanthera bugtracker | — | Prevents AppleIntelMCEReporter panics (AMD CPUs, VMs) |
| [Dortania prebuilt SSDTs](https://github.com/dortania/Getting-Started-With-ACPI) | Dortania | no licence file | ACPI tables when OpenCore's samples don't cover the case |
| [Dortania build-repo](https://github.com/dortania/build-repo) | Dortania | — | Fallback download index for kexts |
| macOS Recovery | Apple | Apple's licence | Downloaded directly from Apple's servers by `macrecovery.py`, never stored here |

## Knowledge sources

The rules in `oclauncher/planner.py` and `oclauncher/data/` were written from these sources. Each hardware
verdict in the app links to the page it comes from.

- [Dortania OpenCore Install Guide](https://dortania.github.io/OpenCore-Install-Guide/), [GPU Buyers Guide](https://dortania.github.io/GPU-Buyers-Guide/), [Wireless Buyers Guide](https://dortania.github.io/Wireless-Buyers-Guide/), [Anti-Hackintosh Buyers Guide](https://dortania.github.io/Anti-Hackintosh-Buyers-Guide/) and the macOS Tahoe notes. Dortania team and contributors.
- `oclauncher/data/kext_index.json` holds the PCI/USB IDs that the kexts listed above declare in their own `Info.plist` files. They are extracted by `tools/update_kext_index.py`.
- [OSX-KVM](https://github.com/kholia/OSX-KVM) by Dhiru Kholia and contributors. The QEMU CPU and device settings in `tools/qemu-test.sh` (Penryn identity, `vendor=GenuineIntel`, `vmware-cpuid-freq`, `kvm ignore_msrs`) follow its approach. No files were copied.
- [OpCore-Simplify](https://github.com/lzhoang2801/OpCore-Simplify) by lzhoang2801 (BSD-3-Clause). We cross-checked our quirk and kext rules against it, which is how we caught DummyPowerManagement for AMD, `apfs_aligned.efi` and SimpleHDA for Tahoe, and `revpatch=sbvmm`. No code was copied.
- [SSDTTime](https://github.com/corpnewt/SSDTTime) by CorpNewt, a reference for the ACPI work planned next.

## Live ISO

`iso/build-iso.sh` builds the live system locally from [Debian](https://www.debian.org/) packages. The ISO is
not distributed by this project.

## Trademarks

Apple, Mac, macOS and the names of Mac models are trademarks of Apple Inc. This project is not affiliated
with or endorsed by Apple, Dortania or acidanthera.
