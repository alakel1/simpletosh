"""Hardware detection for Linux (live ISO or installed system).

Reads /proc and /sys directly so it works in a minimal live environment.
`lspci` is used only (optionally) to get friendly device names.
The result is a plain JSON-serialisable dict ("hardware report") so it can be
saved, shared and re-loaded for testing on another machine.
"""

import glob
import json
import os
import platform
import re
import struct
import subprocess

LAPTOP_CHASSIS = {8, 9, 10, 11, 14, 30, 31, 32}

PCI_CLASS = {
    "0300": "display", "0302": "display", "0380": "display",
    "0200": "ethernet", "0280": "wifi",
    "0403": "audio", "0401": "audio",
    "0601": "isa",
    "0108": "nvme", "0106": "sata", "0104": "raid",
    "0c03": "usb", "0c0a": "thunderbolt", "0805": "cardreader",
}
# Realtek / O2Micro / Genesys PCIe card readers often report class ff00 or 0880
CARDREADER_IDS = {("10ec", d) for d in ("5227", "5228", "5229", "522a", "5249", "524a", "525a", "5260",
                                         "5261", "5286", "5287", "5289")} | {("1217", "8621"), ("1217", "8520"),
                                                                             ("17a0", "9755"), ("17a0", "9750")}
THUNDERBOLT_NHI = {"15d2", "15d9", "15e8", "15eb", "1137", "1575", "1577", "15bf", "15dc", "15dd", "15de",
                   "8a0d", "8a17", "9a1b", "9a1d", "9a1f", "9a21", "463e", "466d", "a73e", "a76d", "7ec2", "7ec3"}


def _read(path, default=None, binary=False):
    try:
        with open(path, "rb" if binary else "r") as f:
            return f.read() if binary else f.read().strip()
    except (OSError, UnicodeDecodeError):
        return default


