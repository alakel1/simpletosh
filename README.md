# OpenCore Launcher

A guided, hardware-aware installer that prepares **OpenCore + the macOS installer** for a PC.

> **Disclaimer.** This is an educational, personal project. It is not affiliated with Apple Inc., and Apple's
> macOS licence only allows macOS on Apple-branded hardware: you are responsible for how you use it. This
> repository contains no Apple software and no third-party binaries. OpenCore, the kexts and the macOS
> recovery are downloaded at run time from their original sources (see [CREDITS.md](CREDITS.md)).
> Apple, Mac and macOS are trademarks of Apple Inc.

```
Welcome → Hardware → macOS versions → What to expect → Setup review → Destination → Confirm → Install → Done
```

1. **Hardware analysis** reads everything from `/proc` and `/sys`:
   - CPU, GPUs, analog codecs and HDMI/DP audio;
   - Ethernet, Wi‑Fi and Bluetooth;
   - USB devices: webcams, USB audio, fingerprint readers;
   - USB controllers and their port counts, SATA and NVMe;
   - card readers, Thunderbolt, touchpads and chipset.
2. **macOS versions**: every release from High Sierra to Tahoe is marked installable or blocked, with the reason. Caveats are listed and one version is recommended.
3. **What to expect**: for the chosen version, every item goes into one of three groups: *will work*, *uncertain*, *will not work*. This page comes before anything is downloaded or written. Only out-of-the-box drivers are used: nothing is root-patched inside macOS.
3. **Setup**: SMBIOS, kexts, SSDTs, quirks, DeviceProperties and boot-args are chosen automatically, each with a one-line *why*.
4. **Destination**:
   - erase a disk and install (like a normal OS installer);
   - make a bootable USB;
   - save to a folder.
5. **Install**: downloads OpenCore and the kexts (latest GitHub releases, Dortania builds as fallback). It generates serials with `macserial`, checks the config with `ocvalidate`, downloads the macOS recovery from Apple and writes the target.

## Where the knowledge comes from

