"""Assemble an EFI folder (OpenCore + kexts + SSDTs + config.plist) from a Plan."""

import copy
import os
import plistlib
import random
import shutil
import subprocess
import uuid

from . import fetch
from .planner import Plan, plan_summary

NVRAM_APPLE = "7C436110-AB2A-4BBB-A880-FE41995C9F82"

KEXT_MIN_KERNEL = {"BlueToolFixup.kext": "21.0.0", "SimpleHDA.kext": "25.0.0", "CryptexFixup.kext": "22.0.0",
                   "IntelBluetoothFirmware.kext": "", "AppleMCEReporterDisabler.kext": "19.0.0"}


class BuildError(Exception):
    pass


def _noop(msg, frac=None):
    pass


# ------------------------------------------------------------------ kexts

def _kext_info(path):
    with open(os.path.join(path, "Contents", "Info.plist"), "rb") as f:
        info = plistlib.load(f)
    exe = info.get("CFBundleExecutable")
    exe_path = f"Contents/MacOS/{exe}" if exe and os.path.exists(os.path.join(path, "Contents", "MacOS", exe)) else ""
    return info.get("CFBundleIdentifier", ""), set(info.get("OSBundleLibraries", {}).keys()), exe_path


def kernel_add_entries(kexts_dir, skip_plugins=()):
    """Kernel->Add entries for every bundle (and plugin) in kexts_dir, dependency-ordered."""
    entries = []
    seen_ids = set()

    def visit(abs_path, rel):
        bid, deps, exe = _kext_info(abs_path)
        if bid in seen_ids or os.path.basename(abs_path) in skip_plugins:
            return
        seen_ids.add(bid)
        entries.append({"rel": rel, "id": bid, "deps": deps, "exe": exe})
        plug = os.path.join(abs_path, "Contents", "PlugIns")
        if os.path.isdir(plug):
            for p in sorted(os.listdir(plug)):
                if p.endswith(".kext"):
                    visit(os.path.join(plug, p), f"{rel}/Contents/PlugIns/{p}")

    # deterministic: Lilu & VirtualSMC first, then alphabetical
    names = sorted((n for n in os.listdir(kexts_dir) if n.endswith(".kext")),
                   key=lambda n: (n not in ("Lilu.kext",), n not in ("VirtualSMC.kext",), n.lower()))
    for n in names:
        visit(os.path.join(kexts_dir, n), n)

    # topological sort (stable)
    ids = {e["id"] for e in entries}
    ordered, placed = [], set()
    pending = list(entries)
    while pending:
        progress = False
        for e in list(pending):
            if all(d in placed or d not in ids for d in e["deps"]):
                ordered.append(e)
                placed.add(e["id"])
                pending.remove(e)
                progress = True
        if not progress:        # cycle: keep remaining order
            ordered.extend(pending)
            break

    out = []
    for e in ordered:
        base = os.path.basename(e["rel"])
        out.append({"Arch": "Any", "BundlePath": e["rel"], "Comment": base, "Enabled": True,
                    "ExecutablePath": e["exe"], "MaxKernel": "", "MinKernel": KEXT_MIN_KERNEL.get(base, ""),
                    "PlistPath": "Contents/Info.plist"})
    return out


# ------------------------------------------------------------------ SMBIOS

def generate_smbios(oc_dir, model):
    ms = os.path.join(oc_dir, "Utilities", "macserial", "macserial.linux")
    if os.path.exists(ms):
        os.chmod(ms, 0o755)
        try:
            out = subprocess.run([ms, "-m", model, "-n", "1"], capture_output=True, text=True).stdout
        except OSError as e:
            raise BuildError(f"Cannot run macserial ({e}). Is {os.path.dirname(ms)} on a 'noexec' filesystem? "
                             "Set OCL_CACHE to a directory where programs can run.") from e
        for line in out.splitlines():
            if "|" in line:
                serial, mlb = [x.strip() for x in line.split("|")]
                return serial, mlb
    raise BuildError("macserial could not generate a serial number")


def rom_bytes(mac):
    if mac:
        try:
            return bytes(int(x, 16) for x in mac.split(":"))
        except ValueError:
            pass
    b = bytearray(random.getrandbits(8) for _ in range(6))
    b[0] = (b[0] & 0xFC) | 0x02     # locally administered unicast
    return bytes(b)


# ------------------------------------------------------------------ config.plist

