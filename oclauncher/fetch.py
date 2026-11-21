"""Downloads with a local cache: OpenCorePkg, kexts, SSDTs, AMD patches, AppleALC layouts."""

import json
import os
import plistlib
import re
import shutil
import time
import urllib.error
import urllib.request
import zipfile

from . import __version__
from .data.kexts import CATALOG, KextSource

CACHE = os.environ.get("OCL_CACHE") or os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "oclauncher")
UA = f"oclauncher/{__version__}"
DORTANIA_INDEX = "https://raw.githubusercontent.com/dortania/build-repo/builds/config.json"
SSDT_PREBUILT = "https://github.com/dortania/Getting-Started-With-ACPI/raw/master/extra-files/compiled/{}.aml"
AMD_PATCHES = "https://raw.githubusercontent.com/AMD-OSX/AMD_Vanilla/master/patches.plist"
OCBINARYDATA_DRIVER = "https://raw.githubusercontent.com/acidanthera/OcBinaryData/master/Drivers/{}"
APPLEALC_RES = "https://raw.githubusercontent.com/acidanthera/AppleALC/master/Resources/{}/Info.plist"

AIRPORTITLWM_NAMES = {"10.13": "HighSierra", "10.14": "Mojave", "10.15": "Catalina", "11": "BigSur",
                      "12": "Monterey", "13": "Ventura"}


class FetchError(Exception):
    pass


def _noop(msg, frac=None):
    pass


