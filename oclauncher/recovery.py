"""Download the macOS Recovery image from Apple with OpenCore's macrecovery.py."""

import os
import re
import subprocess
import sys

from .data.macos import MacOS


class RecoveryError(Exception):
    pass


def download(mac: MacOS, out_dir, oc_dir, progress=lambda m, f=None: None, cancel=None):
    """Download BaseSystem/RecoveryImage into out_dir/com.apple.recovery.boot. Returns that dir."""
    tool = os.path.join(oc_dir, "Utilities", "macrecovery", "macrecovery.py")
    if not os.path.exists(tool):
        raise RecoveryError("macrecovery.py not found in the OpenCore package")
    dest = os.path.join(out_dir, "com.apple.recovery.boot")
    os.makedirs(dest, exist_ok=True)
    marker = os.path.join(dest, f".complete-{mac.version}")
    if os.path.exists(marker):
        progress(f"{mac.label} recovery already downloaded", 1.0)
        return dest
    for f in os.listdir(dest):      # leftovers of an interrupted or different download
        os.remove(os.path.join(dest, f))
    cmd = [sys.executable, "-u", tool, "-b", mac.board, "-m", mac.mlb, "-os", mac.os_type, "-o", dest, "download"]
    progress(f"Downloading {mac.label} recovery from Apple", 0.0)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=os.path.dirname(tool))
    buf = b""
    tail = []
    while True:
        if cancel and cancel():
            proc.kill()
            raise RecoveryError("Cancelled")
        ch = proc.stdout.read(1)
        if not ch:
            break
        if ch in (b"\r", b"\n"):
            line = buf.decode(errors="replace").strip()
            buf = b""
            if not line:
                continue
            tail = (tail + [line])[-20:]
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
            if m:
                progress(f"Downloading {mac.label} recovery from Apple", float(m.group(1)) / 100)
        else:
            buf += ch
    if proc.wait() != 0:
        raise RecoveryError("macrecovery failed:\n" + "\n".join(tail))
    open(marker, "w").close()
    # OpenCore shows this text as the boot menu entry name (instead of the volume label)
    with open(os.path.join(dest, ".contentDetails"), "w") as f:
        f.write(f"macOS {mac.name} Installer")
    progress(f"{mac.label} recovery downloaded", 1.0)
    return dest
