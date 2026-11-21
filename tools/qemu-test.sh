#!/usr/bin/env bash
# Test the live ISO in QEMU/KVM. Works much better than VirtualBox for macOS on AMD hosts:
# the guest is shown an Intel CPU (Penryn base + AVX2 etc.) the same way the OSX-KVM project does.
#
#   tools/qemu-test.sh out/oclauncher-0.1.0.iso   boot the installer ISO (virtual disk attached)
#   tools/qemu-test.sh --disk                      boot OpenCore from the virtual disk
#   tools/qemu-test.sh --reset                     delete the virtual disk and UEFI variables
#
# Env: RAM (8G), CPUS (4), DISK_GB (64), CPU_MODEL (Penryn), DIR (build/qemu), HEADLESS=1 (VNC :1),
#      VGA_DEVICE (vmware-svga, as OSX-KVM; "VGA,vgamem_mb=128" for the standard adapter)
# The guest's serial port (macOS kernel log with boot-arg serial=3) is written to $DIR/serial.log.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="${DIR:-$REPO/build/qemu}"
RAM="${RAM:-8G}"
CPUS="${CPUS:-4}"
DISK_GB="${DISK_GB:-64}"
CPU_MODEL="${CPU_MODEL:-Penryn}"     # minimal Intel identity + the extensions below (Haswell-noTSX hangs XNU on AMD hosts)
# Haswell-level extensions on top of Penryn. Without AVX2/BMI/FMA/MOVBE/LZCNT/F16C the kernel classifies the CPU as
# plain x86_64 instead of x86_64h, and an installed macOS 13+ (whose dyld cache is x86_64h-only unless CryptexFixup
# was used at install time) panics with "initproc failed to start ... libSystem.B.dylib". The recovery still boots.
CPU_FLAGS="${CPU_FLAGS:-+ssse3,+sse4.1,+sse4.2,+popcnt,+avx,+avx2,+aes,+pclmulqdq,+xsave,+xsaveopt,+fma,+bmi1,+bmi2,+movbe,+abm,+f16c}"
DISK="$DIR/disk.qcow2"
VARS="$DIR/OVMF_VARS.fd"

OVMF_CODE=""
for f in /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd /usr/share/edk2/x64/OVMF_CODE.4m.fd \
         /usr/share/edk2/ovmf/OVMF_CODE.fd; do
  [ -f "$f" ] && { OVMF_CODE="$f"; break; }
done
[ -n "$OVMF_CODE" ] || { echo "OVMF not found: sudo apt install ovmf"; exit 1; }
OVMF_VARS_TEMPLATE="${OVMF_CODE/CODE/VARS}"

if [ "${1:-}" = "--reset" ]; then
  rm -f "$DISK" "$VARS"; echo "Removed $DISK and $VARS"; exit 0
fi
[ -w /dev/kvm ] || { echo "/dev/kvm not accessible: add yourself to the 'kvm' group"; exit 1; }
if command -v VBoxManage >/dev/null && [ -n "$(VBoxManage list runningvms 2>/dev/null)" ]; then
  echo "A VirtualBox VM is running: shut it down first (VirtualBox and KVM can't share the CPU)."; exit 1
fi

mkdir -p "$DIR" 2>/dev/null || true
if [ ! -w "$DIR" ] || { [ -e "$DISK" ] && [ ! -w "$DISK" ]; }; then
  echo "$DIR belongs to root (created by sudo). Fix it once with:  sudo chown -R $USER: $DIR"
  exit 1
fi
[ -f "$DISK" ] || qemu-img create -f qcow2 "$DISK" "${DISK_GB}G" >/dev/null
[ -f "$VARS" ] || cp "$OVMF_VARS_TEMPLATE" "$VARS"

CDROM=()
if [ "${1:-}" != "--disk" ]; then
  ISO="$(realpath "${1:?usage: $0 path/to.iso | --disk | --reset}")"
  CDROM=(-drive "id=iso,if=none,media=cdrom,readonly=on,file=$ISO" -device "ide-cd,bus=sata.3,drive=iso,bootindex=0")
fi
DISPLAY_ARGS=(-display gtk,zoom-to-fit=on)
[ "${HEADLESS:-0}" = 1 ] && DISPLAY_ARGS=(-display none -vnc :1)

exec qemu-system-x86_64 \
  -name "OCLauncher-Test" -enable-kvm -machine q35,accel=kvm \
  -cpu "$CPU_MODEL,kvm=on,vendor=GenuineIntel,+invtsc,vmware-cpuid-freq=on,$CPU_FLAGS" \
  -smp "$CPUS,cores=$CPUS,threads=1,sockets=1" -m "$RAM" \
  -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
  -drive "if=pflash,format=raw,file=$VARS" \
  -device ich9-ahci,id=sata \
  -drive "id=disk,if=none,format=qcow2,file=$DISK" -device "ide-hd,bus=sata.2,drive=disk,bootindex=1" \
  "${CDROM[@]}" \
  -netdev user,id=net0 -device vmxnet3,netdev=net0 \
  -device qemu-xhci,id=xhci -device usb-kbd,bus=xhci.0 -device usb-tablet,bus=xhci.0 \
  -device "${VGA_DEVICE:-vmware-svga}" \
  -monitor "unix:$DIR/monitor.sock,server,nowait" \
  -serial "file:$DIR/serial.log" \
  "${DISPLAY_ARGS[@]}"
