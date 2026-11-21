"""Hardware knowledge base: maps CPU models and PCI/USB IDs to macOS support.

Two kinds of knowledge live here:

* Network / Bluetooth drivers: decided by kextdb, i.e. by the PCI/USB IDs that the
  kexts declare in their own Info.plist (authoritative, refreshed automatically).
* CPU and GPU support per macOS version: hand-maintained tables, because those
  drivers are built into macOS itself. Every table cites its source (SRC_*);
  each Support carries `source` so the UI can show where a claim comes from.

Version strings: "10.13".."26". None = unsupported.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from .macos import vkey

SRC_CPU = "https://dortania.github.io/OpenCore-Install-Guide/macos-limits.html"
SRC_INTEL_GPU = "https://dortania.github.io/GPU-Buyers-Guide/modern-gpus/intel-gpu.html"
SRC_AMD_GPU = "https://dortania.github.io/GPU-Buyers-Guide/modern-gpus/amd-gpu.html"
SRC_NVIDIA_GPU = "https://dortania.github.io/GPU-Buyers-Guide/modern-gpus/nvidia-gpu.html"
SRC_WIRELESS = "https://dortania.github.io/Wireless-Buyers-Guide/"
SRC_TAHOE = "https://dortania.github.io/OpenCore-Install-Guide/extras/tahoe.html"
SRC_KEXT = "the driver's own Info.plist (IOKitPersonalities)"


@dataclass
class Support:
    """How well a single component works under macOS."""
    status: str                      # "native" | "kext" | "experimental" | "unsupported" | "ignored"
    summary: str                     # one-line human description
    min_macos: Optional[str] = None
    max_macos: Optional[str] = None
    kexts: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    info: dict = field(default_factory=dict)   # extra facts for the planner
    source: str = ""                           # where this claim comes from

    @property
    def usable(self):
        return self.status in ("native", "kext", "experimental")


# --------------------------------------------------------------------------- CPU

INTEL_MODELS = {
    0x2A: "sandybridge", 0x2D: "sandybridge-e",
    0x3A: "ivybridge", 0x3E: "ivybridge-e",
    0x3C: "haswell", 0x45: "haswell", 0x46: "haswell", 0x3F: "haswell-e",
    0x3D: "broadwell", 0x47: "broadwell", 0x4F: "broadwell-e", 0x56: "broadwell-e",
    0x4E: "skylake", 0x5E: "skylake", 0x55: "skylake-x",
    0x66: "cannonlake",
    0x7D: "icelake", 0x7E: "icelake",
    0x8C: "tigerlake", 0x8D: "tigerlake",
    0xA5: "cometlake", 0xA6: "cometlake",
    0xA7: "rocketlake",
    0x97: "alderlake", 0x9A: "alderlake",
    0xB7: "raptorlake", 0xBA: "raptorlake", 0xBF: "raptorlake",
    0xAA: "meteorlake", 0xAC: "meteorlake",
    0xC5: "arrowlake", 0xC6: "arrowlake", 0xB5: "arrowlake",
    0xBD: "lunarlake",
}

INTEL_PRETTY = {
    "ivybridge": "Ivy Bridge (3rd gen)", "haswell": "Haswell (4th gen)",
    "broadwell": "Broadwell (5th gen)", "skylake": "Skylake (6th gen)",
    "kabylake": "Kaby Lake (7th gen)", "kabylake-r": "Kaby Lake R (8th gen U)",
    "amberlake": "Amber Lake (8th gen Y)", "coffeelake": "Coffee Lake (8th/9th gen)",
    "whiskeylake": "Whiskey Lake (8th gen U)", "cometlake": "Comet Lake (10th gen)",
    "icelake": "Ice Lake (10th gen mobile)", "rocketlake": "Rocket Lake (11th gen)",
    "alderlake": "Alder Lake (12th gen)", "raptorlake": "Raptor Lake (13th/14th gen)",
    "tigerlake": "Tiger Lake (11th gen mobile)", "sandybridge": "Sandy Bridge (2nd gen)",
}

# Platforms in order of age, used for "X and newer" rules.
INTEL_ORDER = ["sandybridge", "ivybridge", "haswell", "broadwell", "skylake", "kabylake",
               "kabylake-r", "amberlake", "coffeelake", "whiskeylake", "cometlake",
               "icelake", "rocketlake", "alderlake", "raptorlake"]


def intel_codename(model: int, stepping: int) -> str:
    if model == 0x9E:
        return "kabylake" if stepping <= 9 else "coffeelake"
    if model == 0x8E:
        return {9: "kabylake", 10: "kabylake-r", 11: "whiskeylake", 12: "cometlake"}.get(stepping, "kabylake")
    return INTEL_MODELS.get(model, "unknown")


def intel_at_least(codename: str, ref: str) -> bool:
    try:
        return INTEL_ORDER.index(codename) >= INTEL_ORDER.index(ref)
    except ValueError:
        return False


def classify_cpu(cpu: dict, laptop: bool) -> Support:
    s = _classify_cpu(cpu, laptop)
    s.source = s.source or SRC_CPU
    return s


def _classify_cpu(cpu: dict, laptop: bool) -> Support:
    vendor = cpu.get("vendor")
    flags = set(cpu.get("flags", []))
    if vendor == "intel":
        if cpu.get("family") != 6:
            return Support("unsupported", "Unknown Intel CPU family")
        code = intel_codename(cpu.get("model", 0), cpu.get("stepping", 0))
        pretty = INTEL_PRETTY.get(code, code)
        info = {"platform": code}
        if code in ("haswell", "broadwell", "skylake", "kabylake", "kabylake-r", "amberlake",
                    "coffeelake", "whiskeylake", "cometlake"):
            lo = "10.15" if code == "cometlake" else "10.13"
            s = Support("native", f"Intel {pretty}", lo, "26", info=info)
        elif code == "icelake":
            s = Support("native", f"Intel {pretty}", "10.15", "26", info=info) if laptop else \
                Support("unsupported", "Intel Ice Lake server/desktop parts are not supported")
        elif code == "ivybridge":
            s = Support("native", f"Intel {pretty}", "10.13", "12", info=info,
                        notes=["Ivy Bridge lacks AVX2: macOS Ventura and newer are not supported."])
        elif code in ("rocketlake", "alderlake", "raptorlake"):
            if laptop:
                return Support("unsupported", f"Intel {pretty} laptops have no macOS-compatible GPU")
            lo = "11" if code == "rocketlake" else "12"
            s = Support("experimental", f"Intel {pretty}", lo, "26", info=info,
                        notes=["CPU is spoofed as Comet Lake.",
                               "The integrated GPU is NOT supported: a compatible AMD graphics card is required."])
            if code != "rocketlake":
                s.notes.append("Efficiency cores work but scheduling is not optimal; "
                               "disabling E-cores in BIOS is an option.")
        elif code in ("sandybridge", "haswell-e", "broadwell-e", "skylake-x", "ivybridge-e", "sandybridge-e"):
            return Support("unsupported", f"Intel {pretty}: needs a manual (HEDT / legacy) setup, not covered yet")
        else:
            return Support("unsupported", f"Intel {pretty}: no macOS support")
        if "avx2" not in flags and s.max_macos and vkey(s.max_macos) > vkey("12"):
            s.max_macos = "12"
            s.notes.append("This CPU has no AVX2 (Pentium/Celeron?): Ventura and newer are not supported.")
        if "sse4_2" not in flags:
            return Support("unsupported", "CPU lacks SSE4.2")
        return s

    if vendor == "amd":
        fam = cpu.get("family", 0)
        if fam in (0x17, 0x19):
            gen = "Zen / Zen+ / Zen 2" if fam == 0x17 else "Zen 3 / Zen 4"
            s = Support("kext" if not laptop else "experimental", f"AMD Ryzen/Threadripper ({gen})",
                        "10.13", "26", info={"platform": "amd-zen", "family": fam},
                        notes=["Uses the AMD_Vanilla kernel patches.",
                               "Some apps need Intel-only instructions (e.g. Adobe apps, Docker, VMs with Hypervisor.framework)."])
            if laptop:
                s.notes.append("AMD laptops are hard: the Radeon iGPU only works with NootedRed, many things will not work.")
            return s
        if fam == 0x1A:
            return Support("experimental", "AMD Zen 5", "13", "26", info={"platform": "amd-zen", "family": fam},
                           notes=["Zen 5 support in AMD_Vanilla is recent; expect issues."])
        return Support("unsupported", "Old AMD CPU (Bulldozer/Jaguar family): not supported")

    return Support("unsupported", f"Unknown CPU vendor {vendor}")


# --------------------------------------------------------------------------- GPU

def _in(dev: int, *ranges):
    for r in ranges:
        if isinstance(r, tuple):
            if r[0] <= dev <= r[1]:
                return True
        elif dev == r:
            return True
    return False


# Intel iGPU device-id -> generation
INTEL_IGPU = {}
for _ids, _gen in [
    ((0x0152, 0x0156, 0x015A, 0x0162, 0x0166, 0x016A), "ivybridge"),
    ((0x0402, 0x0406, 0x040A, 0x0412, 0x0416, 0x041A, 0x041E, 0x0A06, 0x0A0E, 0x0A16, 0x0A1E,
      0x0A26, 0x0A2E, 0x0D22, 0x0D26, 0x0D2E), "haswell"),
    ((0x1606, 0x160E, 0x1612, 0x1616, 0x161E, 0x1622, 0x1626, 0x162B, 0x162D), "broadwell"),
    ((0x1902, 0x1906, 0x190B, 0x1912, 0x1916, 0x191B, 0x191D, 0x191E, 0x1921, 0x1923, 0x1926,
      0x1927, 0x192B, 0x193B, 0x193D), "skylake"),
    ((0x5902, 0x5906, 0x5912, 0x5916, 0x591B, 0x591C, 0x591D, 0x591E, 0x5921, 0x5923, 0x5926,
      0x5927), "kabylake"),
    ((0x5917,), "kabylake-r"),
    ((0x87C0, 0x87CA), "amberlake"),
    ((0x3E91, 0x3E92, 0x3E98, 0x3E9B, 0x3E94, 0x3E96, 0x3EA5, 0x3EA6, 0x3EA8), "coffeelake"),
    ((0x3EA0, 0x3EA1), "whiskeylake"),
    ((0x9BC5, 0x9BC8, 0x9BC4, 0x9BE6, 0x9BF6), "cometlake"),
    ((0x9B41, 0x9BCA, 0x9BCC), "cometlake-u"),
    ((0x8A51, 0x8A52, 0x8A53, 0x8A5A, 0x8A5C), "icelake"),
    ((0x3E90, 0x3E93, 0x9BA4, 0x9BA8, 0x9B21, 0x9BAA), "gt1"),
]:
    for _i in _ids:
        INTEL_IGPU[_i] = _gen

# Source: SRC_INTEL_GPU ("Highest/Initial Supported OS" per family). Dortania lists
# "Current/Sequoia (15)"; Tahoe (26) still ships the Coffee Lake / Ice Lake drivers
# because Apple supports iMac20,x and MacBookPro16,x (SRC_TAHOE "Supported SMBIOS").
IGPU_SUPPORT = {
    "ivybridge": ("10.13", "11", "Intel HD 2500/4000"),
    "haswell": ("10.13", "12", "Intel HD 4200-5200 (Haswell)"),
    "broadwell": ("10.13", "12", "Intel HD/Iris 5500-6200 (Broadwell)"),
    "skylake": ("10.13", "15", "Intel HD 510-580 (Skylake)"),
    "kabylake": ("10.13", "15", "Intel HD 610-650 (Kaby Lake)"),
    "kabylake-r": ("10.13", "15", "Intel UHD 620 (Kaby Lake R)"),
    "amberlake": ("10.14", "15", "Intel UHD 615/617 (Amber Lake)"),
    "coffeelake": ("10.13", "26", "Intel UHD/Iris 630-655 (Coffee Lake)"),
    "whiskeylake": ("10.14", "26", "Intel UHD 620 (Whiskey Lake)"),
    "cometlake": ("10.15", "26", "Intel UHD 630 (Comet Lake)"),
    "cometlake-u": ("10.15", "26", "Intel UHD 620 (Comet Lake U)"),
    "icelake": ("10.15", "26", "Intel Iris Plus G4/G7 (Ice Lake)"),
}
IGPU_NOTES = {
    "skylake": ["macOS Ventura removed the Skylake driver: on Ventura and newer the iGPU is presented as "
                "Kaby Lake (supported by Dortania, graphics glitches are possible)."],
}

# Source: SRC_AMD_GPU. (ranges, family, min, max, boot-args, summary, status)
AMD_DGPU = [
    (((0x67C0, 0x67FF),), "polaris", "10.13", "26", [], "AMD Radeon RX 460-590 (Polaris)", "native"),
    (((0x6980, 0x699F),), "lexa", "10.13", "26", [], "AMD Radeon RX 540/550 Lexa (Polaris 12)", "experimental"),
    (((0x6860, 0x687F),), "vega", "10.13", "26", [], "AMD Radeon RX Vega 56/64 (Vega 10)", "native"),
    (((0x66A0, 0x66AF),), "vega20", "10.14", "26", [], "AMD Radeon VII (Vega 20)", "native"),
    (((0x7310, 0x731F),), "navi10", "10.15", "26", ["agdpmod=pikera"], "AMD Radeon RX 5600/5700 (Navi 10)", "native"),
    (((0x7340, 0x734F),), "navi14", "10.15", "26", ["agdpmod=pikera"], "AMD Radeon RX 5500 (Navi 14)", "native"),
    (((0x73A0, 0x73BF),), "navi21", "11", "26", ["agdpmod=pikera"], "AMD Radeon RX 6800/6900 (Navi 21)", "native"),
    (((0x73E0, 0x73FF),), "navi23", "12", "26", ["agdpmod=pikera"], "AMD Radeon RX 6600 (Navi 23)", "native"),
    (((0x6780, 0x679F), (0x6800, 0x683F), (0x6640, 0x665F), (0x67A0, 0x67BF), (0x6920, 0x693F),
      (0x7300, 0x730F)), "gcn-legacy", "10.13", "12", [], "AMD Radeon HD 7000 / R7 / R9 (GCN 1-3)", "native"),
]
AMD_NOTES = {
    "lexa": ["Lexa cards must be spoofed as an RX 550 Baffin to work; results vary."],
    "navi10": ["Needs macOS Catalina 10.15.1 or newer."],
    "navi21": ["Needs macOS Big Sur 11.4 or newer."],
    "navi23": ["Needs macOS Monterey 12.1 or newer."],
    "vega20": ["Needs macOS Mojave 10.14.5 or newer."],
}
AMD_UNSUPPORTED = [
    (((0x73C0, 0x73DF),), "AMD Radeon RX 6700 (Navi 22): no native driver (NootRX is experimental)"),
    (((0x7420, 0x743F),), "AMD Radeon RX 6400/6500 (Navi 24): not supported"),
    (((0x7440, 0x749F),), "AMD Radeon RX 7000 (RDNA3): not supported"),
    (((0x7550, 0x75FF),), "AMD Radeon RX 9000 (RDNA4): not supported"),
    (((0x6FDF, 0x6FDF),), "AMD Radeon RX 580 2048SP: not supported"),
]
AMD_APU = (0x15DD, 0x15D8, 0x1636, 0x1638, 0x164C, 0x15E7, 0x1681, 0x1506, 0x15BF, 0x164E, 0x1900)

# Source: SRC_NVIDIA_GPU. Kepler drivers were removed in Monterey 12.0 beta 7.
NVIDIA = [
    (((0x0FC0, 0x0FFF), (0x1000, 0x103F), (0x1180, 0x11FF), (0x1280, 0x12BF)), "kepler", "10.13", "11",
     "NVIDIA Kepler (GT/GTX 6xx-7xx)", []),
    (((0x06C0, 0x06DF), (0x0DC0, 0x0DFF), (0x0E20, 0x0E3F), (0x1040, 0x107F), (0x1080, 0x109F),
      (0x1200, 0x121F), (0x1240, 0x125F)), "fermi", "10.13", "10.13", "NVIDIA Fermi (GT/GTX 4xx-5xx)", []),
    (((0x1340, 0x13FF), (0x1400, 0x143F), (0x17C0, 0x17FF), (0x1500, 0x15FF), (0x1B00, 0x1BFF),
      (0x1C00, 0x1CFF), (0x1D00, 0x1D3F)), "pascal-maxwell", "10.13", "10.13",
     "NVIDIA Maxwell/Pascal (GTX 9xx/10xx)",
     ["Works only on macOS High Sierra with the NVIDIA Web Drivers, installed after macOS.",
      "The installer runs without acceleration (nv_disable=1)."]),
]

VIRTUAL_GPUS = {("80ee", "beef"): "VirtualBox VGA", ("15ad", "0405"): "VMware SVGA",
                ("1234", "1111"): "QEMU standard VGA", ("1af4", "1050"): "virtio-gpu",
                ("1b36", "0100"): "QXL"}


def classify_gpu(vendor: str, device: str, slot: str = "") -> Support:
    dev = int(device, 16)
    if (vendor, device) in VIRTUAL_GPUS:
        return Support("native", VIRTUAL_GPUS[(vendor, device)] + " (no acceleration)", "10.13", "26",
                       info={"kind": "virtual"},
                       notes=["Virtual display: macOS will run without graphics acceleration."])
    if vendor == "8086":
        gen = INTEL_IGPU.get(dev)
        if gen is None:
            return Support("unsupported", f"Intel graphics 8086:{device} is not supported by macOS",
                           info={"kind": "igpu", "vendor": "intel"}, source=SRC_INTEL_GPU)
        if gen == "gt1":
            return Support("unsupported", "Intel UHD 610 (GT1) is not supported by macOS",
                           info={"kind": "igpu", "vendor": "intel", "gen": gen}, source=SRC_INTEL_GPU)
        lo, hi, name = IGPU_SUPPORT[gen]
        return Support("kext", name, lo, hi, kexts=["WhateverGreen"], notes=list(IGPU_NOTES.get(gen, [])),
                       info={"kind": "igpu", "vendor": "intel", "gen": gen, "device": dev}, source=SRC_INTEL_GPU)
    if vendor == "1002":
        if dev in AMD_APU:
            return Support("experimental", "AMD Radeon Vega/RDNA integrated graphics (APU)", "11", "14",
                           kexts=["NootedRed"], info={"kind": "igpu", "vendor": "amd", "gen": "apu"},
                           notes=["Needs NootedRed: experimental, may not work at all."],
                           source="https://github.com/ChefKissInc/NootedRed")
        for ranges, msg in AMD_UNSUPPORTED:
            if _in(dev, *ranges):
                return Support("unsupported", msg, info={"kind": "dgpu", "vendor": "amd"}, source=SRC_AMD_GPU)
        for ranges, fam, lo, hi, args, name, status in AMD_DGPU:
            if _in(dev, *ranges):
                return Support(status, name, lo, hi, kexts=["WhateverGreen"], notes=list(AMD_NOTES.get(fam, [])),
                               info={"kind": "dgpu", "vendor": "amd", "gen": fam, "boot_args": args},
                               source=SRC_AMD_GPU)
        return Support("unsupported", f"AMD graphics 1002:{device} (unknown model)",
                       info={"kind": "dgpu", "vendor": "amd"}, source=SRC_AMD_GPU)
    if vendor == "10de":
        for ranges, fam, lo, hi, name, notes in NVIDIA:
            if _in(dev, *ranges):
                status = "experimental" if fam == "pascal-maxwell" else "native"
                return Support(status, name, lo, hi, kexts=["WhateverGreen"], notes=list(notes),
                               info={"kind": "dgpu", "vendor": "nvidia", "gen": fam}, source=SRC_NVIDIA_GPU)
        return Support("unsupported", "NVIDIA RTX/GTX 16xx or newer: no macOS drivers exist",
                       info={"kind": "dgpu", "vendor": "nvidia", "gen": "modern"}, source=SRC_NVIDIA_GPU)
    return Support("unsupported", f"Unknown display controller {vendor}:{device}", info={"kind": "dgpu"})


# --------------------------------------------------------------------------- Ethernet
# Third-party drivers: decided by kextdb (IDs read from each kext's Info.plist).
# What remains here are drivers built into macOS, which have no kext to read.

ETH_KEXTS = {   # kext -> (summary, min macOS, max macOS)
    "IntelMausi": ("Intel I217/I218/I219 Ethernet", "10.13", "26"),
    "RealtekRTL8111": ("Realtek RTL8111/8168 Gigabit", "10.13", "26"),
    "LucyRTL8125Ethernet": ("Realtek RTL8125/8126 2.5/5GbE", "10.15", "26"),
    "AtherosE2200Ethernet": ("Qualcomm Atheros / Killer Ethernet", "10.13", "26"),
    "AppleIGC": ("Intel I225/I226 2.5GbE", "11", "26"),
}
NATIVE_NIC = {("8086", 0x1533): "Intel I210", ("8086", 0x100F): "Intel 82545EM (VirtualBox)",
              ("8086", 0x100E): "Intel 82540EM (VirtualBox)", ("8086", 0x10D3): "Intel 82574L",
              ("15ad", 0x07B0): "VMware vmxnet3"}
AQUANTIA = {0xD107, 0x07B1, 0x87B1, 0xD108, 0xD109, 0x94C0, 0x00B1}


def classify_ethernet(vendor: str, device: str) -> Support:
    from .. import kextdb
    dev = int(device, 16)
    for kext in kextdb.match_pci(vendor, device, "ethernet"):
        summary, lo, hi = ETH_KEXTS.get(kext, (f"Ethernet via {kext}", "10.13", "26"))
        return Support("kext", summary, lo, hi, kexts=[kext], source=f"{kext}: {SRC_KEXT}")
    if (vendor, dev) in NATIVE_NIC:
        return Support("native", NATIVE_NIC[(vendor, dev)], "10.13", "26", source="built into macOS")
    if vendor == "1d6a" and dev in AQUANTIA:
        return Support("native", "Aquantia AQC107 10GbE", "10.15", "26", info={"quirk": "ForceAquantiaEthernet"},
                       source="built into macOS (ForceAquantiaEthernet quirk)")
    if vendor == "1af4":
        return Support("native", "virtio-net", "12", "26", source="built into macOS")
    if vendor == "8086" and dev in (0x1539, 0x157B, 0x157C, 0x1536, 0x1537, 0x1538):
        return Support("kext", "Intel I211 Ethernet", "10.13", "26", kexts=["AppleIGB"],
                       notes=["AppleIGB has no downloadable release: use a USB Ethernet adapter or add it by hand."])
    if vendor == "10ec" and dev == 0x8136:
        return Support("kext", "Realtek RTL8100/8101 Fast Ethernet", "10.13", "26", kexts=["RealtekRTL8100"],
                       notes=["RealtekRTL8100 has no downloadable release: add it by hand."])
    if vendor == "14e4":
        return Support("experimental", "Broadcom Ethernet (may work natively)", "10.13", "26")
    return Support("unsupported", f"Ethernet {vendor}:{device} has no known macOS driver")


# --------------------------------------------------------------------------- Wi-Fi
# Intel: itlwm matches every Intel PCI device and filters in code, so its list of
# supported cards is kept here (source: itlwm README / SRC_WIRELESS).
# Broadcom: native cards are built into macOS (removed in Sonoma, SRC_TAHOE);
# AirportBrcmFixup's own IONameMatch decides the rest.

INTEL_WIFI = {0x2723: "AX200", 0x2725: "AX210", 0xA0F0: "AX201", 0x02F0: "AX201", 0x06F0: "AX201",
              0x34F0: "AX201", 0x43F0: "AX201", 0x3DF0: "AX201", 0x4DF0: "AX201", 0x9DF0: "AC 9560",
              0x31DC: "AC 9560", 0xA370: "AC 9560", 0x30DC: "AC 9560", 0x2526: "AC 9260",
              0x24FD: "AC 8265", 0x24F3: "AC 8260", 0x095A: "AC 7265", 0x095B: "AC 7265",
              0x3165: "AC 3165", 0x3166: "AC 3165", 0x24FB: "AC 3168", 0x08B1: "AC 7260",
              0x08B2: "AC 7260", 0x51F0: "AX211", 0x51F1: "AX211", 0x54F0: "AX211", 0x7AF0: "AX211",
              0x7A70: "AX211"}
BRCM_NATIVE = {0x43A0: "BCM4360", 0x43BA: "BCM43602", 0x43A3: "BCM4350", 0x43DC: "BCM4355",
               0x4331: "BCM4331", 0x4353: "BCM43224", 0x4464: "BCM4364"}


def classify_wifi(vendor: str, device: str) -> Support:
    from .. import kextdb
    dev = int(device, 16)
    if vendor == "8086" and dev in INTEL_WIFI:
        return Support("kext", f"Intel Wi-Fi {INTEL_WIFI[dev]}", "10.13", "15", kexts=["itlwm"],
                       notes=["Intel Wi-Fi uses itlwm: after installing macOS, install the HeliPort app to connect.",
                              "Wi-Fi is not available inside the macOS installer: use Ethernet."],
                       source="https://openintelwireless.github.io/itlwm/Compat.html")
    if vendor == "8086":
        return Support("unsupported", f"Intel Wi-Fi 8086:{device} (not supported by itlwm)")
    if vendor == "14e4" and dev in BRCM_NATIVE:
        return Support("native", f"Broadcom {BRCM_NATIVE[dev]} Wi-Fi", "10.13", "13",
                       notes=["Native up to Ventura; Sonoma and newer removed the driver."], source=SRC_WIRELESS)
    if kextdb.match_pci(vendor, device, "wifi"):
        return Support("kext", f"Broadcom Wi-Fi 14e4:{device}", "10.13", "13", kexts=["AirportBrcmFixup"],
                       notes=["Sonoma and newer removed the Broadcom Wi-Fi driver."],
                       source=f"AirportBrcmFixup: {SRC_KEXT}")
    return Support("unsupported", f"Wi-Fi card {vendor}:{device} has no macOS driver "
                   "(Realtek/MediaTek/Qualcomm are unsupported)", source=SRC_WIRELESS)


# --------------------------------------------------------------------------- Bluetooth (USB)

BT_KEXTS = {
    "IntelBluetoothFirmware": ("Intel Bluetooth", ["IntelBluetoothFirmware", "IntelBTPatcher", "BlueToolFixup"]),
    "BrcmPatchRAM3": ("Broadcom Bluetooth", ["BrcmPatchRAM3", "BrcmFirmwareData", "BlueToolFixup"]),
}


def classify_bluetooth(vendor: str, product: str) -> Optional[Support]:
    from .. import kextdb
    for kext in kextdb.match_usb(vendor, product, "bluetooth"):
        summary, kexts = BT_KEXTS[kext]
        return Support("kext", summary, "10.13", "26", kexts=list(kexts), source=f"{kext}: {SRC_KEXT}")
    if vendor == "05ac":
        return Support("native", "Apple/Broadcom Bluetooth module", "10.13", "26", kexts=["BlueToolFixup"],
                       source="built into macOS")
    return None


# --------------------------------------------------------------------------- USB / storage / misc
SRC_USB = "https://dortania.github.io/OpenCore-Post-Install/usb/"
SRC_STORAGE = "https://dortania.github.io/OpenCore-Install-Guide/macos-limits.html#storage-support"

USB_VENDORS = {"8086": "Intel", "1022": "AMD", "1b21": "ASMedia", "1912": "Renesas", "1b73": "Fresco Logic",
               "1106": "VIA", "1d94": "Hygon"}


def classify_usb_controller(vendor: str, device: str, cls: str, ports: Optional[int]) -> Optional[Support]:
    prog = cls[4:6]
    brand = USB_VENDORS.get(vendor, vendor)
    if prog == "30":
        s = Support("native", f"{brand} USB 3 (XHCI) controller", "10.13", "26", source=SRC_USB)
        if vendor not in ("8086", "1022"):
            s.status = "experimental"
            s.notes.append("Third-party USB controller: usually works with the generic macOS driver, not guaranteed.")
    elif prog == "20":
        s = Support("native", f"{brand} USB 2 (EHCI) controller", "10.13", "26", source=SRC_USB)
    elif prog in ("00", "10"):
        return None     # UHCI/OHCI companions: ignore
    else:
        return None
    if ports:
        s.info["ports"] = ports
        s.summary += f", {ports} port" + ("s" if ports != 1 else "")
        if ports > 15:
            s.notes.append(f"This controller exposes {ports} ports (USB 2 and USB 3 halves count separately): "
                           "macOS uses at most 15, so some ports may not work until you create a USB map "
                           "after installing (e.g. with USBToolBox).")
    return s


BAD_NVME = {("144d", "a808"): "Samsung PM981 / 970 EVO Plus OEM", ("144d", "a809"): "Samsung PM991",
            ("1344", "5405"): "Micron 2200S"}


def classify_nvme(vendor: str, device: str) -> Support:
    if (vendor, device) in BAD_NVME:
        return Support("unsupported", f"{BAD_NVME[(vendor, device)]} NVMe: known to be incompatible with macOS "
                       "(use it for data only, or install on another drive)", source=SRC_STORAGE)
    return Support("native", "NVMe SSD", "10.13", "26", kexts=["NVMeFix"], source=SRC_STORAGE)


def classify_sata(cls: str, ports: Optional[int]) -> Support:
    if cls.startswith("0104"):
        return Support("unsupported", "RAID / Intel RST mode: switch the SATA mode to AHCI in the BIOS",
                       source=SRC_STORAGE)
    s = Support("native", "SATA (AHCI) controller" + (f", {ports} port" + ("s" if ports != 1 else "")
                                                       if ports else ""), "10.13", "26", source=SRC_STORAGE)
    return s


def classify_cardreader(vendor: str, device: str) -> Support:
    if vendor == "10ec":
        return Support("unsupported", "Realtek PCIe SD card reader: needs the third-party RealtekCardReader "
                       "kext, not included (not out-of-the-box)",
                       source="https://github.com/0xFireWolf/RealtekCardReader")
    return Support("unsupported", "PCIe SD card reader: no macOS driver")


def classify_thunderbolt() -> Support:
    return Support("experimental", "Thunderbolt controller", "10.13", "26",
                   notes=["Devices plugged in before boot usually work; hot-plug and Thunderbolt displays need "
                          "extra setup (SSDT, firmware flashing) that this installer does not do."],
                   source="https://dortania.github.io/OpenCore-Post-Install/")


FINGERPRINT_VENDORS = {"27c6": "Goodix", "138a": "Validity/Synaptics", "06cb": "Synaptics", "10a5": "FPC",
                       "1c7a": "LighTuning", "04f3": "Elan"}


def classify_usb_device(dev: dict) -> Optional[tuple]:
    """(category, Support) for interesting USB devices, None for the rest."""
    ifaces = set(dev.get("interfaces", []))
    name = dev.get("name") or f"{dev['vendor']}:{dev['product']}"
    lname = name.lower()
    if "fingerprint" in lname or (dev["vendor"] in FINGERPRINT_VENDORS and "ff" in ifaces and "03" not in ifaces):
        return "Fingerprint", Support("unsupported", "Fingerprint reader: macOS has no driver for it (Touch ID "
                                      "needs Apple hardware)")
    if "0e" in ifaces:
        if re.search(r"\bIR\b|infrared", name, re.I):
            return "Webcam", Support("unsupported", "Infrared (Windows Hello) camera: not usable on macOS")
        return "Webcam", Support("native", "USB webcam (UVC)", "10.13", "26",
                                 notes=["Standard UVC webcams work with the built-in macOS driver."],
                                 source="built into macOS (USB Video Class)")
    if "01" in ifaces and "0e" not in ifaces:
        return "Audio", Support("native", "USB audio device", "10.13", "26",
                                source="built into macOS (USB Audio Class)")
    if "e0" in ifaces or dev.get("class") == "e0":
        bt = classify_bluetooth(dev["vendor"], dev["product"])
        if bt:
            return "Bluetooth", bt
        return "Bluetooth", Support("unsupported", "Bluetooth adapter without a macOS driver "
                                    "(Realtek/MediaTek/Qualcomm)", source=SRC_WIRELESS)
    return None


# --------------------------------------------------------------------------- chipset

CHIPSET_300 = {0xA303, 0xA304, 0xA305, 0xA306, 0xA308, 0xA309, 0xA30A, 0xA30C, 0xA30D, 0xA30E}
CHIPSET_400 = {0x0684, 0x0685, 0x0687, 0x068C, 0x068D, 0xA3C8, 0xA3DA, 0x0284, 0x0285, 0x02E0}


def chipset_series(lpc_vendor: str, lpc_device: str, name: str = "") -> Optional[int]:
    """Intel PCH series (100/200/300/400/500...) from the LPC/eSPI bridge, if recognizable."""
    if lpc_vendor != "8086":
        return None
    dev = int(lpc_device, 16)
    if dev in CHIPSET_300:
        return 300
    if dev in CHIPSET_400:
        return 400
    m =re.search(r"\b[ZHBQ]([1-7])[0-9]0\b", name or "")
    if m:
        return int(m.group(1)) * 100
    return None
