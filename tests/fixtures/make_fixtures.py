"""Generate synthetic hardware reports for machines we don't have at hand.

Run: python3 tests/fixtures/make_fixtures.py   (writes *.json next to this file)
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def pci(slot, vendor, device, kind, name, path, cls=None):
    classes = {"display": "030000", "ethernet": "020000", "wifi": "028000", "audio": "040300",
               "isa": "060100", "nvme": "010802", "sata": "010601", "usb": "0c0330", "cardreader": "ff0000"}
    return {"slot": slot, "vendor": vendor, "device": device, "subsys_vendor": "0000", "subsys_device": "0000",
            "class": cls or classes[kind], "kind": kind, "name": name, "driver": None, "path": path,
            "boot_vga": False}


def machine(vendor, product, board, form="desktop", virtual=None, chassis=3):
    return {"vendor": vendor, "product": product, "board_vendor": vendor, "board_name": board,
            "bios_version": "1.0", "chassis_type": chassis, "form_factor": form, "battery": form == "laptop",
            "virtual": virtual, "firmware": "uefi", "secure_boot": False, "memory_gb": 16}


def cpu(vendor, name, family, model, stepping, cores, flags=("avx", "avx2", "sse4_1", "sse4_2"), cfg_lock=None):
    return {"vendor": vendor, "name": name, "family": family, "model": model, "stepping": stepping,
            "cores": cores, "threads": cores * 2, "flags": list(flags), "cfg_lock": cfg_lock}


def report(m, c, devs, usb=(), codecs=(), inp=None, acpi=None, nics=(), usb_ports=None):
    return {"schema": 1, "host": "fixture", "machine": m, "cpu": c, "pci": list(devs), "usb": list(usb),
            "usb_ports": usb_ports or {}, "thunderbolt": False,
            "audio_codecs": list(codecs), "input": inp or {"ps2": False, "i2c_hid": False},
            "acpi": acpi or {"dsdt_available": False, "names": []}, "nics": list(nics)}


ALC = lambda n: {"card": 0, "name": f"Realtek ALC{n}", "vendor_id": f"0x10ec0{n}", "applealc": f"ALC{n}"}

FIXTURES = {
    # i7-8700K, Z390, UHD 630 only, I219-V, ALC1220
    "intel-coffeelake-z390-igpu": report(
        machine("Gigabyte", "Z390 AORUS PRO", "Z390 AORUS PRO"),
        cpu("intel", "Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz", 6, 0x9E, 10, 6, cfg_lock=True),
        [pci("0000:00:02.0", "8086", "3e92", "display", "Intel UHD Graphics 630", "PciRoot(0x0)/Pci(0x2,0x0)"),
         pci("0000:00:1f.0", "8086", "a305", "isa", "Intel Z390 Chipset LPC/eSPI Controller", "PciRoot(0x0)/Pci(0x1f,0x0)"),
         pci("0000:00:1f.6", "8086", "15bc", "ethernet", "Intel Ethernet Connection (7) I219-V", "PciRoot(0x0)/Pci(0x1f,0x6)"),
         pci("0000:01:00.0", "144d", "a804", "nvme", "Samsung 970 EVO", "PciRoot(0x0)/Pci(0x1d,0x0)/Pci(0x0,0x0)")],
        codecs=[ALC(1220)],
        acpi={"dsdt_available": True, "names": ["AWAC", "STAS", "LPCB", "PR00"]},
        nics=[{"iface": "eno1", "mac": "aa:bb:cc:dd:ee:01", "pci_slot": "0000:00:1f.6", "wireless": False}]),

    # i5-10400 + RX 580 + RTL8125, B460
    "intel-cometlake-rx580": report(
        machine("ASUS", "PRIME B460-PLUS", "PRIME B460-PLUS"),
        cpu("intel", "Intel(R) Core(TM) i5-10400 CPU @ 2.90GHz", 6, 0xA5, 3, 6),
        [pci("0000:00:02.0", "8086", "9bc8", "display", "Intel UHD Graphics 630", "PciRoot(0x0)/Pci(0x2,0x0)"),
         pci("0000:01:00.0", "1002", "67df", "display", "AMD Ellesmere [Radeon RX 470/480/570/580]",
             "PciRoot(0x0)/Pci(0x1,0x0)/Pci(0x0,0x0)"),
         pci("0000:00:1f.0", "8086", "a3c8", "isa", "Intel B460 Chipset LPC", "PciRoot(0x0)/Pci(0x1f,0x0)"),
         pci("0000:03:00.0", "10ec", "8125", "ethernet", "Realtek RTL8125 2.5GbE", "PciRoot(0x0)/Pci(0x1c,0x0)/Pci(0x0,0x0)")],
        codecs=[ALC(887)]),

    # i5-12600K + RX 6600 + I225-V + AX201 + Intel BT
    "intel-alderlake-rx6600": report(
        machine("MSI", "PRO Z690-A", "PRO Z690-A (MS-7D25)"),
        cpu("intel", "12th Gen Intel(R) Core(TM) i5-12600K", 6, 0x97, 2, 10),
        [pci("0000:00:02.0", "8086", "4680", "display", "Intel AlderLake-S GT1", "PciRoot(0x0)/Pci(0x2,0x0)"),
         pci("0000:03:00.0", "1002", "73ff", "display", "AMD Navi 23 [Radeon RX 6600]",
             "PciRoot(0x0)/Pci(0x1,0x0)/Pci(0x0,0x0)/Pci(0x0,0x0)/Pci(0x0,0x0)"),
         pci("0000:00:1f.0", "8086", "7a84", "isa", "Intel Z690 Chipset LPC/eSPI Controller", "PciRoot(0x0)/Pci(0x1f,0x0)"),
         pci("0000:04:00.0", "8086", "15f3", "ethernet", "Intel Ethernet Controller I225-V", "PciRoot(0x0)/Pci(0x1c,0x0)/Pci(0x0,0x0)"),
         pci("0000:00:14.3", "8086", "7af0", "wifi", "Intel Alder Lake-S PCH CNVi WiFi", "PciRoot(0x0)/Pci(0x14,0x3)"),
         pci("0000:00:14.0", "8086", "7ae0", "usb", "Intel Alder Lake-S PCH USB 3.2 xHCI", "PciRoot(0x0)/Pci(0x14,0x0)", cls="0c0330"),
         pci("0000:03:00.1", "1002", "ab28", "audio", "AMD Navi 21/23 HDMI/DP Audio", "PciRoot(0x0)/Pci(0x1,0x0)/Pci(0x0,0x0)/Pci(0x0,0x0)/Pci(0x0,0x1)")],
        usb=[{"vendor": "8087", "product": "0026", "name": "Intel Bluetooth", "class": "e0", "interfaces": ["e0"]},
             {"vendor": "0bda", "product": "b00a", "name": "Realtek Bluetooth Radio", "class": "e0", "interfaces": ["e0"]}],
        codecs=[ALC(897)], usb_ports={"0000:00:14.0": 26}),

    # i5-8250U laptop, UHD 620 (KBL-R), I2C touchpad, Intel 8265 Wi-Fi, ALC256
    "laptop-kabylake-r": report(
        machine("Dell Inc.", "XPS 13 9370", "0F6P3V", form="laptop", chassis=10),
        cpu("intel", "Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz", 6, 0x8E, 10, 4),
        [pci("0000:00:02.0", "8086", "5917", "display", "Intel UHD Graphics 620", "PciRoot(0x0)/Pci(0x2,0x0)"),
         pci("0000:00:1f.0", "8086", "9d4e", "isa", "Intel Sunrise Point LPC", "PciRoot(0x0)/Pci(0x1f,0x0)"),
         pci("0000:02:00.0", "8086", "24fd", "wifi", "Intel Wireless 8265 / 8275", "PciRoot(0x0)/Pci(0x1c,0x0)/Pci(0x0,0x0)"),
         pci("0000:00:14.0", "8086", "9d2f", "usb", "Intel Sunrise Point-LP USB 3.0 xHCI", "PciRoot(0x0)/Pci(0x14,0x0)", cls="0c0330"),
         pci("0000:03:00.0", "10ec", "525a", "cardreader", "Realtek RTS525A PCI Express Card Reader",
             "PciRoot(0x0)/Pci(0x1c,0x4)/Pci(0x0,0x0)", cls="ff0000")],
        usb=[{"vendor": "8087", "product": "0a2b", "name": "Intel Bluetooth", "class": "e0", "interfaces": ["e0"]},
             {"vendor": "0c45", "product": "6713", "name": "Microdia Integrated_Webcam_HD", "class": "ef",
              "interfaces": ["0e"]},
             {"vendor": "0c45", "product": "6714", "name": "Microdia Integrated IR Camera", "class": "ef",
              "interfaces": ["0e"]},
             {"vendor": "27c6", "product": "5385", "name": "Goodix Fingerprint", "class": "ff", "interfaces": ["ff"]}],
        codecs=[ALC(256)],
        inp={"ps2": True, "i2c_hid": True}),

    # Ryzen 7 5800X + RX 580 on B550 + RTL8111
    "amd-ryzen5800x-b550-rx580": report(
        machine("Micro-Star", "MAG B550 TOMAHAWK", "MAG B550 TOMAHAWK (MS-7C91)"),
        cpu("amd", "AMD Ryzen 7 5800X 8-Core Processor", 0x19, 0x21, 0, 8),
        [pci("0000:2d:00.0", "1002", "67df", "display", "AMD Radeon RX 580", "PciRoot(0x0)/Pci(0x3,0x1)/Pci(0x0,0x0)"),
         pci("0000:00:14.3", "1022", "790e", "isa", "AMD FCH LPC Bridge", "PciRoot(0x0)/Pci(0x14,0x3)"),
         pci("0000:2a:00.0", "10ec", "8168", "ethernet", "Realtek RTL8111", "PciRoot(0x0)/Pci(0x1,0x2)/Pci(0x0,0x0)")],
        codecs=[ALC(1220)]),

    # VirtualBox guest on an Intel host
    "virtualbox-intel": report(
        machine("innotek GmbH", "VirtualBox", "VirtualBox", virtual="virtualbox", chassis=1),
        cpu("intel", "Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz", 6, 0x9E, 13, 4, flags=("avx", "avx2", "sse4_1", "sse4_2", "hypervisor")),
        [pci("0000:00:02.0", "15ad", "0405", "display", "VMware SVGA II Adapter", "PciRoot(0x0)/Pci(0x2,0x0)"),
         pci("0000:00:03.0", "8086", "100f", "ethernet", "Intel 82545EM Gigabit Ethernet", "PciRoot(0x0)/Pci(0x3,0x0)"),
         pci("0000:00:1f.2", "8086", "2829", "sata", "Intel 82801HM AHCI", "PciRoot(0x0)/Pci(0x1f,0x2)")]),

    # i7-4790 + GTX 1660 (unsupported) + HD 4600
    "intel-haswell-gtx1660": report(
        machine("Dell Inc.", "OptiPlex 9020", "0PC5F7"),
        cpu("intel", "Intel(R) Core(TM) i7-4790 CPU @ 3.60GHz", 6, 0x3C, 3, 4, cfg_lock=False),
        [pci("0000:00:02.0", "8086", "0412", "display", "Intel Xeon E3-1200 v3/4th Gen Core Processor Integrated Graphics",
             "PciRoot(0x0)/Pci(0x2,0x0)"),
         pci("0000:01:00.0", "10de", "2184", "display", "NVIDIA TU116 [GeForce GTX 1660]", "PciRoot(0x0)/Pci(0x1,0x0)/Pci(0x0,0x0)"),
         pci("0000:00:19.0", "8086", "153a", "ethernet", "Intel Ethernet Connection I217-LM", "PciRoot(0x0)/Pci(0x19,0x0)")],
        codecs=[ALC(280)]),
}

if __name__ == "__main__":
    for name, rep in FIXTURES.items():
        with open(os.path.join(HERE, f"{name}.json"), "w") as f:
            json.dump(rep, f, indent=2)
        print("wrote", name)