def build_config(sample, plan: Plan, kernel_add, acpi_files, amd_patches=None, serial=None, mlb=None):
    c = copy.deepcopy(sample)
    for k in [k for k in c if k.startswith("#")]:
        del c[k]

    # ACPI
    c["ACPI"]["Add"] = [{"Comment": f, "Enabled": True, "Path": f} for f in acpi_files]
    c["ACPI"]["Delete"] = []
    c["ACPI"]["Patch"] = []
    for p in plan.acpi_patches:
        c["ACPI"]["Patch"].append({"Base": "", "BaseSkip": 0, "Comment": p["Comment"], "Count": 0, "Enabled": True,
                                   "Find": p["Find"], "Limit": 0, "Mask": b"", "OemTableId": b"", "Replace": p["Replace"],
                                   "ReplaceMask": b"", "Skip": 0, "TableLength": 0, "TableSignature": b""})
    c["ACPI"]["Quirks"].update(plan.acpi_quirks)

    # Booter
    c["Booter"]["MmioWhitelist"] = []
    c["Booter"]["Patch"] = []
    c["Booter"]["Quirks"].update(plan.booter_quirks)

    # DeviceProperties
    c["DeviceProperties"]["Add"] = {path: dict(props) for path, props in plan.device_properties.items()}
    c["DeviceProperties"]["Delete"] = {}

    # Kernel
    c["Kernel"]["Add"] = kernel_add
    c["Kernel"]["Block"] = []
    c["Kernel"]["Force"] = []
    c["Kernel"]["Patch"] = list(amd_patches or [])
    for p in plan.kernel_patches:
        c["Kernel"]["Patch"].append({"Arch": "Any", "Base": "", "Comment": p["Comment"], "Count": 0, "Enabled": True,
                                     "Find": p["Find"], "Identifier": p["Identifier"], "Limit": 0, "Mask": b"",
                                     "MaxKernel": "", "MinKernel": "", "Replace": p["Replace"],
                                     "ReplaceMask": b"", "Skip": 0})
    c["Kernel"]["Quirks"].update(plan.kernel_quirks)
    em = c["Kernel"]["Emulate"]
    if plan.emulate:
        em.update(plan.emulate)
    else:
        em["Cpuid1Data"] = b""
        em["Cpuid1Mask"] = b""

    # Misc
    misc = c["Misc"]
    misc["BlessOverride"] = []
    misc["Entries"] = []
    misc["Tools"] = []
    misc["Boot"].update({"HideAuxiliary": False, "PickerMode": "Builtin", "ShowPicker": True, "Timeout": 10,
                         "PollAppleHotKeys": True, "PickerAttributes": 17})
    misc["Debug"].update({"AppleDebug": True, "ApplePanic": True, "DisableWatchDog": True, "Target": 3})
    misc["Security"].update({"AllowSetDefault": True, "BlacklistAppleUpdate": True, "ScanPolicy": 0,
                             "SecureBootModel": plan.secure_boot_model, "Vault": "Optional",
                             "ExposeSensitiveData": 6, "DmgLoading": "Signed"})

    # NVRAM
    nv = c["NVRAM"]["Add"].setdefault(NVRAM_APPLE, {})
    for k in [k for k in nv if k.startswith("#")]:
        del nv[k]
    nv["boot-args"] = " ".join(plan.boot_args)
    nv["csr-active-config"] = bytes(4)
    nv["prev-lang:kbd"] = b"en-US:0"
    nv["run-efi-updater"] = "No"
    c["NVRAM"]["WriteFlash"] = True
    if plan.nvram_restrictevents:
        guid = "4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"
        c["NVRAM"]["Add"].setdefault(guid, {}).update(plan.nvram_restrictevents)
        dele = c["NVRAM"]["Delete"].setdefault(guid, [])
        dele.extend(k for k in plan.nvram_restrictevents if k not in dele)

    # PlatformInfo
    pi = c["PlatformInfo"]
    pi["Automatic"] = True
    pi["UpdateSMBIOSMode"] = "Create"
    pi["CustomMemory"] = False
    pi["Generic"].update({"SystemProductName": plan.smbios, "SystemSerialNumber": serial or "",
                          "MLB": mlb or "", "SystemUUID": str(uuid.uuid4()).upper(),
                          "ROM": rom_bytes(plan.rom_mac), "SpoofVendor": True})
    for k in ("DataHub", "Memory", "PlatformNVRAM", "SMBIOS"):
        pi.pop(k, None)

    # UEFI
    u = c["UEFI"]
    u["Drivers"] = [{"Arguments": "", "Comment": d.name, "Enabled": True, "HideVerbose": False,
                     "LoadEarly": False, "Path": d.name} for d in plan.drivers]
    u["Quirks"].update(plan.uefi_quirks)
    u["ConnectDrivers"] = True
    u["ReservedMemory"] = []
    u["APFS"]["EnableJumpstart"] = plan.apfs_jumpstart
    if plan.apfs_legacy:
        u["APFS"]["MinDate"] = -1
        u["APFS"]["MinVersion"] = -1
    return c


