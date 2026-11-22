#!/usr/bin/env python3
"""Read or change the OpenCore config.plist inside a test VM disk, without root.

  tools/vm-config.py show                      [--disk build/qemu/disk.qcow2]
  tools/vm-config.py args serial=3 msgbuf=1048576   add boot-args (prefix with '-' to remove: -- -serial=3)
  tools/vm-config.py set Kernel.Quirks.AppleXcpmCfgLock true

Works on qcow2 (QEMU) and raw images; the VM must be shut down. Uses qemu-img + mtools.
The EFI partition is expected to be the first partition (as written by OpenCore Launcher).
"""

import argparse
import json
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


def first_partition(gpt):
    """(first LBA, sector count) of GPT partition 1, from the first 34 sectors of the disk."""
    if gpt[512:520] != b"EFI PART":
        sys.exit("no GPT partition table on this disk")
    entries_lba = int.from_bytes(gpt[512 + 72:512 + 80], "little")
    e = gpt[entries_lba * 512:entries_lba * 512 + 128]
    first, last = int.from_bytes(e[32:40], "little"), int.from_bytes(e[40:48], "little")
    if not first:
        sys.exit("cannot find the EFI partition")
    return first, last - first + 1


class Image:
    """Only the EFI partition (a few hundred MB) is copied out and back: never the whole disk."""

    def __init__(self, disk):
        self.disk = disk
        info = json.loads(run(["qemu-img", "info", "-U", "--output=json", disk]))
        self.fmt = info["format"]
        self.tmp = tempfile.mkdtemp(prefix="vmcfg-")
        try:
            self._extract(disk)
        except BaseException:
            self.close()
            raise

    def _extract(self, disk):
        gpt = os.path.join(self.tmp, "gpt.bin")
        # qemu-img takes a write lock check here too, so this fails cleanly if the VM is running
        run(["qemu-img", "dd", "-f", self.fmt, "-O", "raw", "bs=512", "count=34", f"if={disk}", f"of={gpt}"])
        with open(gpt, "rb") as f:
            self.first, self.count = first_partition(f.read())
        self.esp = os.path.join(self.tmp, "esp.img")
        run(["qemu-img", "dd", "-f", self.fmt, "-O", "raw", "bs=512", f"skip={self.first}",
             f"count={self.count}", f"if={disk}", f"of={self.esp}"])
        self.img = self.esp

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
        self.flush()

    def flush(self):
        """Write the modified EFI partition back in place (a raw window over the disk image)."""
        target = (f"driver=raw,offset={self.first * 512},size={self.count * 512},"
                  f"file.driver={self.fmt},file.file.driver=file,file.file.filename={self.disk}")
        run(["qemu-img", "convert", "-n", "-f", "raw", self.esp, "--target-image-opts", target])

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