| Topic | Source |
|-------|--------|
| Ethernet, Broadcom Wi‑Fi fixups, Bluetooth | The PCI/USB IDs each kext declares in its own `Info.plist` (`oclauncher/kextdb.py`). A snapshot ships in `data/kext_index.json` (`tools/update_kext_index.py`) and is refreshed at runtime when online |
| GPU support per macOS version | Dortania GPU Buyers Guide (Intel / AMD / NVIDIA pages) |
| CPU support | Dortania *macOS limits* |
| macOS Tahoe specifics (AppleHDA gone, WhateverGreen on AMD, `-ibtcompatbeta`) | Dortania *extras/tahoe* |
| Intel Wi‑Fi cards | itlwm compatibility list (itlwm matches every Intel device, so it can't be indexed) |
| Audio layouts | AppleALC `Resources/<codec>/Info.plist`, fetched live |
| Recovery boards | OpenCorePkg `macrecovery/recovery_urls.txt` |
| Tahoe APFS driver (`apfs_aligned.efi`, Jumpstart off) | acidanthera *OcBinaryData* (W^X-fixed copy of Apple's driver) |
| Tahoe analog audio (opt-in, experimental) | *SimpleHDA* by laobamac (third-party, loads AppleHDA via OpenCore) |
| AMD / VM quirks (DummyPowerManagement, AppleMCEReporterDisabler), T2 updates (`revpatch=sbvmm`) | Dortania AMD config, RestrictEvents docs, cross-checked with OSX-KVM and OpCore-Simplify |
| Config validity | OpenCore's own `ocvalidate` |

The hardware page shows the source of every claim (expand a row).

## Run from source

Requirements: Python ≥ 3.10, GTK 4 + libadwaita (`gir1.2-gtk-4.0 gir1.2-adw-1`), and for writing disks `gdisk dosfstools util-linux parted efibootmgr`.

```bash
./run.sh                    # GUI; as a normal user only "save to folder" is enabled
sudo ./run.sh               # GUI with USB / disk writing
./run.sh analyze            # compatibility report in the terminal
./run.sh detect -o me.json  # save a hardware report (to test another machine)
./run.sh build --report me.json --macos 15 --out /tmp/efi [--recovery]
OCL_REPORT=me.json ./run.sh # GUI driven by a saved report
```

Environment: `OCL_LANG=it` (translations in `oclauncher/locale/`), `OCL_CACHE` (download cache),
`OCL_STEP=<page>` (developer: jump to a page).

## Layout

| Path | What |
|------|------|
| `oclauncher/hw.py` | hardware detection → JSON report |
| `oclauncher/data/devices.py` | knowledge base: CPU/GPU/NIC/Wi‑Fi/BT → macOS support |
| `oclauncher/data/{macos,smbios,kexts}.py` | releases + recovery boards, Mac models, kext sources |
| `oclauncher/planner.py` | analysis + build plan (all the Dortania rules live here) |
| `oclauncher/fetch.py` | downloads with cache |
| `oclauncher/builder.py` | EFI folder + `config.plist` from OpenCore's `Sample.plist`, `ocvalidate` |
| `oclauncher/recovery.py` | macOS recovery via `macrecovery.py` |
| `oclauncher/disks.py` | USB / whole-disk / folder writers (destructive, root) |
| `oclauncher/gui/app.py` | GTK4/libadwaita installer |
| `tests/` | synthetic hardware reports + build/validate test |

## Live ISO

```bash
sudo ./iso/build-iso.sh                 # -> out/oclauncher-<version>.iso  (~6 GB free space, internet)
./tools/vbox-test.sh out/oclauncher-*.iso   # boot it in a VirtualBox VM with a 64 GB virtual disk
./tools/vbox-test.sh --disk                 # after "Erase a disk and install": boot OpenCore from the virtual disk
./tools/vbox-test.sh --delete
```

- **Base**: Debian 13 "trixie" with a recent kernel and all the non-free firmware, so it boots on as many PCs as possible. The ISO is a BIOS + UEFI hybrid; Secure Boot must be off (OpenCore needs that anyway).
- **Session**: root autologin on tty1, which starts the installer full-screen in the `cage` Wayland kiosk. Software rendering is used automatically in VMs, with `nomodeset`, or with no GPU driver.
- **Boot menu**: normal, *safe graphics*, *load into RAM* (frees the USB stick so it can be the target), and UEFI firmware settings.
- **Network**: Wi‑Fi is set up from the installer's welcome page (NetworkManager). `nmtui` is available on tty2.
- **Files**: `iso/packages.list` (packages), `iso/overlay/` (files copied into the image), `iso/grub.cfg`.

Write it to a USB stick with `dd`, GNOME Disks or balenaEtcher.

## Tests

```bash
python3 tests/fixtures/make_fixtures.py   # regenerate synthetic reports
python3 tests/test_fixtures.py            # analyse + build + ocvalidate every fixture
python3 tests/test_fixtures.py --no-build # rules only, no downloads
```

## Known limits (v0.1)

- SSDTs are the Dortania prebuilts. Custom SSDTs generated from the DSDT (SSDTTime-style) are not done yet.
- USB port mapping is not done (macOS's 15-port limit applies after install).
- Laptop iGPU framebuffer values are best-effort. I2C touchpads may need a custom SSDT-GPI0.
- Disk/USB writing has not been tested on real hardware yet. Try it in a VM first.
- VM testing on AMD hosts: use `tools/qemu-test.sh` (QEMU/KVM, Penryn CPU identity). VirtualBox can't boot macOS on AMD:
  its CPU profiles only change CPUID, while XNU also reads Intel-only MSRs. KVM gets past them with `ignore_msrs=1`
  and `vmware-cpuid-freq`, but VirtualBox on AMD hangs (EXITBS / LKC). The live ISO itself runs fine in VirtualBox.
  `tools/vm-config.py` edits the config.plist or adds kexts on the virtual disk without reinstalling.

## License

BSD-3-Clause, see [LICENSE](LICENSE). Third-party projects keep their own licences: see [CREDITS.md](CREDITS.md).
