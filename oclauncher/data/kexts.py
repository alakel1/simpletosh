"""Catalog of kexts the launcher knows how to download.

Each entry: where to get it and which .kext bundles to take from the archive.
`github` = owner/repo + regex for the release asset. `dortania` = key in
dortania/build-repo config.json (used as fallback). `url` = fixed URL.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class KextSource:
    name: str                       # logical name used by the planner
    bundles: list                   # .kext bundle names to copy (top-level in EFI/OC/Kexts)
    purpose: str                    # human description
    github: Optional[str] = None
    asset: str = r"RELEASE\.zip$"
    dortania: Optional[str] = None
    url: Optional[str] = None
    package: Optional[str] = None   # several logical kexts can share one download
    manual: bool = False            # cannot be downloaded automatically


_C = [
    KextSource("Lilu", ["Lilu.kext"], "Patching engine required by almost every other kext",
               github="acidanthera/Lilu", dortania="Lilu"),
    KextSource("VirtualSMC", ["VirtualSMC.kext"], "Emulates the Apple SMC chip (mandatory)",
               github="acidanthera/VirtualSMC", dortania="VirtualSMC", package="VirtualSMC"),
    KextSource("SMCProcessor", ["SMCProcessor.kext"], "Intel CPU temperature sensors",
               github="acidanthera/VirtualSMC", dortania="VirtualSMC", package="VirtualSMC"),
    KextSource("SMCSuperIO", ["SMCSuperIO.kext"], "Fan speed sensors on desktop motherboards",
               github="acidanthera/VirtualSMC", dortania="VirtualSMC", package="VirtualSMC"),
    KextSource("SMCBatteryManager", ["SMCBatteryManager.kext"], "Laptop battery status",
               github="acidanthera/VirtualSMC", dortania="VirtualSMC", package="VirtualSMC"),
    KextSource("WhateverGreen", ["WhateverGreen.kext"], "Graphics fixes for Intel, AMD and NVIDIA GPUs",
               github="acidanthera/WhateverGreen", dortania="WhateverGreen"),
    KextSource("AppleALC", ["AppleALC.kext"], "Enables onboard audio (Realtek and others)",
               github="acidanthera/AppleALC", dortania="AppleALC"),
    KextSource("RestrictEvents", ["RestrictEvents.kext"], "Hides warnings and fixes quirks of the chosen Mac model",
               github="acidanthera/RestrictEvents", dortania="RestrictEvents"),
    KextSource("NVMeFix", ["NVMeFix.kext"], "Better power management for non-Apple NVMe SSDs",
               github="acidanthera/NVMeFix", dortania="NVMeFix"),
    KextSource("CryptexFixup", ["CryptexFixup.kext"], "Lets Ventura+ install on CPUs without AVX2",
               github="acidanthera/CryptexFixup", dortania="CryptexFixup"),
    KextSource("CpuTopologyRebuild", ["CpuTopologyRebuild.kext"], "Correct core topology on Alder/Raptor Lake",
               github="b00t0x/CpuTopologyRebuild"),
    # Ethernet
    KextSource("IntelMausi", ["IntelMausi.kext"], "Intel I217/I218/I219 Ethernet driver",
               github="acidanthera/IntelMausi", dortania="IntelMausi"),
    KextSource("RealtekRTL8111", ["RealtekRTL8111.kext"], "Realtek RTL8111/8168 Ethernet driver",
               github="Mieze/RTL8111_driver_for_OS_X", asset=r"\.zip$", dortania="RealtekRTL8111"),
    KextSource("LucyRTL8125Ethernet", ["LucyRTL8125Ethernet.kext"], "Realtek RTL8125 2.5GbE driver",
               github="Mieze/LucyRTL8125Ethernet", asset=r"\.zip$"),
    KextSource("AtherosE2200Ethernet", ["AtherosE2200Ethernet.kext"], "Atheros / Killer Ethernet driver",
               github="Mieze/AtherosE2200Ethernet", asset=r"\.zip$"),
    KextSource("AppleIGC", ["AppleIGC.kext"], "Intel I225/I226 2.5GbE driver",
               github="SongXiaoXi/AppleIGC", asset=r"\.zip$"),
    KextSource("RealtekRTL8100", ["RealtekRTL8100.kext"], "Realtek RTL8100 Fast Ethernet driver", manual=True),
    KextSource("AppleIGB", ["AppleIGB.kext"], "Intel I211 Ethernet driver", manual=True),
    # Wireless
    KextSource("itlwm", ["itlwm.kext"], "Intel Wi-Fi driver (use with the HeliPort app)",
               github="OpenIntelWireless/itlwm", asset=r"^itlwm_.*\.zip$"),
    KextSource("AirportItlwm", ["AirportItlwm.kext"], "Intel Wi-Fi shown as native AirPort (works in the installer)",
               github="OpenIntelWireless/itlwm", asset=r"^AirportItlwm_.*_{codename}\.kext\.zip$"),
    KextSource("AirportBrcmFixup", ["AirportBrcmFixup.kext"], "Fixes for Broadcom Wi-Fi cards",
               github="acidanthera/AirportBrcmFixup", dortania="AirportBrcmFixup"),
    KextSource("IntelBluetoothFirmware", ["IntelBluetoothFirmware.kext"], "Intel Bluetooth firmware loader",
               github="OpenIntelWireless/IntelBluetoothFirmware", asset=r"\.zip$", package="IntelBluetooth"),
    KextSource("IntelBTPatcher", ["IntelBTPatcher.kext"], "Intel Bluetooth fixes",
               github="OpenIntelWireless/IntelBluetoothFirmware", asset=r"\.zip$", package="IntelBluetooth"),
    KextSource("BrcmPatchRAM3", ["BrcmPatchRAM3.kext"], "Broadcom Bluetooth firmware uploader",
               github="acidanthera/BrcmPatchRAM", dortania="BrcmPatchRAM", package="BrcmPatchRAM"),
    KextSource("BrcmFirmwareData", ["BrcmFirmwareData.kext"], "Broadcom Bluetooth firmware files",
               github="acidanthera/BrcmPatchRAM", dortania="BrcmPatchRAM", package="BrcmPatchRAM"),
    KextSource("BlueToolFixup", ["BlueToolFixup.kext"], "Bluetooth fix for Monterey and newer",
               github="acidanthera/BrcmPatchRAM", dortania="BrcmPatchRAM", package="BrcmPatchRAM"),
    # Laptop
    KextSource("VoodooPS2Controller", ["VoodooPS2Controller.kext"], "PS/2 keyboard and touchpad",
               github="acidanthera/VoodooPS2", dortania="VoodooPS2"),
    KextSource("VoodooI2C", ["VoodooI2C.kext", "VoodooI2CHID.kext"], "I2C touchpads (precision touchpads)",
               github="VoodooI2C/VoodooI2C", dortania="VoodooI2C"),
    KextSource("ECEnabler", ["ECEnabler.kext"], "Lets macOS read the laptop battery",
               github="averycblack/ECEnabler", dortania="ECEnabler"),
    KextSource("BrightnessKeys", ["BrightnessKeys.kext"], "Makes brightness keys work on laptops",
               github="acidanthera/BrightnessKeys", dortania="BrightnessKeys"),
    # Audio on macOS Tahoe (AppleHDA was removed): third-party, experimental, opt-in only
    KextSource("SimpleHDA", ["SimpleHDA.kext"], "Restores Apple's audio driver on macOS Tahoe via OpenCore",
               github="laobamac/SimpleHDA", asset=r"RELEASE\.zip$"),
    # AMD
    KextSource("NootedRed", ["NootedRed.kext"], "Experimental driver for AMD Radeon integrated graphics",
               github="ChefKissInc/NootedRed"),
    KextSource("AppleMCEReporterDisabler", ["AppleMCEReporterDisabler.kext"],
               "Prevents kernel panics in AppleIntelMCEReporter (AMD CPUs, VMs)",
               url="https://github.com/acidanthera/bugtracker/files/3703498/AppleMCEReporterDisabler.kext.zip"),
]

CATALOG = {k.name: k for k in _C}

# Load order hints (dependencies first). Anything not listed goes after these.
ORDER = ["Lilu", "VirtualSMC", "WhateverGreen", "AppleALC", "RestrictEvents"]
