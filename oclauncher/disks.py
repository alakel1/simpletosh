"""Disk listing and writing (DESTRUCTIVE). Requires root.

Two layouts (prepare_usb / prepare_disk return a mounted Target; the macOS recovery
is downloaded straight onto it, so nothing big has to fit in RAM on the live ISO):
  USB  : GPT, one FAT32 partition "OPENCORE" with EFI/ + com.apple.recovery.boot/
  DISK : GPT, p1 ESP "EFI" (FAT32, OpenCore), p2 FAT32 "OCINSTALL" (recovery),
         p3 FAT32 "MACINTOSHHD" placeholder, to be erased as APFS from macOS Disk Utility.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time

ESP_MB = 300
RECOVERY_MB = 3072


class DiskError(Exception):
    pass


def _run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise DiskError(f"{' '.join(cmd)} failed:\n{r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def _live_disks():
    """Disks backing the running system (never offered as target)."""
    out = set()
    for mp in ("/", "/run/live/medium", "/lib/live/mount/medium", "/cdrom", "/boot", "/boot/efi"):
        try:
            src = subprocess.run(["findmnt", "-n", "-o", "SOURCE", mp], capture_output=True, text=True).stdout.strip()
            if src.startswith("/dev/"):
                pk = subprocess.run(["lsblk", "-no", "PKNAME", src], capture_output=True, text=True).stdout.strip()
                out.add(pk.splitlines()[0] if pk else os.path.basename(src))
        except OSError:
            pass
    return out


def list_disks():
    data = json.loads(_run(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,TYPE,RM,HOTPLUG,TRAN,MODEL,VENDOR,RO"]))
    live = _live_disks()
    disks = []
    for d in data.get("blockdevices", []):
        if d.get("type") != "disk" or d.get("ro") in (True, "1") or d["name"].startswith(("loop", "zram", "sr", "ram")):
            continue
        size = int(d.get("size") or 0)
        if size == 0:           # empty card-reader slots, ejected media
            continue
        removable = d.get("rm") in (True, "1") or d.get("hotplug") in (True, "1") or d.get("tran") == "usb"
        model = " ".join(x for x in ((d.get("vendor") or "").strip(), (d.get("model") or "").strip()) if x) or d["name"]
        disks.append({"name": d["name"], "path": d["path"], "size": size, "size_h": human(size),
                      "removable": removable, "transport": d.get("tran") or "", "model": model,
                      "live": d["name"] in live})
    return disks


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} PB"


def _partitions(disk):
    """Partition device paths of `disk` as the kernel sees them (/sys/block), ordered by number."""
    name = os.path.basename(os.path.realpath(disk))
    found = []
    for entry in os.listdir(f"/sys/block/{name}") if os.path.isdir(f"/sys/block/{name}") else []:
        part = f"/sys/block/{name}/{entry}/partition"
        if entry.startswith(name) and os.path.exists(part):
            with open(part) as f:
                found.append((int(f.read().strip()), f"/dev/{entry}"))
    return [p for _, p in sorted(found)]


def _holders(disk):
    """Who keeps the disk busy: mounted partitions, swap, device-mapper/LVM/RAID holders."""
    name = os.path.basename(os.path.realpath(disk))
    info = []
    for p in [disk] + _partitions(disk):
        pn = os.path.basename(p)
        hdir = f"/sys/block/{name}/{pn}/holders" if pn != name else f"/sys/block/{name}/holders"
        if os.path.isdir(hdir) and os.listdir(hdir):
            info.append(f"{pn} is used by {', '.join(os.listdir(hdir))}")
    mounts = subprocess.run(["findmnt", "-rno", "SOURCE,TARGET"], capture_output=True, text=True).stdout
    info += [f"mounted: {line}" for line in mounts.splitlines() if line.startswith(f"/dev/{name}")]
    return info


def _unmount_all(disk):
    for p in _partitions(disk):
        subprocess.run(["umount", "-f", p], capture_output=True)
        subprocess.run(["swapoff", p], capture_output=True)
    subprocess.run(["swapoff", disk], capture_output=True)
    # LVM / device-mapper on top of the disk would keep it busy
    if shutil.which("dmsetup"):
        name = os.path.basename(os.path.realpath(disk))
        for p in [name] + [os.path.basename(x) for x in _partitions(disk)]:
            hdir = f"/sys/block/{name}/{p}/holders" if p != name else f"/sys/block/{name}/holders"
            for h in os.listdir(hdir) if os.path.isdir(hdir) else []:
                subprocess.run(["dmsetup", "remove", "--force", h], capture_output=True)


def _settle(disk, n):
    """Make the kernel load the new partition table, trying several tools. Returns partitions."""
    report = []
    for cmd in (["partprobe", disk], ["blockdev", "--rereadpt", disk], ["partx", "-u", disk],
                ["partx", "-a", disk]):
        if not shutil.which(cmd[0]):
            report.append(f"{cmd[0]}: not installed")
            continue
        r = subprocess.run(cmd, capture_output=True, text=True)
        report.append(f"{' '.join(cmd)} -> exit {r.returncode} {(r.stderr or r.stdout).strip()[:200]}")
        subprocess.run(["udevadm", "settle", "--timeout=10"], capture_output=True)
        for _ in range(10):
            parts = _partitions(disk)
            if len(parts) >= n and all(os.path.exists(p) for p in parts[:n]):
                return parts
            time.sleep(0.3)
    on_disk = subprocess.run(["sgdisk", "-p", disk], capture_output=True, text=True).stdout.strip()
    raise DiskError(
        f"The kernel did not pick up the new partitions on {disk}.\n"
        + "\n".join(report)
        + f"\nkernel sees: {_partitions(disk) or 'no partitions'}"
        + (f"\nbusy: {'; '.join(_holders(disk))}" if _holders(disk) else "")
        + f"\non-disk table:\n{on_disk[-600:]}")


def _copy_tree(src, dst, progress=None, label=""):
    total = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(src) for f in fs) or 1
    done = 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for f in files:
            s = os.path.join(root, f)
            shutil.copyfile(s, os.path.join(dst, rel, f))
            done += os.path.getsize(s)
            if progress:
                progress(label, done / total)


def _mount(part):
    mnt = tempfile.mkdtemp(prefix="oclauncher-")
    _run(["mount", part, mnt])
    return mnt


def _umount(mnt):
    subprocess.run(["sync"])
    _run(["umount", mnt])
    os.rmdir(mnt)


def check_target(disk, min_bytes):
    d = next((x for x in list_disks() if x["path"] == disk), None)
    if not d:
        raise DiskError(f"{disk} not found")
    if d["live"]:
        raise DiskError(f"{disk} holds the running installer, choose another disk")
    if d["size"] < min_bytes:
        raise DiskError(f"{disk} is too small ({d['size_h']}, need {human(min_bytes)})")
    return d


class Target:
    """A partitioned + mounted destination. Use as a context manager so it is always unmounted."""

    def __init__(self, disk, mode):
        self.disk, self.mode = disk, mode
        self.efi_mnt = self.rec_mnt = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        for m in {self.efi_mnt, self.rec_mnt} - {None}:
            try:
                _umount(m)
            except DiskError:
                subprocess.run(["umount", "-l", m], capture_output=True)
        self.efi_mnt = self.rec_mnt = None

    def install_efi(self, efi_dir, progress=lambda m, f=None: None):
        _copy_tree(efi_dir, os.path.join(self.efi_mnt, "EFI"), progress, "Installing OpenCore")

    def free_bytes(self):
        st = os.statvfs(self.rec_mnt)
        return st.f_bavail * st.f_frsize

    def finish(self, progress=lambda m, f=None: None):
        progress("Flushing data to the disk")
        self.close()
        if self.mode == "disk" and shutil.which("efibootmgr") and os.path.isdir("/sys/firmware/efi"):
            # \EFI\BOOT\BOOTx64.efi also works without this entry (e.g. VirtualBox)
            subprocess.run(["efibootmgr", "-c", "-d", self.disk, "-p", "1", "-L", "OpenCore",
                            "-l", "\\EFI\\BOOT\\BOOTx64.efi"], capture_output=True)
        progress("Disk ready", 1.0)


def _wipe(disk):
    _unmount_all(disk)
    _run(["wipefs", "-a", disk])
    _run(["sgdisk", "--zap-all", disk])


def prepare_usb(disk, progress=lambda m, f=None: None) -> Target:
    """Erase `disk`: one FAT32 partition holding EFI/ and com.apple.recovery.boot/."""
    if os.geteuid() != 0:
        raise DiskError("Writing disks requires root")
    check_target(disk, 4 * 10**9)
    progress(f"Erasing {disk}")
    _wipe(disk)
    _run(["sgdisk", "-n", "1:0:0", "-t", "1:0700", "-c", "1:OPENCORE", disk])
    part = _settle(disk, 1)[0]
    _run(["mkfs.vfat", "-F", "32", "-n", "OPENCORE", part])
    t = Target(disk, "usb")
    t.efi_mnt = t.rec_mnt = _mount(part)
    return t


def prepare_disk(disk, progress=lambda m, f=None: None) -> Target:
    """Erase `disk` for a direct install: ESP (OpenCore) + recovery partition + space for macOS."""
    if os.geteuid() != 0:
        raise DiskError("Writing disks requires root")
    check_target(disk, 40 * 10**9)
    progress(f"Erasing {disk}")
    _wipe(disk)
    _run(["sgdisk", "-n", f"1:0:+{ESP_MB}M", "-t", "1:EF00", "-c", "1:EFI",
          "-n", f"2:0:+{RECOVERY_MB}M", "-t", "2:0700", "-c", "2:OCINSTALL",
          "-n", "3:0:0", "-t", "3:0700", "-c", "3:Macintosh HD", disk])
    esp, rec, main = _settle(disk, 3)[:3]
    _run(["mkfs.vfat", "-F", "32", "-n", "EFI", esp])
    _run(["mkfs.vfat", "-F", "32", "-n", "OCINSTALL", rec])
    _run(["mkfs.vfat", "-F", "32", "-n", "MACINTOSHHD", main])
    t = Target(disk, "disk")
    t.efi_mnt = _mount(esp)
    t.rec_mnt = _mount(rec)
    return t


def write_folder(folder, efi_dir, recovery_dir=None, progress=lambda m, f=None: None):
    """Non-destructive: copy everything into a folder (for testing or manual copy)."""
    os.makedirs(folder, exist_ok=True)
    if os.path.abspath(efi_dir) != os.path.abspath(os.path.join(folder, "EFI")):
        _copy_tree(efi_dir, os.path.join(folder, "EFI"), progress, "Copying OpenCore")
    if recovery_dir and os.path.abspath(recovery_dir) != os.path.abspath(os.path.join(folder, "com.apple.recovery.boot")):
        _copy_tree(recovery_dir, os.path.join(folder, "com.apple.recovery.boot"), progress, "Copying recovery")
