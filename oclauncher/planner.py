"""The brain: hardware report -> compatibility analysis -> OpenCore build plan.

analyze(report)            -> Analysis: per-component support + per-macOS verdicts
make_plan(analysis, ver)   -> Plan: everything needed to build EFI/OC for that version

Rules follow the Dortania OpenCore Install Guide. Every decision records a
human-readable reason so the UI can explain *why* (the project is educational).
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from .data import devices as D
from .data import macos as M
from .data.smbios import MAX_MACOS
from .data.kexts import CATALOG


# =========================================================================== analysis

@dataclass
class Component:
    category: str           # "CPU", "Graphics", "Audio", "Ethernet", "Wi-Fi", "Bluetooth", "Input", "Storage"
    name: str               # what was found
    support: D.Support
    device: Optional[dict] = None   # PCI/USB record


@dataclass
class Verdict:
    macos: M.MacOS
    ok: bool
    recommended: bool = False
    blockers: list = field(default_factory=list)   # why it cannot be installed
    caveats: list = field(default_factory=list)    # what will not work
    gpus: list = field(default_factory=list)       # usable display components for this version


@dataclass
class Analysis:
    report: dict
    laptop: bool
    virtual: Optional[str]
    platform: str                  # e.g. "coffeelake", "amd-zen", "vm"
    vendor: str                    # "intel" | "amd"
    components: list
    verdicts: list
    notes: list = field(default_factory=list)

    @property
    def installable(self):
        return [v for v in self.verdicts if v.ok]

    @property
    def recommended(self):
        for v in self.verdicts:
            if v.recommended:
                return v
        return None

    def by_category(self, cat):
        return [c for c in self.components if c.category == cat]


def _in_range(sup: D.Support, version: str) -> bool:
    if not sup.usable or not sup.min_macos:
        return False
    return M.vkey(sup.min_macos) <= M.vkey(version) <= M.vkey(sup.max_macos or "99")


def analyze(report: dict) -> Analysis:
    machine = report.get("machine", {})
    laptop = machine.get("form_factor") == "laptop"
    virtual = machine.get("virtual")
    cpu = report["cpu"]
    comps = []

    cpu_sup = D.classify_cpu(cpu, laptop)
    if virtual and cpu_sup.status == "unsupported" and cpu.get("vendor") == "intel":
        # Hypervisors often expose odd CPU models: treat any SSE4.2 Intel CPU as usable.
        cpu_sup = D.Support("experimental", f"Virtual CPU ({cpu.get('name')})", "10.13", "26",
                            info={"platform": "vm"})
    if virtual:
        cpu_sup.notes.append(f"Running inside a virtual machine ({virtual}): support is experimental.")
        if virtual == "virtualbox" and cpu.get("vendor") == "amd":
            cpu_sup.notes.append("macOS in VirtualBox on an AMD host is very unreliable.")
    comps.append(Component("CPU", cpu.get("name", "?"), cpu_sup))

    pci = report.get("pci", [])
    usb_ports = report.get("usb_ports", {})
    displays = {}
    for dev in pci:
        kind = dev.get("kind")
        label = dev.get("name") or f"{dev['vendor']}:{dev['device']}"
        if kind == "display":
            c = Component("Graphics", label, D.classify_gpu(dev["vendor"], dev["device"], dev["slot"]), dev)
            displays[dev["slot"][:-2]] = c          # "0000:01:00" -> for its HDMI audio function
            comps.append(c)
        elif kind == "ethernet":
            comps.append(Component("Ethernet", label, D.classify_ethernet(dev["vendor"], dev["device"]), dev))
        elif kind == "wifi":
            comps.append(Component("Wi-Fi", label, D.classify_wifi(dev["vendor"], dev["device"]), dev))
        elif kind == "nvme":
            comps.append(Component("Storage", label, D.classify_nvme(dev["vendor"], dev["device"]), dev))
        elif kind in ("sata", "raid"):
            comps.append(Component("Storage", label, D.classify_sata(dev["class"], dev.get("ata_ports")), dev))
        elif kind == "usb":
            s = D.classify_usb_controller(dev["vendor"], dev["device"], dev["class"], usb_ports.get(dev["slot"]))
            if s:
                comps.append(Component("USB", label, s, dev))
        elif kind == "cardreader":
            comps.append(Component("Card reader", label, D.classify_cardreader(dev["vendor"], dev["device"]), dev))
        elif kind == "thunderbolt" and not any(c.category == "Thunderbolt" for c in comps):
            comps.append(Component("Thunderbolt", label, D.classify_thunderbolt(), dev))

    # HDMI / DisplayPort audio: the audio function sitting next to a graphics card
    for dev in pci:
        if dev.get("kind") == "audio" and dev["slot"][:-2] in displays:
            g = displays[dev["slot"][:-2]]
            gs = g.support
            s = D.Support(gs.status if gs.usable else "unsupported",
                          f"HDMI/DisplayPort audio of {gs.summary}", gs.min_macos, gs.max_macos,
                          source=gs.source)
            if not gs.usable:
                s.summary = "HDMI/DisplayPort audio of an unsupported graphics card"
            comps.append(Component("Audio", dev.get("name") or "HDMI audio", s, dev))

    # USB devices: Bluetooth, webcams, USB audio, fingerprint readers
    seen = set()
    for u in report.get("usb", []):
        res = D.classify_usb_device(u)
        if res and (u["vendor"], u["product"]) not in seen:
            seen.add((u["vendor"], u["product"]))
            cat, s = res
            comps.append(Component(cat, u.get("name") or f"{u['vendor']}:{u['product']}", s, u))

    # Analog audio: one entry per codec (macOS Tahoe removed AppleHDA, see SRC_TAHOE)
    for c in report.get("audio_codecs", []):
        if virtual:
            s = D.Support("unsupported", "Virtual audio is not supported")
        elif c.get("applealc"):
            s = D.Support("kext", f"{c['name']} via AppleALC", "10.13", "15", kexts=["AppleALC"],
                          info={"codec": c["applealc"], "analog": True}, source="https://github.com/acidanthera/AppleALC",
                          notes=["You may need to try different layout-id values to get every jack working.",
                                 "macOS Tahoe removed AppleHDA: analog audio does not work there."])
        else:
            s = D.Support("unsupported", f"{c['name']}: codec not supported by AppleALC",
                          source="https://github.com/acidanthera/AppleALC/wiki/Supported-codecs")
        comps.append(Component("Audio", c["name"], s))

    # Laptop input
    if laptop:
        inp = report.get("input", {})
        if inp.get("i2c_hid"):
            comps.append(Component("Input", "I2C touchpad", D.Support(
                "experimental", "I2C (HID) touchpad via VoodooI2C", "10.13", "26", kexts=["VoodooI2C"],
                notes=["I2C touchpads often need a custom SSDT-GPI0: keep a USB mouse at hand."])))
        if inp.get("ps2"):
            comps.append(Component("Input", "PS/2 keyboard/touchpad", D.Support(
                "kext", "PS/2 keyboard and touchpad via VoodooPS2", "10.13", "26", kexts=["VoodooPS2Controller"])))

    platform = cpu_sup.info.get("platform", "unknown")
    if virtual:
        platform = "vm"
    a = Analysis(report, laptop, virtual, platform, cpu.get("vendor"), comps, [])
    a.verdicts = [_verdict(a, m) for m in M.RELEASES]
    _pick_recommended(a)
    return a


def _verdict(a: Analysis, mac: M.MacOS) -> Verdict:
    v = Verdict(mac, ok=True)
    ver = mac.version
    cpu = a.by_category("CPU")[0]
    if not cpu.support.usable:
        v.ok = False
        v.blockers.append(f"CPU not supported: {cpu.support.summary}")
    elif not _in_range(cpu.support, ver):
        v.ok = False
        v.blockers.append(f"{cpu.support.summary} supports macOS {cpu.support.min_macos} to {cpu.support.max_macos}")

    gpus = [g for g in a.by_category("Graphics") if _in_range(g.support, ver)]
    v.gpus = gpus
    if not gpus:
        v.ok = False
        names = ", ".join(g.support.summary for g in a.by_category("Graphics")) or "no display device found"
        v.blockers.append(f"No graphics card works on this version ({names})")
    elif a.platform in ("rocketlake", "alderlake", "raptorlake") and not any(
            g.support.info.get("kind") == "dgpu" for g in gpus):
        v.ok = False
        v.blockers.append("This Intel CPU generation needs a supported AMD graphics card")

    for g in a.by_category("Graphics"):
        if g not in gpus and g.support.usable:
            v.caveats.append(f"{g.support.summary} will not work on this version (supported {g.support.min_macos}-{g.support.max_macos})")
        if g in gpus and g.support.info.get("gen") == "pascal-maxwell":
            v.caveats.append("NVIDIA acceleration only after installing the NVIDIA Web Drivers")
        if g in gpus and g.support.status == "experimental":
            v.caveats.append(f"{g.support.summary}: experimental")

    nets = [c for c in a.by_category("Ethernet") + a.by_category("Wi-Fi") if _in_range(c.support, ver)]
    installer_net = [c for c in a.by_category("Ethernet") if _in_range(c.support, ver)]
    if not nets:
        v.caveats.append("No working network adapter: you'll need a USB Ethernet adapter or USB tethering")
    elif not installer_net:
        if any(c.support.kexts == ["itlwm"] for c in nets) and M.vkey(ver) <= M.vkey("13"):
            pass    # AirportItlwm works inside the installer
        else:
            v.caveats.append("Wi-Fi will not work inside the macOS installer: connect Ethernet or use USB tethering")
    for c in a.by_category("Wi-Fi"):
        if c.support.usable and not _in_range(c.support, ver):
            v.caveats.append(f"{c.support.summary} will not work on this version")
    audio = [c for c in a.by_category("Audio") if c.support.info.get("analog")]
    if audio and not any(_in_range(c.support, ver) for c in audio):
        if M.vkey(ver) >= M.vkey("26") and any(c.support.usable for c in audio):
            v.caveats.append("Onboard audio does not work: Apple removed AppleHDA in macOS Tahoe")
        else:
            v.caveats.append("Onboard audio will not work")
    for c in a.by_category("Storage"):
        if not c.support.usable:
            v.caveats.append(c.support.summary)
    if mac.version == "26":
        v.caveats.append("macOS Tahoe is the last release for Intel Macs; Hackintosh support is still maturing")
    return v


def _pick_recommended(a: Analysis):
    ok = [v for v in a.verdicts if v.ok]
    if not ok:
        return
    # newest version with the fewest caveats; prefer not-Tahoe when equivalent
    best = max(ok, key=lambda v: (-len(v.caveats), v.macos.key if v.macos.version != "26" else (0,)))
    best.recommended = True


# =========================================================================== plan

@dataclass
class Item:
    name: str
    reason: str


@dataclass
class Plan:
    macos: M.MacOS
    smbios: str
    smbios_reason: str
    kexts: list = field(default_factory=list)          # [Item]
    drivers: list = field(default_factory=list)        # [Item] (.efi names)
    ssdts: list = field(default_factory=list)          # [Item] (.aml names)
    acpi_patches: list = field(default_factory=list)   # [dict]
    kernel_patches: list = field(default_factory=list)  # [dict] Kernel->Patch entries
    booter_quirks: dict = field(default_factory=dict)
    kernel_quirks: dict = field(default_factory=dict)
    uefi_quirks: dict = field(default_factory=dict)
    acpi_quirks: dict = field(default_factory=dict)
    emulate: dict = field(default_factory=dict)
    device_properties: dict = field(default_factory=dict)  # path -> {key: bytes|str}
    boot_args: list = field(default_factory=list)
    secure_boot_model: str = "Default"
    apfs_legacy: bool = False
    apfs_jumpstart: bool = True                         # UEFI->APFS->EnableJumpstart
    nvram_restrictevents: dict = field(default_factory=dict)  # 4D1FDA02-... (RestrictEvents/OpenCore vars)
    amd_patches: bool = False
    cores: int = 0
    audio_codec: Optional[str] = None
    audio_layout: Optional[int] = None
    rom_mac: Optional[str] = None
    explanations: list = field(default_factory=list)   # (topic, text)
    warnings: list = field(default_factory=list)

    def add_kext(self, name, reason):
        if name not in [k.name for k in self.kexts]:
            self.kexts.append(Item(name, reason))

    def add_ssdt(self, name, reason):
        if name not in [s.name for s in self.ssdts]:
            self.ssdts.append(Item(name, reason))

    def add_arg(self, arg):
        if arg not in self.boot_args:
            self.boot_args.append(arg)


def _hex(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", ""))


def _cpu_suffix(name: str) -> str:
    m = re.search(r"\d{4,5}([A-Z]{1,2})\b", name or "")
    suf = m.group(1) if m else ""
    if suf.startswith(("U", "Y")):
        return "u"
    if suf.startswith("H"):
        return "h"
    if suf.startswith("F") or suf == "KF":
        return "f"
    return suf.lower()


def _choose_smbios(a: Analysis, mac: M.MacOS, has_dgpu: bool, igpu_gen: Optional[str]) -> tuple:
    p = a.platform
    name = a.report["cpu"].get("name", "")
    suffix = _cpu_suffix(name)
    if p == "vm":
        cands = ["iMacPro1,1", "MacPro7,1"]
        why = "Virtual machines work best with a Mac model that has no integrated GPU"
    elif p == "amd-zen":
        if a.laptop:
            cands = ["MacBookPro16,3", "MacBookPro16,2"]
            why = "AMD laptops use a 13-inch MacBook Pro identity"
        else:
            cands = ["iMacPro1,1", "MacPro7,1"] if M.vkey(mac.version) < M.vkey("10.15") else ["MacPro7,1"]
            why = "AMD desktops use a Mac Pro / iMac Pro identity (Macs without an Intel iGPU)"
    elif a.laptop:
        table = {
            "haswell": ["MacBookPro11,4"], "broadwell": ["MacBookPro12,1"],
            "skylake": ["MacBookPro13,3"] if suffix == "h" else ["MacBookPro13,1"],
            "kabylake": (["MacBookPro14,3"] if suffix == "h" else ["MacBookPro14,1"]) + ["MacBookPro15,2"],
            "kabylake-r": ["MacBookPro14,1", "MacBookPro15,2"], "amberlake": ["MacBookPro14,1", "MacBookPro15,2"],
            "coffeelake": (["MacBookPro15,1", "MacBookPro16,1"] if suffix == "h" else ["MacBookPro15,2", "MacBookPro16,2"]),
            "whiskeylake": ["MacBookPro15,4", "MacBookPro16,2"],
            "cometlake": ["MacBookPro16,1"] if suffix == "h" else ["MacBookPro16,2"],
            "icelake": ["MacBookAir9,1", "MacBookPro16,2"] if suffix == "u" and "i3" in name else ["MacBookPro16,2"],
        }
        cands = table.get(p, ["MacBookPro16,2"])
        why = "Laptop: the MacBook Pro whose CPU/iGPU is closest to yours"
    else:
        no_igpu = igpu_gen is None
        if p == "ivybridge":
            cands = ["iMac13,2", "MacPro6,1"]
        elif p in ("haswell", "broadwell"):
            cands = (["iMac14,4" if p == "haswell" else "iMac16,2", "iMac16,2"] if not has_dgpu
                     else ["iMac15,1", "iMac17,1", "iMacPro1,1", "MacPro7,1"])
        elif p == "skylake":
            cands = ["iMac17,1"] + (["iMacPro1,1", "MacPro7,1"] if has_dgpu else ["iMac18,1", "iMac19,1"])
        elif p == "kabylake":
            cands = (["iMac18,3"] if has_dgpu else ["iMac18,1"]) + ["iMac19,1"] + (["MacPro7,1"] if has_dgpu else [])
        elif p == "coffeelake":
            cands = ["iMacPro1,1", "MacPro7,1"] if no_igpu else ["iMac19,1", "iMac20,1"]
        elif p == "cometlake":
            big = re.search(r"i9-10(85|90)0", name)
            cands = ["MacPro7,1"] if no_igpu else (["iMac20,2"] if big else ["iMac20,1"])
        else:   # rocket/alder/raptor
            cands = ["MacPro7,1"]
        why = "Desktop: the iMac/Mac Pro model closest to your CPU and graphics"
    for c in cands:
        if M.vkey(MAX_MACOS[c]) >= mac.key:
            return c, why, False
    return cands[-1], why + f" (no Mac model with this hardware officially runs {mac.name})", True


IGPU_PROPS = {
    # gen: (display-output props, headless props)
    "haswell": ({"AAPL,ig-platform-id": "0300220D"}, {"AAPL,ig-platform-id": "04001204"}),
    "broadwell": ({"AAPL,ig-platform-id": "07002216"}, None),
    "skylake": ({"AAPL,ig-platform-id": "00001219"}, {"AAPL,ig-platform-id": "01001219"}),
    "kabylake": ({"AAPL,ig-platform-id": "00001259"}, {"AAPL,ig-platform-id": "03001259"}),
    "coffeelake": ({"AAPL,ig-platform-id": "07009B3E", "framebuffer-patch-enable": "01000000",
                    "framebuffer-stolenmem": "00003001", "framebuffer-fbmem": "00009000"},
                   {"AAPL,ig-platform-id": "0300913E"}),
    "cometlake": ({"AAPL,ig-platform-id": "07009B3E", "device-id": "C89B0000", "framebuffer-patch-enable": "01000000",
                   "framebuffer-stolenmem": "00003001", "framebuffer-fbmem": "00009000"},
                  {"AAPL,ig-platform-id": "0300C89B", "device-id": "C89B0000"}),
}
IGPU_LAPTOP = {
    "haswell": {"AAPL,ig-platform-id": "0500260A", "framebuffer-patch-enable": "01000000",
                "framebuffer-cursormem": "00009000"},
    "broadwell": {"AAPL,ig-platform-id": "06002616"},
    "skylake": {"AAPL,ig-platform-id": "00001619"},
    "kabylake": {"AAPL,ig-platform-id": "00001B59"},
    "kabylake-r": {"AAPL,ig-platform-id": "0000C087", "device-id": "16590000"},
    "amberlake": {"AAPL,ig-platform-id": "0000C087"},
    "coffeelake": {"AAPL,ig-platform-id": "0900A53E"},
    "whiskeylake": {"AAPL,ig-platform-id": "0900A53E", "device-id": "9B3E0000"},
    "cometlake": {"AAPL,ig-platform-id": "0900A53E", "device-id": "9B3E0000"},
    "cometlake-u": {"AAPL,ig-platform-id": "0900A53E", "device-id": "9B3E0000"},
    "icelake": {"AAPL,ig-platform-id": "0000528A"},
}
FB_PATCH = {"framebuffer-patch-enable": "01000000", "framebuffer-stolenmem": "00003001",
            "framebuffer-fbmem": "00009000"}

LEGACY_MEMORY = {"ivybridge", "haswell", "broadwell", "skylake", "kabylake", "kabylake-r", "amberlake"}


T2_MODELS = ("iMacPro1,1", "MacPro7,1", "iMac20,1", "iMac20,2", "Macmini8,1", "MacBookAir9,1",
             "MacBookPro15,1", "MacBookPro15,2", "MacBookPro15,3", "MacBookPro15,4",
             "MacBookPro16,1", "MacBookPro16,2", "MacBookPro16,3", "MacBookPro16,4")


def make_plan(a: Analysis, version: str, audio_layout: Optional[int] = None, tahoe_audio: bool = False) -> Plan:
    """tahoe_audio: opt in to SimpleHDA (third-party, experimental) to restore analog audio on macOS 26."""
    mac = M.get(version)
    verdict = next(v for v in a.verdicts if v.macos.version == version)
    report = a.report
    cpu = report["cpu"]
    machine = report.get("machine", {})
    board = (machine.get("board_name") or "").upper()
    sys_vendor = (machine.get("vendor") or "").upper()
    p = a.platform
    intel = a.vendor == "intel"
    usable_gpus = verdict.gpus
    dgpus = [g for g in usable_gpus if g.support.info.get("kind") == "dgpu"]
    igpus = [g for g in usable_gpus if g.support.info.get("kind") == "igpu" and g.support.info.get("vendor") == "intel"]
    igpu_any = [g for g in a.by_category("Graphics")
                if g.support.info.get("kind") == "igpu" and g.support.info.get("vendor") == "intel"]
    igpu_gen = igpu_any[0].support.info.get("gen") if igpu_any else None
    has_dgpu = bool(dgpus)

    smbios, why, over_max = _choose_smbios(a, mac, has_dgpu, igpu_gen if igpu_any else None)
    plan = Plan(mac, smbios, why)
    if over_max:
        plan.add_arg("-no_compat_check")
        plan.warnings.append(f"{smbios} is not officially supported by {mac.name}: using -no_compat_check "
                             "(you will not receive system updates).")

    # ------------------------------------------------------------- base kexts
    plan.add_kext("Lilu", "Required by almost every other kext")
    plan.add_kext("VirtualSMC", "macOS refuses to boot without an SMC: this emulates it")
    plan.add_kext("WhateverGreen", "Graphics fixes (framebuffer, HDMI, black screen issues)")
    if intel and p != "vm":
        plan.add_kext("SMCProcessor", "Shows Intel CPU temperatures")
        if not a.laptop:
            plan.add_kext("SMCSuperIO", "Shows fan speeds on desktop motherboards")
    if smbios in ("MacPro7,1", "iMacPro1,1") or a.vendor == "amd":
        plan.add_kext("RestrictEvents", f"Removes the memory-configuration warnings of {smbios} and other fixes")
    if smbios in T2_MODELS and M.vkey(version) >= M.vkey("14"):
        # Macs with a T2 chip get updates only if Apple's secure boot accepts the machine: RestrictEvents'
        # sbvmm makes macOS treat it as a virtual machine for that check (Dortania / acidanthera RestrictEvents).
        plan.add_kext("RestrictEvents", "Lets macOS install system updates with this Mac model (revpatch=sbvmm)")
        plan.nvram_restrictevents["revpatch"] = "sbvmm"

    # ------------------------------------------------------------- components
    for comp in a.components:
        s = comp.support
        if not _in_range(s, version):
            continue
        if comp.category == "Audio":
            plan.add_kext("AppleALC", f"Onboard audio for {comp.name}")
            plan.audio_codec = s.info.get("codec")
            continue
        if comp.category == "Graphics":
            continue
        if comp.category == "Wi-Fi" and s.kexts == ["itlwm"]:
            if M.vkey(version) <= M.vkey("13") and not any(
                    _in_range(c.support, version) for c in a.by_category("Ethernet")):
                plan.add_kext("AirportItlwm", f"{comp.name}: native-looking Wi-Fi that also works in the installer")
            else:
                plan.add_kext("itlwm", f"{comp.name}: install the HeliPort app after macOS to connect")
            continue
        for k in s.kexts:
            plan.add_kext(k, f"{comp.category}: {s.summary}")
        if s.info.get("quirk") == "ForceAquantiaEthernet":
            plan.kernel_quirks["ForceAquantiaEthernet"] = True
    if plan.audio_codec:
        plan.audio_layout = audio_layout

    if a.laptop:
        plan.add_kext("SMCBatteryManager", "Battery percentage and charging status")
        plan.add_kext("ECEnabler", "Lets macOS read battery information from the embedded controller")
        plan.add_kext("BrightnessKeys", "Brightness keys on the keyboard")
        if not any(k.name.startswith("Voodoo") for k in plan.kexts):
            plan.add_kext("VoodooPS2Controller", "Built-in keyboard (PS/2)")
    if p in ("alderlake", "raptorlake"):
        plan.add_kext("CpuTopologyRebuild", "Makes macOS understand performance + efficiency cores")
    # AppleIntelMCEReporter (loaded for MacPro/iMacPro SMBIOS) panics on AMD and on virtual CPUs without
    # Machine Check support (CMCI): "Controller failed to Init()" followed by a page fault in its destructor.
    if M.vkey(version) >= M.vkey("10.15") and (
            a.vendor == "amd" or (p == "vm" and smbios in ("MacPro6,1", "MacPro7,1", "iMacPro1,1"))):
        plan.add_kext("AppleMCEReporterDisabler", "Prevents a kernel panic in Apple's Machine Check driver "
                      "(AMD CPUs and virtual machines with a Mac Pro / iMac Pro SMBIOS)")
    for g in usable_gpus:
        for k in g.support.kexts:
            if k != "WhateverGreen":
                plan.add_kext(k, f"Graphics: {g.support.summary}")

    # ------------------------------------------------------------- graphics props / args
    unsupported_dgpu = [g for g in a.by_category("Graphics")
                        if g.support.info.get("kind") == "dgpu" and g not in usable_gpus]
    for g in usable_gpus:
        for arg in g.support.info.get("boot_args", []):
            plan.add_arg(arg)
        if g.support.info.get("gen") == "pascal-maxwell":
            plan.add_arg("nv_disable=1")
            plan.warnings.append("NVIDIA Maxwell/Pascal: nv_disable=1 is set so the installer can boot. After "
                                 "installing the NVIDIA Web Drivers replace it with nvda_drv=1.")
    if unsupported_dgpu:
        if not dgpus:
            plan.add_arg("-wegnoegpu")
            plan.explanations.append(("Graphics", "-wegnoegpu turns off the unsupported graphics card(s): "
                                      + ", ".join(g.name for g in unsupported_dgpu)))
        else:
            for g in unsupported_dgpu:
                if g.device and g.device.get("path"):
                    plan.device_properties.setdefault(g.device["path"], {})["disable-gpu"] = _hex("01000000")
            plan.explanations.append(("Graphics", "Unsupported card(s) are disabled with the WhateverGreen "
                                      "'disable-gpu' property: " + ", ".join(g.name for g in unsupported_dgpu)))
        if a.laptop and not dgpus:
            plan.explanations.append(("Graphics", "The NVIDIA/AMD laptop GPU stays off; macOS uses the Intel iGPU."))

    if igpus and p != "vm":
        gen = igpus[0].support.info["gen"]
        path = (igpus[0].device or {}).get("path") or "PciRoot(0x0)/Pci(0x2,0x0)"
        if gen == "skylake" and M.vkey(version) >= M.vkey("13"):
            # Ventura+ has no Skylake driver: present the iGPU as Kaby Lake (Dortania GPU guide)
            props = ({"AAPL,ig-platform-id": "00001B59", "device-id": "16590000"} if a.laptop else
                     {"AAPL,ig-platform-id": "03001259" if has_dgpu else "00001259", "device-id": "12590000"})
            why = "Skylake iGPU presented as Kaby Lake (Ventura and newer only ship the Kaby Lake driver)"
        elif a.laptop:
            props = dict(IGPU_LAPTOP.get(gen, {}))
            if gen in ("coffeelake", "whiskeylake", "cometlake", "cometlake-u", "icelake"):
                props.update(FB_PATCH)
            why = "Laptop iGPU framebuffer"
        elif has_dgpu:
            props = (IGPU_PROPS.get(gen.replace("-u", ""), ({}, None))[1]) or {}
            why = "iGPU in headless mode: the AMD card drives the monitors, the iGPU helps with video encoding"
        else:
            props = dict(IGPU_PROPS.get(gen.replace("-u", ""), ({}, {}))[0])
            why = "iGPU drives the monitors"
        if props:
            plan.device_properties[path] = {k: _hex(v) for k, v in props.items()}
            plan.explanations.append(("Graphics", f"{why} (AAPL,ig-platform-id {props['AAPL,ig-platform-id']})"))

    if plan.audio_codec and plan.audio_layout is not None:
        plan.add_arg(f"alcid={plan.audio_layout}")

    # ------------------------------------------------------------- macOS Tahoe specifics (Dortania tahoe.md)
    if mac.version == "26":
        if any(g.support.info.get("vendor") == "amd" and g.support.info.get("kind") == "dgpu" for g in usable_gpus):
            plan.kexts = [k for k in plan.kexts if k.name != "WhateverGreen"]
            if "agdpmod=pikera" in plan.boot_args:
                plan.boot_args.remove("agdpmod=pikera")
                plan.kernel_patches.append({
                    "Comment": "board-id -> board-ix in AppleGraphicsDevicePolicy (replaces agdpmod=pikera)",
                    "Identifier": "com.apple.driver.AppleGraphicsDevicePolicy",
                    "Find": b"board-id", "Replace": b"board-ix"})
            plan.explanations.append(("Graphics", "macOS Tahoe: WhateverGreen is left out because it breaks "
                                      "AMD connector patching; a kernel patch replaces agdpmod=pikera"))
        if any(k.name == "IntelBluetoothFirmware" for k in plan.kexts):
            plan.add_arg("-ibtcompatbeta")
            plan.explanations.append(("Bluetooth", "-ibtcompatbeta is required for Intel Bluetooth on macOS Tahoe"))
        # Tahoe's own apfs.efi violates W^X memory protection: OpenCore must load acidanthera's fixed copy
        # (OcBinaryData/Drivers/apfs_aligned.efi) instead of "jumpstarting" the one inside the APFS container.
        plan.apfs_jumpstart = False
        plan.explanations.append(("UEFI", "macOS Tahoe: the APFS driver is loaded from apfs_aligned.efi "
                                  "(Apple's driver fixed by acidanthera) and APFS Jumpstart is turned off"))
        analog = [c for c in a.by_category("Audio") if c.support.usable and c.support.info.get("analog")]
        if tahoe_audio and analog:
            plan.add_kext("AppleALC", f"Onboard audio for {analog[0].name} (used by SimpleHDA)")
            plan.add_kext("SimpleHDA", "Experimental: brings back Apple's audio driver on Tahoe without "
                          "patching macOS (third-party kext by laobamac)")
            plan.audio_codec = analog[0].support.info.get("codec")
            plan.audio_layout = audio_layout
            plan.warnings.append("Tahoe audio uses SimpleHDA, a young third-party kext: if macOS does not boot, "
                                 "rebuild without it.")

    # ------------------------------------------------------------- ACPI
    acpi = report.get("acpi", {})
    names = set(acpi.get("names", []))
    dsdt = acpi.get("dsdt_available")
    lpc = next((d for d in report.get("pci", []) if d.get("kind") == "isa"), None)
    series = D.chipset_series(lpc["vendor"], lpc["device"], lpc.get("name") or "") if lpc else None
    if p != "vm":
        if intel:
            if p in ("alderlake", "raptorlake"):
                plan.add_ssdt("SSDT-PLUG-ALT", "CPU power management for 12th gen+ processor objects")
            else:
                plan.add_ssdt("SSDT-PLUG-DRTNIA", "Enables native CPU power management (XCPM)")
            legacy_ec = p in ("haswell", "broadwell", "ivybridge")
            if a.laptop:
                plan.add_ssdt("SSDT-EC-LAPTOP" if legacy_ec else "SSDT-EC-USBX-LAPTOP",
                              "Fake Embedded Controller + USB power (macOS needs a device called EC)")
                plan.add_ssdt("SSDT-PNLF", "Backlight control for the built-in screen")
            else:
                plan.add_ssdt("SSDT-EC-DESKTOP" if legacy_ec else "SSDT-EC-USBX-DESKTOP",
                              "Fake Embedded Controller + USB power (macOS needs a device called EC)")
            newer_rtc = (series or 0) >= 300 or p in ("coffeelake", "whiskeylake", "cometlake", "icelake",
                                                       "rocketlake", "alderlake", "raptorlake")
            if (dsdt and "AWAC" in names and "STAS" in names) or (not dsdt and newer_rtc):
                plan.add_ssdt("SSDT-AWAC", "300-series+ boards use a new clock (AWAC) macOS can't read: re-enable the legacy RTC")
            true_300 = series == 300 or (a.laptop and p in ("coffeelake", "whiskeylake"))
            if true_300:
                plan.add_ssdt("SSDT-PMC", "300-series chipsets hide the NVRAM controller: this exposes it")
            if a.laptop and any(c.category == "Input" and "I2C" in c.name for c in a.components):
                plan.add_ssdt("SSDT-XOSI", "Pretends to be Windows so the firmware enables the I2C touchpad")
                plan.acpi_patches.append({"Comment": "_OSI to XOSI (for SSDT-XOSI)", "Find": _hex("5F4F5349"),
                                          "Replace": _hex("584F5349")})
        else:
            plan.add_ssdt("SSDT-EC-USBX-DESKTOP", "Fake Embedded Controller + USB power (macOS needs a device called EC)")
            if re.search(r"\b(B550|A520)\b", board):
                plan.add_ssdt("SSDT-CPUR", "B550/A520 boards declare CPUs in a way macOS can't read")
    if p != "vm" and not dsdt:
        plan.explanations.append(("ACPI", "The DSDT could not be read, so the generic Dortania prebuilt SSDTs are "
                                  "used. They cover the most common boards."))

    # ------------------------------------------------------------- quirks
    bq = {"AvoidRuntimeDefrag": True, "EnableSafeModeSlide": True, "ProvideCustomSlide": True,
          "SetupVirtualMap": True, "ResizeAppleGpuBars": -1, "DevirtualiseMmio": False,
          "EnableWriteUnprotector": False, "RebuildAppleMemoryMap": True, "SyncRuntimePermissions": True,
          "ProtectUefiServices": False}
    if p in LEGACY_MEMORY:
        bq.update(EnableWriteUnprotector=True, RebuildAppleMemoryMap=False, SyncRuntimePermissions=False)
    elif intel and p != "vm":
        bq["DevirtualiseMmio"] = True
        if not a.laptop and ((series or 0) >= 300 or p in ("cometlake", "rocketlake", "alderlake", "raptorlake")):
            bq["ProtectUefiServices"] = True
    if a.vendor == "amd" and re.search(r"\b(B550|A520|TRX40|X570|B650|X670|A620|B850|X870)", board):
        bq["SetupVirtualMap"] = False
        plan.explanations.append(("Booter", "SetupVirtualMap is off: required on B550/A520/X570/AM5 boards"))
    if "TRX40" in board:
        bq["DevirtualiseMmio"] = True
    plan.booter_quirks = bq

    kq = {"DisableLinkeditJettison": True, "PanicNoKextDump": True, "PowerTimeoutKernelPanic": True,
          "XhciPortLimit": M.vkey(version) <= M.vkey("10.15")}
    kq.update(plan.kernel_quirks)
    if intel and p != "vm":
        locked = cpu.get("cfg_lock")
        if locked is not False:
            if p == "ivybridge":
                kq["AppleCpuPmCfgLock"] = True
            kq["AppleXcpmCfgLock"] = True
            plan.explanations.append(("Kernel", "CFG Lock is on (or unknown): AppleXcpmCfgLock works around it. "
                                      "Disabling 'CFG Lock' in BIOS is better if the option exists."))
        kq["DisableIoMapper"] = True
        if "HP" in sys_vendor.split() or sys_vendor.startswith("HEWLETT"):
            kq["LapicKernelPanic"] = True
    if p in ("alderlake", "raptorlake", "amd-zen", "vm"):
        kq["ProvideCurrentCpuInfo"] = True
    plan.kernel_quirks = kq

    if p in ("rocketlake", "alderlake", "raptorlake"):
        plan.emulate = {"Cpuid1Data": _hex("55060A00000000000000000000000000"),
                        "Cpuid1Mask": _hex("FFFFFFFF000000000000000000000000")}
        plan.explanations.append(("Kernel", "The CPU is presented to macOS as a Comet Lake (CPUID spoof)."))

    uq = {"RequestBootVarRouting": True, "ReleaseUsbOwnership": False,
          "IgnoreInvalidFlexRatio": p in ("ivybridge", "haswell", "broadwell")}
    if "HP" in sys_vendor.split():
        uq["UnblockFsConnect"] = True
    plan.uefi_quirks = uq
    plan.acpi_quirks = {"ResetLogoStatus": True, "FadtEnableReset": False, "NormalizeHeaders": False,
                        "RebaseRegions": False, "ResetHwSig": False, "SyncTableIds": False}

    if a.vendor == "amd":       # also in VMs: the guest still sees the host's AMD CPU
        plan.amd_patches = True
        plan.cores = cpu.get("cores") or 0
        plan.explanations.append(("Kernel", f"AMD_Vanilla kernel patches with the core count set to {plan.cores}."))
    if a.vendor == "amd" or p == "vm":
        # Intel power management (AppleIntelCPUPowerManagement) must not attach to AMD or virtual CPUs
        # (Dortania AMD config: Kernel -> Emulate -> DummyPowerManagement; OSX-KVM does the same)
        plan.emulate["DummyPowerManagement"] = True
        plan.explanations.append(("Kernel", "DummyPowerManagement: stops Apple's Intel power management "
                                  "from loading on this CPU"))

    # ------------------------------------------------------------- drivers / misc
    plan.drivers = [Item("OpenRuntime.efi", "Core OpenCore runtime services (mandatory)"),
                    Item("OpenHfsPlus.efi", "Lets OpenCore see HFS+ volumes such as the macOS recovery"),
                    Item("ResetNvramEntry.efi", "Adds 'Reset NVRAM' to the boot menu")]
    if not plan.apfs_jumpstart:
        plan.drivers.append(Item("apfs_aligned.efi", "Apple's APFS driver with acidanthera's W^X fix "
                                 "(needed to see macOS Tahoe disks)"))
    plan.apfs_legacy = M.vkey(version) < M.vkey("11")
    plan.secure_boot_model = "Disabled" if (M.vkey(version) <= M.vkey("10.13") or any(
        g.support.info.get("gen") == "pascal-maxwell" for g in usable_gpus)) else "Default"
    base = ["-v", "keepsyms=1", "debug=0x100"]
    plan.boot_args = base + [x for x in plan.boot_args if x not in base]
    plan.explanations.append(("NVRAM", "-v shows the boot text instead of the Apple logo: if something goes wrong "
                              "you can see (and photograph) where it stops."))

    nics = [n for n in report.get("nics", []) if not n.get("wireless") and n.get("mac")]
    plan.rom_mac = nics[0]["mac"] if nics else None

    # ------------------------------------------------------------- warnings from analysis
    plan.warnings.extend(verdict.caveats)
    airport = any(k.name == "AirportItlwm" for k in plan.kexts)
    for comp in a.components:
        if comp.category == "Wi-Fi" and airport:
            continue
        plan.warnings.extend(n for n in comp.support.notes if comp.support.usable and n not in plan.warnings)
    if airport:
        plan.warnings.append("Intel Wi-Fi uses AirportItlwm: it works inside the installer, join your network "
                             "from the Wi-Fi menu at the top right.")
    for k in plan.kexts:
        src = CATALOG.get(k.name)
        if src and src.manual:
            plan.warnings.append(f"{k.name} cannot be downloaded automatically: add it by hand to EFI/OC/Kexts "
                                 "and run the build again (or use a USB network adapter).")
    return plan


# =========================================================================== expectations

@dataclass
class Expectations:
    works: list = field(default_factory=list)       # [(title, detail)]
    uncertain: list = field(default_factory=list)
    broken: list = field(default_factory=list)

    def add(self, bucket, title, detail=""):
        lst = getattr(self, bucket)
        if title not in [t for t, _ in lst]:
            lst.append((title, detail))


CATEGORY_ORDER = ["CPU", "Graphics", "Audio", "Ethernet", "Wi-Fi", "Bluetooth", "Webcam", "USB", "Storage",
                  "Input", "Card reader", "Thunderbolt", "Fingerprint"]


def expectations(a: Analysis, version: str, plan: Optional[Plan] = None) -> Expectations:
    """What will work, what is uncertain and what will not, for this hardware on this macOS."""
    plan = plan or make_plan(a, version)
    mac = M.get(version)
    e = Expectations()
    kexts = {k.name for k in plan.kexts}
    comps = sorted(a.components, key=lambda c: CATEGORY_ORDER.index(c.category)
                   if c.category in CATEGORY_ORDER else 99)
    for c in comps:
        s = c.support
        title = f"{c.category}: {s.summary}"
        detail = " ".join(s.notes)
        if not s.usable:
            e.add("broken", title, detail)
        elif c.category == "Audio" and s.info.get("analog") and "SimpleHDA" in kexts:
            e.add("uncertain", title, "macOS Tahoe has no AppleHDA: SimpleHDA (experimental, third-party) "
                                      "brings it back without patching macOS.")
        elif not _in_range(s, version):
            e.add("broken", title, f"Only works on macOS {s.min_macos} – {s.max_macos}.")
        elif c.category == "Wi-Fi" and "AirportItlwm" in kexts and s.kexts == ["itlwm"]:
            e.add("works", title, "AirportItlwm: appears as a normal Wi-Fi card, also inside the installer.")
        elif c.category == "Wi-Fi" and "itlwm" in kexts:
            e.add("uncertain", title, "Works after installing the HeliPort app (not out-of-the-box); "
                                      "not available inside the installer.")
        elif any(k in kexts for k in s.kexts) and any(CATALOG.get(k) and CATALOG[k].manual for k in s.kexts):
            e.add("uncertain", title, "Its driver cannot be downloaded automatically: it must be added by hand.")
        elif s.status == "experimental" or s.info.get("ports", 0) > 15:
            e.add("uncertain", title, detail)
        else:
            e.add("works", title, detail if c.category != "CPU" else "")

    gpus = [g for g in a.by_category("Graphics") if _in_range(g.support, version)]
    amd_dgpu = any(g.support.info.get("vendor") == "amd" and g.support.info.get("kind") == "dgpu" for g in gpus)
    igpu_only = gpus and all(g.support.info.get("kind") == "igpu" for g in gpus)
    if a.virtual:
        e.add("broken", "Graphics acceleration", "Virtual machines run macOS without GPU acceleration: "
                                                 "animations and video will be slow.")
    if igpu_only and not a.virtual:
        if a.laptop:
            e.add("uncertain", "Built-in screen and external outputs",
                  "Laptop iGPU settings are a best guess: brightness, HDMI or the internal screen may need tuning.")
        else:
            e.add("uncertain", "HDMI / DisplayPort outputs of the integrated graphics",
                  "Some motherboard video ports need framebuffer connector patches; if a port stays black, "
                  "try another one.")
        e.add("broken", "DRM streaming (Apple TV+, Netflix and Prime in Safari)",
              "Broken on Intel iGPUs since macOS Catalina (Dortania GPU guide). Chrome/Firefox work.")
    elif amd_dgpu:
        e.add("uncertain", "DRM streaming (Apple TV+, Netflix in Safari)",
              "Usually works with an AMD card, depending on the Mac model and macOS version.")

    native_brcm = any(c.category == "Wi-Fi" and c.support.status == "native" and _in_range(c.support, version)
                      for c in a.components)
    if not native_brcm and not a.virtual:
        e.add("broken", "AirDrop, Handoff, Continuity, Apple Watch unlock",
              "Need an Apple/Broadcom Wi-Fi + Bluetooth card that macOS supports natively.")
    if not a.virtual:
        e.add("uncertain", "Sleep and wake",
              "Often needs a USB port map and some testing after installing.")
        e.add("uncertain", "iMessage and FaceTime",
              "Usually work with the generated serial number; if they don't, follow Dortania's "
              "'Fixing iServices' guide.")
    if mac.version == "26" and amd_dgpu:
        e.add("uncertain", "AMD graphics on macOS Tahoe",
              "WhateverGreen is not used on Tahoe; multi-monitor setups may need extra connector work.")
    if a.vendor == "amd" and not a.virtual:
        e.add("broken", "Apps that need Intel-only features",
              "Docker Desktop, Hypervisor.framework VMs and some Adobe apps do not run on AMD CPUs.")
    if not a.laptop and not a.virtual:
        e.add("works", "USB keyboards and mice")
    return e


def expectations_text(e: Expectations) -> str:
    lines = []
    for label, bucket in (("WORKS", e.works), ("UNCERTAIN", e.uncertain), ("WILL NOT WORK", e.broken)):
        lines.append(f"{label}:")
        lines += [f"  - {t}" + (f"  ({d})" if d else "") for t, d in bucket] or ["  (nothing)"]
    return "\n".join(lines)


def plan_summary(plan: Plan) -> str:
    lines = [f"Target: {plan.macos.label}", f"SMBIOS: {plan.smbios}  ({plan.smbios_reason})", "", "Kexts:"]
    lines += [f"  - {k.name}: {k.reason}" for k in plan.kexts]
    lines += ["", "SSDTs:"] + [f"  - {s.name}: {s.reason}" for s in plan.ssdts]
    lines += ["", "UEFI drivers:"] + [f"  - {d.name}: {d.reason}" for d in plan.drivers]
    lines += ["", f"boot-args: {' '.join(plan.boot_args)}", f"SecureBootModel: {plan.secure_boot_model}"]
    if plan.device_properties:
        lines += ["", "DeviceProperties:"]
        for path, props in plan.device_properties.items():
            lines.append(f"  {path}")
            lines += [f"    {k} = {v.hex().upper() if isinstance(v, bytes) else v}" for k, v in props.items()]
    if plan.explanations:
        lines += ["", "Why:"] + [f"  [{t}] {x}" for t, x in plan.explanations]
    if plan.warnings:
        lines += ["", "Warnings:"] + [f"  ! {w}" for w in plan.warnings]
    return "\n".join(lines)
