#!/usr/bin/env python3
"""Read or change the OpenCore config.plist inside a test VM disk, without root.

  tools/vm-config.py show                      [--disk build/qemu/disk.qcow2]
  tools/vm-config.py args serial=3 msgbuf=1048576   add boot-args (prefix with '-' to remove: -- -serial=3)
  tools/vm-config.py set Kernel.Quirks.AppleXcpmCfgLock true

Works on qcow2 (QEMU) and raw images; the VM must be shut down. Uses qemu-img + mtools.
The EFI partition is expected to be the first partition (as written by OpenCore Launcher).
"""

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile

NVRAM = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit(f"{' '.join(cmd)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def esp_offset(raw):
    out = run(["sgdisk" if shutil.which("sgdisk") else "/usr/sbin/sgdisk", "-i", "1", raw])
    for line in out.splitlines():
        if line.startswith("First sector:"):
            return int(line.split()[2]) * 512
    sys.exit("cannot find the EFI partition")


class Image:
    def __init__(self, disk):
        self.disk = disk
        self.qcow = run(["qemu-img", "info", "--output=json", disk]).count('"format": "qcow2"') > 0
        self.tmp = tempfile.mkdtemp(prefix="vmcfg-")
        self.raw = disk
        if self.qcow:
            self.raw = os.path.join(self.tmp, "disk.raw")
            run(["qemu-img", "convert", "-O", "raw", disk, self.raw])     # fails if the VM is running
        self.img = f"{self.raw}@@{esp_offset(self.raw)}"

    def read(self):
        path = os.path.join(self.tmp, "config.plist")
        run(["mcopy", "-n", "-i", self.img, "::/EFI/OC/config.plist", path])
        with open(path, "rb") as f:
            return plistlib.load(f)

    def write(self, config):
        path = os.path.join(self.tmp, "config.plist")
        with open(path, "wb") as f:
            plistlib.dump(config, f)
        run(["mcopy", "-o", "-i", self.img, path, "::/EFI/OC/config.plist"])
        if self.qcow:
            run(["qemu-img", "convert", "-O", "qcow2", self.raw, self.disk + ".new"])
            os.replace(self.disk + ".new", self.disk)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def parse_value(v):
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        return v


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--disk", default=os.path.join(REPO, "build", "qemu", "disk.qcow2"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    a = sub.add_parser("args")
    a.add_argument("args", nargs="+")
    s = sub.add_parser("set")
    s.add_argument("key")
    s.add_argument("value")
    k = sub.add_parser("addkext", help="download a kext from the catalog and add it to the EFI")
    k.add_argument("name")
    o = p.parse_args()

    bundles = []
    if o.cmd == "addkext":
        sys.path.insert(0, REPO)
        from oclauncher import fetch
        bundles = fetch.kext_bundles(o.name)

    img = Image(o.disk)
    try:
        cfg = img.read()
        nv = cfg["NVRAM"]["Add"][NVRAM]
        if o.cmd == "show":
            print("SMBIOS   :", cfg["PlatformInfo"]["Generic"]["SystemProductName"])
            print("boot-args:", nv["boot-args"])
            print("kexts    :", ", ".join(k["BundlePath"] for k in cfg["Kernel"]["Add"] if k["Enabled"]))
            print("patches  :", len(cfg["Kernel"]["Patch"]))
            for sec in ("Booter", "Kernel", "UEFI"):
                on = [k for k, v in cfg[sec]["Quirks"].items() if v is True]
                print(f"{sec:9}:", ", ".join(on))
            return
        if o.cmd == "args":
            args = nv["boot-args"].split()
            for x in o.args:
                if x.startswith("-") and x[1:] in args and not x.startswith("--"):
                    args.remove(x[1:])
                elif x not in args:
                    args.append(x)
            nv["boot-args"] = " ".join(args)
            print("boot-args:", nv["boot-args"])
        elif o.cmd == "set":
            node = cfg
            *path, last = o.key.split(".")
            for k in path:
                node = node[k]
            node[last] = parse_value(o.value)
            print(f"{o.key} = {node[last]!r}")
        elif o.cmd == "addkext":
            from oclauncher.builder import kernel_add_entries
            stage = os.path.join(img.tmp, "kexts")
            os.makedirs(stage)
            for b in bundles:
                shutil.copytree(b, os.path.join(stage, os.path.basename(b)), symlinks=True)
                run(["mcopy", "-s", "-o", "-i", img.img, os.path.join(stage, os.path.basename(b)), "::/EFI/OC/Kexts/"])
            have = {e["BundlePath"] for e in cfg["Kernel"]["Add"]}
            for e in kernel_add_entries(stage):
                if e["BundlePath"] not in have:
                    cfg["Kernel"]["Add"].append(e)
                    print("added", e["BundlePath"])
        img.write(cfg)
        print("saved")
    finally:
        img.close()


if __name__ == "__main__":
    main()