def _path(*parts):
    p = os.path.join(CACHE, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def download(url, dest, progress=_noop, label=None, retries=3):
    """Download url to dest (atomic). Returns dest."""
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    label = label or os.path.basename(dest)
    tmp = dest + ".part"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    progress(f"Downloading {label}", done / total if total else None)
            os.replace(tmp, dest)
            return dest
        except (urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(1 + attempt)
    raise FetchError(f"Download failed: {url} ({last})")


def get_json(url, cache_name, max_age=6 * 3600):
    dest = _path("meta", cache_name)
    if os.path.exists(dest) and time.time() - os.path.getmtime(dest) < max_age:
        with open(dest) as f:
            return json.load(f)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        with open(dest, "w") as f:
            json.dump(data, f)
        return data
    except (urllib.error.URLError, OSError, ValueError) as e:
        if os.path.exists(dest):    # stale cache is better than nothing
            with open(dest) as f:
                return json.load(f)
        raise FetchError(f"Cannot reach {url}: {e}")


def extract(zip_path, progress=_noop):
    out = zip_path[:-4] if zip_path.endswith(".zip") else zip_path + ".d"
    if os.path.isdir(out):
        return out
    progress(f"Extracting {os.path.basename(zip_path)}")
    tmp = out + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = os.path.realpath(os.path.join(tmp, info.filename))
            if not target.startswith(os.path.realpath(tmp) + os.sep):
                raise FetchError(f"Unsafe path in {zip_path}: {info.filename}")
            z.extract(info, tmp)
            mode = info.external_attr >> 16
            if mode & 0o111:
                os.chmod(target, 0o755)
    os.replace(tmp, out)
    return out


# ------------------------------------------------------------------ GitHub / Dortania

def github_latest_asset(repo, pattern):
    rel = get_json(f"https://api.github.com/repos/{repo}/releases/latest", f"gh_{repo.replace('/', '_')}.json")
    for a in rel.get("assets", []):
        if re.search(pattern, a["name"]):
            return rel.get("tag_name"), a["name"], a["browser_download_url"]
    raise FetchError(f"No asset matching {pattern} in {repo} {rel.get('tag_name')}")


def dortania_asset(key):
    idx = get_json(DORTANIA_INDEX, "dortania_build_repo.json")
    ver = idx[key]["versions"][0]
    url = ver["links"]["release"]
    return ver.get("version", "latest"), os.path.basename(url), url


# ------------------------------------------------------------------ OpenCore

def opencore(progress=_noop):
    """Download + extract the latest OpenCorePkg RELEASE. Returns (version, dir)."""
    try:
        tag, name, url = github_latest_asset("acidanthera/OpenCorePkg", r"RELEASE\.zip$")
    except FetchError:
        tag, name, url = dortania_asset("OpenCorePkg")
    z = download(url, _path("opencore", name), progress, f"OpenCore {tag}")
    return tag, extract(z, progress)


def ocbinarydata_driver(name, progress=_noop):
    """A UEFI driver that is not in OpenCorePkg but in acidanthera's OcBinaryData (e.g. apfs_aligned.efi)."""
    return download(OCBINARYDATA_DRIVER.format(name), _path("ocbinarydata", name), progress, name)


# ------------------------------------------------------------------ kexts

def kext_package(src: KextSource, macos_version=None, progress=_noop):
    """Return the extracted directory of the archive containing src's bundles."""
    if src.manual:
        raise FetchError(f"{src.name} must be added manually")
    errors = []
    candidates = []
    if src.url:
        candidates.append(("direct", lambda: ("", os.path.basename(src.url), src.url)))
    if src.github:
        pattern = src.asset
        if "{codename}" in pattern:
            code = AIRPORTITLWM_NAMES.get(macos_version)
            if not code:
                raise FetchError(f"{src.name} has no build for macOS {macos_version}")
            pattern = pattern.replace("{codename}", code)
        candidates.append(("github", lambda p=pattern: github_latest_asset(src.github, p)))
    if src.dortania:
        candidates.append(("dortania", lambda: dortania_asset(src.dortania)))
    for kind, resolve in candidates:
        try:
            tag, name, url = resolve()
            sub = src.package or src.name
            z = download(url, _path("kexts", sub, f"{tag}-{name}" if tag else name), progress, f"{src.name} {tag}")
            return extract(z, progress)
        except (FetchError, zipfile.BadZipFile) as e:
            errors.append(f"{kind}: {e}")
    raise FetchError(f"Could not download {src.name}: " + "; ".join(errors))


def find_bundle(root, bundle):
    """Shallowest directory named `bundle` under root, skipping debug symbols."""
    best = None
    for dirpath, dirnames, _ in os.walk(root):
        if ".dSYM" in dirpath:
            continue
        if bundle in dirnames:
            cand = os.path.join(dirpath, bundle)
            if "/PlugIns/" in cand and best:
                continue
            if best is None or cand.count(os.sep) < best.count(os.sep):
                best = cand
    return best


def kext_bundles(name, macos_version=None, progress=_noop):
    src = CATALOG[name]
    root = kext_package(src, macos_version, progress)
    found = []
    for b in src.bundles:
        p = find_bundle(root, b)
        if not p:
            # e.g. AirportItlwm zips contain the kext under a different folder structure
            raise FetchError(f"{b} not found inside the {name} download")
        found.append(p)
    return found


# ------------------------------------------------------------------ ACPI / patches / audio

def ssdt(name, oc_dir=None, progress=_noop):
    """Prebuilt SSDT: OpenCore AcpiSamples first for -ALT variants, else Dortania."""
    if oc_dir:
        local = os.path.join(oc_dir, "Docs", "AcpiSamples", "Binaries", f"{name}.aml")
        if os.path.exists(local):
            return local
    return download(SSDT_PREBUILT.format(name), _path("ssdt", f"{name}.aml"), progress, f"{name}.aml")


def amd_patches(cores, progress=_noop):
    path = download(AMD_PATCHES, _path("amd", "patches.plist"), progress, "AMD_Vanilla patches")
    with open(path, "rb") as f:
        patches = plistlib.load(f)["Kernel"]["Patch"]
    for p in patches:
        if "cpuid_cores_per_package" in p.get("Comment", ""):
            rep = bytearray(p["Replace"])
            rep[1] = cores & 0xFF
            p["Replace"] = bytes(rep)
    return patches


def applealc_layouts(codec):
    """Sorted list of layout-ids AppleALC supports for codec (e.g. 'ALC892')."""
    if not codec:
        return []
    try:
        path = download(APPLEALC_RES.format(codec), _path("applealc", f"{codec}.plist"))
        with open(path, "rb") as f:
            data = plistlib.load(f)
        return sorted({int(l["Id"]) for l in data.get("Files", {}).get("Layouts", [])})
    except (FetchError, plistlib.InvalidFileException, KeyError, ValueError):
        return []


def default_layout(codec, layouts):
    if not layouts:
        return 1
    for pref in (1, 11, 3, 7, 13, 21):
        if pref in layouts:
            return pref
    return layouts[0]