# ------------------------------------------------------------------ EFI folder

def build_efi(plan: Plan, analysis, out_dir, progress=_noop, oc=None):
    """Create out_dir/EFI. Returns dict with paths and versions."""
    progress("Downloading OpenCore", None)
    oc_version, oc_dir = oc or fetch.opencore(progress)
    src = os.path.join(oc_dir, "X64", "EFI")
    efi = os.path.join(out_dir, "EFI")
    if os.path.exists(efi):
        shutil.rmtree(efi)
    os.makedirs(os.path.join(efi, "BOOT"))
    oc_out = os.path.join(efi, "OC")
    for sub in ("ACPI", "Drivers", "Kexts", "Tools"):
        os.makedirs(os.path.join(oc_out, sub))
    shutil.copy2(os.path.join(src, "BOOT", "BOOTx64.efi"), os.path.join(efi, "BOOT", "BOOTx64.efi"))
    shutil.copy2(os.path.join(src, "OC", "OpenCore.efi"), os.path.join(oc_out, "OpenCore.efi"))
    for d in plan.drivers:
        bundled = os.path.join(src, "OC", "Drivers", d.name)
        shutil.copy2(bundled if os.path.exists(bundled) else fetch.ocbinarydata_driver(d.name, progress),
                     os.path.join(oc_out, "Drivers", d.name))

    # kexts
    failed = []
    n = len(plan.kexts)
    for i, k in enumerate(plan.kexts):
        progress(f"Getting {k.name}", i / max(n, 1))
        try:
            for b in fetch.kext_bundles(k.name, plan.macos.version, progress):
                dest = os.path.join(oc_out, "Kexts", os.path.basename(b))
                if not os.path.exists(dest):
                    shutil.copytree(b, dest, symlinks=False)
        except fetch.FetchError as e:
            failed.append((k.name, str(e)))
    skip = set()
    if os.path.exists(os.path.join(oc_out, "Kexts", "VoodooI2C.kext")):
        skip |= {"VoodooPS2Trackpad.kext", "VoodooPS2Mouse.kext"}
    kernel_add = kernel_add_entries(os.path.join(oc_out, "Kexts"), skip)

    # ACPI
    acpi_files = []
    for s in plan.ssdts:
        progress(f"Getting {s.name}.aml")
        path = fetch.ssdt(s.name, oc_dir, progress)
        shutil.copy2(path, os.path.join(oc_out, "ACPI", f"{s.name}.aml"))
        acpi_files.append(f"{s.name}.aml")

    patches = fetch.amd_patches(plan.cores, progress) if plan.amd_patches else None

    progress("Generating serial numbers")
    serial, mlb = generate_smbios(oc_dir, plan.smbios)

    with open(os.path.join(oc_dir, "Docs", "Sample.plist"), "rb") as f:
        sample = plistlib.load(f)
    config = build_config(sample, plan, kernel_add, acpi_files, patches, serial, mlb)
    with open(os.path.join(oc_out, "config.plist"), "wb") as f:
        plistlib.dump(config, f, sort_keys=True)

    # human readable report next to the EFI
    from .planner import expectations, expectations_text
    with open(os.path.join(out_dir, "oclauncher-report.txt"), "w") as f:
        f.write(f"OpenCore {oc_version}\n{plan_summary(plan)}\n\n")
        f.write(f"What to expect on {plan.macos.label}\n")
        f.write(expectations_text(expectations(analysis, plan.macos.version, plan)) + "\n")
        if failed:
            f.write("\nKexts that could not be downloaded:\n" + "\n".join(f"  - {a}: {b}" for a, b in failed) + "\n")

    result = {"efi": efi, "config": os.path.join(oc_out, "config.plist"), "opencore": oc_version,
              "oc_dir": oc_dir, "failed_kexts": failed, "serial": serial}
    result["validation"] = validate(oc_dir, result["config"])
    return result


def validate(oc_dir, config_path):
    """Run OpenCore's own ocvalidate. Returns (ok, output)."""
    tool = os.path.join(oc_dir, "Utilities", "ocvalidate", "ocvalidate.linux")
    if not os.path.exists(tool):
        return None, "ocvalidate not available"
    os.chmod(tool, 0o755)
    try:
        r = subprocess.run([tool, config_path], capture_output=True, text=True)
    except OSError as e:
        return None, f"ocvalidate could not run: {e}"
    return r.returncode == 0, (r.stdout + r.stderr).strip()