def _lspci_names():
    names = {}
    try:
        out = subprocess.run(["lspci", "-D", "-mm", "-nn"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return names
    for line in out.splitlines():
        parts = re.findall(r'"([^"]*)"|(\S+)', line)
        fields = [a or b for a, b in parts]
        if len(fields) >= 4:
            vendor = re.sub(r"\s*\[[0-9a-f]{4}\]$", "", fields[2])
            device = re.sub(r"\s*\[[0-9a-f]{4}\]$", "", fields[3])
            vendor = vendor.replace("Corporation", "").replace("Co., Ltd.", "").replace(", Inc.", "").strip()
            names[fields[0]] = f"{vendor} {device}".strip()
    return names


# ------------------------------------------------------------------ CPU

def detect_cpu():
    info = {}
    text = _read("/proc/cpuinfo", "")
    first = text.split("\n\n")[0]
    for line in first.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    vendor = {"GenuineIntel": "intel", "AuthenticAMD": "amd"}.get(info.get("vendor_id"), info.get("vendor_id"))
    cores = set()
    threads = 0
    phys = core = None
    for line in text.splitlines():
        if line.startswith("processor"):
            threads += 1
        elif line.startswith("physical id"):
            phys = line.split(":")[1].strip()
        elif line.startswith("core id"):
            core = line.split(":")[1].strip()
            cores.add((phys, core))
    wanted = {"sse4_1", "sse4_2", "avx", "avx2", "avx512f", "hypervisor", "vmx", "svm"}
    flags = sorted(wanted & set(info.get("flags", "").split()))
    return {
        "vendor": vendor,
        "name": re.sub(r"\s+", " ", info.get("model name", "Unknown CPU")),
        "family": int(info.get("cpu family", 0)),
        "model": int(info.get("model", 0)),
        "stepping": int(info.get("stepping", 0) or 0),
        "cores": len(cores) or int(info.get("cpu cores", 0) or 0) or threads,
        "threads": threads,
        "flags": flags,
        "cfg_lock": detect_cfg_lock(vendor),
    }


def detect_cfg_lock(vendor):
    """Intel MSR 0xE2 bit 15 = CFG Lock. Needs root + msr module. None if unknown."""
    if vendor != "intel":
        return None
    path = "/dev/cpu/0/msr"
    if not os.path.exists(path):
        subprocess.run(["modprobe", "msr"], capture_output=True)
    try:
        with open(path, "rb") as f:
            f.seek(0xE2)
            val = struct.unpack("<Q", f.read(8))[0]
        return bool(val & (1 << 15))
    except OSError:
        return None


# ------------------------------------------------------------------ machine

def detect_machine():
    dmi = "/sys/class/dmi/id"
    chassis = _read(f"{dmi}/chassis_type")
    try:
        chassis = int(chassis)
    except (TypeError, ValueError):
        chassis = None
    batteries = [p for p in glob.glob("/sys/class/power_supply/*")
                 if _read(f"{p}/type") == "Battery" and _read(f"{p}/scope") != "Device"]
    sys_vendor = _read(f"{dmi}/sys_vendor", "")
    product = _read(f"{dmi}/product_name", "")
    virtual = None
    blob = f"{sys_vendor} {product}".lower()
    for key, name in (("virtualbox", "virtualbox"), ("innotek", "virtualbox"), ("vmware", "vmware"),
                      ("qemu", "qemu"), ("kvm", "qemu"), ("microsoft corporation virtual", "hyperv"),
                      ("parallels", "parallels")):
        if key in blob:
            virtual = name
            break
    laptop = (chassis in LAPTOP_CHASSIS) or bool(batteries)
    if virtual:
        laptop = False
    sb = None
    for p in glob.glob("/sys/firmware/efi/efivars/SecureBoot-*"):
        raw = _read(p, binary=True)
        if raw and len(raw) >= 5:
            sb = raw[4] == 1
    return {
        "vendor": sys_vendor,
        "product": product,
        "board_vendor": _read(f"{dmi}/board_vendor", ""),
        "board_name": _read(f"{dmi}/board_name", ""),
        "bios_version": _read(f"{dmi}/bios_version", ""),
        "chassis_type": chassis,
        "form_factor": "laptop" if laptop else "desktop",
        "battery": bool(batteries),
        "virtual": virtual,
        "firmware": "uefi" if os.path.isdir("/sys/firmware/efi") else "bios",
        "secure_boot": sb,
        "memory_gb": _memory_gb(),
    }


def _memory_gb():
    m = re.search(r"MemTotal:\s+(\d+)", _read("/proc/meminfo", ""))
    return round(int(m.group(1)) / 1024 / 1024) if m else None


# ------------------------------------------------------------------ PCI

def _device_path(sysfs_dev):
    """UEFI device path like PciRoot(0x0)/Pci(0x1,0x0)/Pci(0x0,0x0)."""
    real = os.path.realpath(sysfs_dev)
    parts = real.split("/")
    try:
        root_idx = next(i for i, p in enumerate(parts) if p.startswith("pci0000:") or re.match(r"pci[0-9a-f]{4}:[0-9a-f]{2}$", p))
    except StopIteration:
        return None
    root = parts[root_idx]
    bus = int(root.split(":")[1], 16)
    nodes = ["PciRoot(0x%x)" % (0 if bus == 0 else bus)]
    for p in parts[root_idx + 1:]:
        m = re.match(r"[0-9a-f]{4}:[0-9a-f]{2}:([0-9a-f]{2})\.([0-7])$", p)
        if not m:
            break
        nodes.append("Pci(0x%x,0x%x)" % (int(m.group(1), 16), int(m.group(2))))
    return "/".join(nodes)


def _pci_ids_names():
    """Fallback names from the pci.ids database when lspci is missing."""
    for path in ("/usr/share/misc/pci.ids", "/usr/share/hwdata/pci.ids", "/usr/share/pci.ids"):
        if os.path.exists(path):
            vendors, devices, cur = {}, {}, None
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("C "):
                        break
                    if not line.strip() or line.startswith("#"):
                        continue
                    if not line.startswith("\t"):
                        cur = line[:4].lower()
                        vendors[cur] = line[6:].strip()
                    elif not line.startswith("\t\t") and cur:
                        devices[(cur, line[1:5].lower())] = line[7:].strip()
            return vendors, devices
    return {}, {}


def _ata_ports_by_slot():
    counts = {}
    for p in glob.glob("/sys/class/ata_port/ata*"):
        real = os.path.realpath(p)
        m = re.findall(r"([0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7])", real)
        if m:
            counts[m[-1]] = counts.get(m[-1], 0) + 1
    return counts


def detect_pci():
    names = _lspci_names()
    vendors = devnames = None
    if not names:
        vendors, devnames = _pci_ids_names()
    ata = _ata_ports_by_slot()
    devices = []
    for d in sorted(glob.glob("/sys/bus/pci/devices/*")):
        slot = os.path.basename(d)
        cls = (_read(f"{d}/class", "0x000000") or "0x000000")[2:]
        drv = os.path.realpath(f"{d}/driver")
        vendor = (_read(f"{d}/vendor", "0x0000"))[2:]
        device = (_read(f"{d}/device", "0x0000"))[2:]
        kind = PCI_CLASS.get(cls[:4])
        if (vendor, device) in CARDREADER_IDS:
            kind = "cardreader"
        elif vendor == "8086" and device in THUNDERBOLT_NHI:
            kind = "thunderbolt"
        name = names.get(slot)
        if not name and vendors is not None:
            name = " ".join(x for x in (vendors.get(vendor), devnames.get((vendor, device))) if x) or None
        devices.append({
            "slot": slot,
            "vendor": vendor,
            "device": device,
            "subsys_vendor": (_read(f"{d}/subsystem_vendor", "0x0000"))[2:],
            "subsys_device": (_read(f"{d}/subsystem_device", "0x0000"))[2:],
            "class": cls,
            "kind": kind,
            "name": name,
            "driver": os.path.basename(drv) if os.path.exists(f"{d}/driver") else None,
            "path": _device_path(d),
            "boot_vga": _read(f"{d}/boot_vga") == "1",
            "ata_ports": ata.get(slot),
        })
    return devices


# ------------------------------------------------------------------ USB

def detect_usb():
    """USB devices (with their interface classes) and, separately, root-hub port counts."""
    out = []
    for d in sorted(glob.glob("/sys/bus/usb/devices/*")):
        vid = _read(f"{d}/idVendor")
        if not vid or vid == "1d6b":        # 1d6b = Linux root hubs
            continue
        name = os.path.basename(d)
        ifaces = sorted({_read(f"{i}/bInterfaceClass", "") for i in glob.glob(f"{d}/{name}:*")} - {""})
        out.append({
            "vendor": vid,
            "product": _read(f"{d}/idProduct", ""),
            "name": " ".join(x for x in (_read(f"{d}/manufacturer", ""), _read(f"{d}/product", "")) if x),
            "class": _read(f"{d}/bDeviceClass", ""),
            "interfaces": ifaces,
            "internal": _read(f"{d}/removable") == "fixed",
        })
    return out


def detect_usb_ports():
    """Ports per USB controller (PCI slot): root hubs of the same controller are summed.

    macOS limits each controller to 15 ports; USB 2 and USB 3 halves of one physical
    connector count as two ports.
    """
    ports = {}
    for d in glob.glob("/sys/bus/usb/devices/usb*"):
        real = os.path.realpath(d)
        m = re.findall(r"([0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7])", real)
        if not m:
            continue
        n = int(_read(f"{d}/maxchild", "0") or 0)
        ports[m[-1]] = ports.get(m[-1], 0) + n
    return ports


# ------------------------------------------------------------------ audio

def detect_audio_codecs():
    codecs = []
    for path in sorted(glob.glob("/proc/asound/card*/codec#*")):
        text = _read(path, "")
        name = re.search(r"^Codec:\s*(.+)$", text, re.M)
        vid = re.search(r"^Vendor Id:\s*(0x[0-9a-f]+)", text, re.M)
        if not name:
            continue
        name = name.group(1).strip()
        if re.search(r"HDMI|DisplayPort|Nvidia|ATI|AMD.*HDMI|Intel.*(Kabylake|Skylake|Haswell|Broadwell|Coffeelake|Cometlake|Icelake|Tigerlake|Alderlake)", name, re.I):
            continue    # digital outputs, handled by the GPU
        card = re.search(r"card(\d+)", path).group(1)
        codecs.append({"card": int(card), "name": name, "vendor_id": vid.group(1) if vid else None,
                       "applealc": applealc_name(name)})
    return codecs


def applealc_name(codec_name: str):
    """'Realtek ALC892' -> 'ALC892'; 'Conexant CX20751/2' -> 'CX20751_2'."""
    m = re.search(r"\b(ALC\d{3,4}[A-Z]?|CX\d{4,5}(?:/\d)?|AD\d{4}[AB]?|IDT\d+|VT\d{4}\w*|CS\d{4})\b", codec_name)
    if not m:
        return None
    return m.group(1).replace("/", "_")


# ------------------------------------------------------------------ input / ACPI

def detect_input():
    hids = set()
    for d in glob.glob("/sys/bus/acpi/devices/*"):
        hid = _read(f"{d}/hid", "")
        status = _read(f"{d}/status", "15")
        if hid and status not in ("0",):
            hids.add(hid)
    i2c_hid = any(h in hids for h in ("PNP0C50", "ACPI0C50")) or bool(glob.glob("/sys/bus/i2c/drivers/i2c_hid*/*-*"))
    ps2 = bool(glob.glob("/sys/bus/serio/devices/serio*"))
    return {"ps2": ps2, "i2c_hid": i2c_hid,
            "elan_or_synaptics_smbus": any("elan" in (_read(f"{d}/name", "") or "").lower()
                                           for d in glob.glob("/sys/bus/i2c/devices/*"))}


ACPI_NAMES = ["AWAC", "STAS", "RTC_", "PMCR", "LPCB", "LPC0", "LPC_", "SBRG", "EC__", "EC0_", "H_EC",
              "ECDV", "PGEC", "PR00", "CP00", "CPU0", "C000", "P000", "PNLF", "GPI0", "XOSI", "HPET"]


def detect_acpi():
    raw = _read("/sys/firmware/acpi/tables/DSDT", binary=True)
    if raw is None:
        return {"dsdt_available": False, "names": []}
    found = [n for n in ACPI_NAMES if n.encode() in raw]
    return {"dsdt_available": True, "names": found, "oem": raw[10:16].decode(errors="replace").strip()}


# ------------------------------------------------------------------ network

def detect_nics():
    nics = []
    for d in glob.glob("/sys/class/net/*"):
        dev = os.path.realpath(f"{d}/device")
        if not os.path.exists(f"{d}/device"):
            continue
        nics.append({"iface": os.path.basename(d), "mac": _read(f"{d}/address"),
                     "pci_slot": os.path.basename(dev), "wireless": os.path.isdir(f"{d}/wireless")})
    return sorted(nics, key=lambda n: n["iface"])


# ------------------------------------------------------------------ all

def detect():
    report = {
        "schema": 1,
        "host": platform.node(),
        "machine": detect_machine(),
        "cpu": detect_cpu(),
        "pci": detect_pci(),
        "usb": detect_usb(),
        "usb_ports": detect_usb_ports(),
        "thunderbolt": bool(glob.glob("/sys/bus/thunderbolt/devices/domain*")),
        "audio_codecs": detect_audio_codecs(),
        "input": detect_input(),
        "acpi": detect_acpi(),
        "nics": detect_nics(),
    }
    return report


def load(path):
    with open(path) as f:
        return json.load(f)


def save(report, path):
    with open(path, "w") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    print(json.dumps(detect(), indent=2))
