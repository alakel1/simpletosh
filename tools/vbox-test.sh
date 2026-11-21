#!/usr/bin/env bash
# Test the live ISO in VirtualBox.
#
#   tools/vbox-test.sh out/oclauncher-0.1.0.iso   create (if needed) and start the VM from the ISO
#   tools/vbox-test.sh --disk                      start from the virtual disk (after "Erase a disk and install")
#   tools/vbox-test.sh --delete                    remove the VM and its disk
#
# Env: VM (name, default OCLauncher-Test), RAM_MB (8192), CPUS (4), DISK_GB (64),
#      CPU_PROFILE: CPU shown to the guest. On AMD hosts it defaults to "Intel Core i7-6700K"
#      (macOS boots far more reliably on an Intel CPU); CPU_PROFILE=host keeps the real CPU.
set -euo pipefail
VM="${VM:-OCLauncher-Test}"
RAM_MB="${RAM_MB:-8192}"
CPUS="${CPUS:-4}"
DISK_GB="${DISK_GB:-64}"
BASE="$HOME/VirtualBox VMs/$VM"
DISK="$BASE/$VM.vdi"

exists() { VBoxManage showvminfo "$VM" >/dev/null 2>&1; }

# VM files left on disk but removed from VirtualBox's list (e.g. "Remove → keep files"): register again
if ! exists && [ -f "$BASE/$VM.vbox" ]; then
  echo "Registering the existing $VM from $BASE"
  VBoxManage registervm "$BASE/$VM.vbox"
fi

if [ "${1:-}" = "--delete" ]; then
  exists && { VBoxManage controlvm "$VM" poweroff 2>/dev/null || true; sleep 2; VBoxManage unregistervm "$VM" --delete; }
  echo "Deleted $VM"; exit 0
fi

if ! exists; then
  echo "Creating VM $VM"
  VBoxManage createvm --name "$VM" --ostype Debian_64 --register
  VBoxManage modifyvm "$VM" --memory "$RAM_MB" --cpus "$CPUS" --vram 128 --graphicscontroller vmsvga \
    --firmware efi --chipset ich9 --ioapic on --rtcuseutc on --pae on --nested-paging on \
    --nic1 nat --nictype1 82545EM --usb-xhci on --mouse usbtablet --keyboard usb --audio-enabled off
  VBoxManage storagectl "$VM" --name SATA --add sata --controller IntelAhci --portcount 4 --hostiocache on
  VBoxManage createmedium disk --filename "$DISK" --size $((DISK_GB * 1024)) --format VDI
  VBoxManage storageattach "$VM" --storagectl SATA --port 0 --device 0 --type hdd --medium "$DISK" --nonrotational on
fi

if [ -n "$(VBoxManage list runningvms | grep "\"$VM\"")" ]; then
  echo "$VM is running: close it first (VBoxManage controlvm \"$VM\" poweroff)"; exit 1
fi

# CPU presented to the guest (applied every time, so existing VMs get it too)
if [ -z "${CPU_PROFILE:-}" ]; then
  grep -q AuthenticAMD /proc/cpuinfo && CPU_PROFILE="Intel Core i7-6700K" || CPU_PROFILE=host
fi
VBoxManage modifyvm "$VM" --cpu-profile "$CPU_PROFILE"
echo "Guest CPU: $CPU_PROFILE"

if [ "${1:-}" = "--disk" ]; then
  VBoxManage storageattach "$VM" --storagectl SATA --port 1 --device 0 --type dvddrive --medium emptydrive 2>/dev/null || true
  VBoxManage modifyvm "$VM" --boot1 disk --boot2 none
else
  ISO="$(realpath "${1:?usage: $0 path/to.iso | --disk | --delete}")"
  VBoxManage storageattach "$VM" --storagectl SATA --port 1 --device 0 --type dvddrive --medium "$ISO"
  VBoxManage modifyvm "$VM" --boot1 dvd --boot2 disk
fi
VBoxManage startvm "$VM"
