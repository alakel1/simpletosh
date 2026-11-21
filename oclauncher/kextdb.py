"""Device support read from the kexts themselves.

Driver kexts declare which hardware they attach to in Contents/Info.plist
(IOKitPersonalities): PCI IDs via IOPCIMatch / IOPCIPrimaryMatch / IONameMatch,
USB devices via idVendor/idProduct. This is exactly what macOS uses to decide
whether to load a driver, so it is the most reliable source we have.

A snapshot ships in data/kext_index.json (regenerate: tools/update_kext_index.py).
At runtime, refresh() rebuilds it from the latest kext downloads when online.
"""

import json
import os
import plistlib
import re
import time

from . import fetch

# kext name -> what kind of device it drives (only kexts whose Info.plist
# lists concrete IDs; itlwm matches every Intel device so it can't be indexed)
INDEXED = {
    "IntelMausi": "ethernet", "RealtekRTL8111": "ethernet", "LucyRTL8125Ethernet": "ethernet",
    "AtherosE2200Ethernet": "ethernet", "AppleIGC": "ethernet",
    "AirportBrcmFixup": "wifi",
    "IntelBluetoothFirmware": "bluetooth", "BrcmPatchRAM3": "bluetooth",
}

SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "kext_index.json")
RUNTIME = os.path.join(fetch.CACHE, "meta", "kext_index.json")


def _ints(text):
    return [int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]+", text)]


def parse_info_plist(path):
    """Return {"pci": [[value, mask], ...], "usb": [[vid, pid], ...]} from an Info.plist."""
    with open(path, "rb") as f:
        info = plistlib.load(f)
    pci, usb = [], []
    for pers in info.get("IOKitPersonalities", {}).values():
        for key in ("IOPCIPrimaryMatch", "IOPCIMatch"):
            for token in str(pers.get(key, "")).split():
                parts = _ints(token)
                if parts:
                    pci.append([parts[0], parts[1] if len(parts) > 1 else 0xFFFFFFFF])
        names = pers.get("IONameMatch")
        if names:
            for n in names if isinstance(names, list) else [names]:
                m = re.match(r"pci([0-9a-f]{4}),([0-9a-f]{1,4})$", str(n), re.I)
                if m:
                    pci.append([(int(m.group(2), 16) << 16) | int(m.group(1), 16), 0xFFFFFFFF])
        if "idVendor" in pers and "idProduct" in pers:
            usb.append([int(pers["idVendor"]), int(pers["idProduct"])])
    return {"pci": pci, "usb": usb}


def build(progress=lambda m, f=None: None):
    """Download every indexed kext (cached) and read its match table."""
    index = {"generated": time.strftime("%Y-%m-%d"), "kexts": {}}
    for name, kind in INDEXED.items():
        try:
            bundle = fetch.kext_bundles(name, None, progress)[0]
            data = parse_info_plist(os.path.join(bundle, "Contents", "Info.plist"))
            data["kind"] = kind
            index["kexts"][name] = data
        except (fetch.FetchError, OSError, plistlib.InvalidFileException) as e:
            progress(f"kext index: {name} skipped ({e})")
    return index


_cache = None


def load():
    """Freshest available index: runtime refresh > shipped snapshot > empty."""
    global _cache
    if _cache is not None:
        return _cache
    for path in (RUNTIME, SNAPSHOT):
        try:
            with open(path) as f:
                _cache = json.load(f)
                return _cache
        except (OSError, ValueError):
            continue
    _cache = {"kexts": {}}
    return _cache


def refresh(max_age=7 * 86400, progress=lambda m, f=None: None):
    """Rebuild the runtime index if older than max_age. Never raises."""
    global _cache
    try:
        if os.path.exists(RUNTIME) and time.time() - os.path.getmtime(RUNTIME) < max_age:
            return load()
        index = build(progress)
        if len(index["kexts"]) >= len(INDEXED) // 2:
            os.makedirs(os.path.dirname(RUNTIME), exist_ok=True)
            with open(RUNTIME, "w") as f:
                json.dump(index, f)
            _cache = index
    except Exception:       # noqa: BLE001 - offline etc.: keep the snapshot
        pass
    return load()


def match_pci(vendor: str, device: str, kind=None, index=None):
    """Kexts whose Info.plist matches PCI vendor:device (hex strings)."""
    ident = (int(device, 16) << 16) | int(vendor, 16)
    out = []
    for name, data in (index or load())["kexts"].items():
        if kind and data.get("kind") != kind:
            continue
        if any((ident & mask) == (value & mask) for value, mask in data.get("pci", [])):
            out.append(name)
    return out


def match_usb(vendor: str, product: str, kind=None, index=None):
    vid, pid = int(vendor, 16), int(product, 16)
    return [name for name, data in (index or load())["kexts"].items()
            if (not kind or data.get("kind") == kind) and [vid, pid] in data.get("usb", [])]
