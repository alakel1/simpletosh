#!/usr/bin/env bash
# Build the OpenCore Launcher live ISO (Debian 13 "trixie", amd64, BIOS + UEFI hybrid).
#
#   sudo ./iso/build-iso.sh              full build, skipping steps that are already up to date
#   sudo ./iso/build-iso.sh --iso-only   only redo the final ISO step from the existing build
#   sudo ./iso/build-iso.sh --clean      delete the previous build and start from scratch
#
# Steps are resumable: the Debian base, the package install and the squashfs are
# reused when packages.list / the app / the overlay have not changed.
# Env overrides: SUITE (trixie), MIRROR, WORK (build dir), OUT (output dir)
# Needs ~6 GB free space and an internet connection. Runs on Debian/Ubuntu/Pop!_OS hosts.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
SUITE="${SUITE:-trixie}"
MIRROR="${MIRROR:-http://deb.debian.org/debian}"
WORK="${WORK:-$REPO/build/iso}"
OUT="${OUT:-$REPO/out}"
VERSION="$(python3 -c "import sys; sys.path.insert(0, '$REPO'); import oclauncher; print(oclauncher.__version__)")"
ROOTFS="$WORK/rootfs"
STAGE="$WORK/stage"
ISO="$OUT/oclauncher-$VERSION.iso"
MODE="${1:-}"

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
skip() { printf '\033[0;32m    %s (already up to date)\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo: sudo $0 $*"
[ "$(uname -m)" = "x86_64" ] || die "x86_64 host required"
case "$MODE" in ""|--iso-only|--clean) ;; *) die "unknown option $MODE (use --iso-only or --clean)";; esac

# ------------------------------------------------------------------ host tools
# Only the GRUB *-bin* packages, without recommends: they provide files for grub-mkrescue and
# never touch the host's own bootloader (grub-efi-amd64 / -signed would).
HOST_PKGS="debootstrap squashfs-tools xorriso mtools grub-pc-bin grub-efi-amd64-bin grub-common dosfstools curl"
missing=""
for p in $HOST_PKGS; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
if [ -n "$missing" ]; then
  log "Installing build tools:$missing"
  apt-get update -qq || echo "warning: 'apt-get update' reported errors, trying to install anyway"
  apt-get install -y --no-install-recommends $missing || die "could not install:$missing"
fi
for cmd in debootstrap mksquashfs xorriso grub-mkrescue mformat curl dpkg-deb sha256sum; do
  command -v "$cmd" >/dev/null || die "'$cmd' not found after installing the build tools"
done
[ -d /usr/lib/grub/i386-pc ] || die "GRUB BIOS files missing (grub-pc-bin)"
[ -d /usr/lib/grub/x86_64-efi ] || die "GRUB UEFI files missing (grub-efi-amd64-bin)"

# ------------------------------------------------------------------ helpers
mounted=()
cleanup() {
  for ((i=${#mounted[@]}-1; i>=0; i--)); do umount -lf "${mounted[$i]}" 2>/dev/null || true; done
  mounted=()
}
trap cleanup EXIT
bind() { mount --bind "$1" "$2"; mounted+=("$2"); }

pkg_hash() { sha256sum "$HERE/packages.list" | cut -c1-16; }
content_hash() {
  ( cat "$HERE/packages.list"
    cd "$REPO" && find oclauncher iso/overlay -type f ! -path '*__pycache__*' -print0 | sort -z | xargs -0 sha256sum
  ) | sha256sum | cut -c1-16
}

if [ "$MODE" = "--clean" ]; then
  log "Removing the previous build"
  rm -rf "$ROOTFS" "$STAGE"
fi

if [ "$MODE" != "--iso-only" ]; then
  # ---------------------------------------------------------------- Debian keyring
  KEYRING="$WORK/debian-archive-keyring.gpg"
  if [ ! -s "$KEYRING" ]; then
    log "Fetching the current Debian archive keyring"
    mkdir -p "$WORK/keyring"
    deb=$(curl -fsSL "$MIRROR/pool/main/d/debian-archive-keyring/" | grep -oE 'debian-archive-keyring_[0-9.]+(\+[a-z0-9.]+)?_all\.deb' | sort -V | tail -1)
    [ -n "$deb" ] || die "could not find debian-archive-keyring in $MIRROR"
    curl -fsSL -o "$WORK/keyring/$deb" "$MIRROR/pool/main/d/debian-archive-keyring/$deb"
    dpkg-deb -x "$WORK/keyring/$deb" "$WORK/keyring/x"
    cp "$WORK/keyring/x/usr/share/keyrings/debian-archive-keyring.gpg" "$KEYRING"
  fi

  # ---------------------------------------------------------------- 1. base system
  log "1/4 Debian $SUITE base system"
  if [ -x "$ROOTFS/usr/bin/apt-get" ]; then
    skip "base system"
  else
    rm -rf "$ROOTFS"; mkdir -p "$ROOTFS"
    script=/usr/share/debootstrap/scripts/$SUITE
    [ -e "$script" ] || script=/usr/share/debootstrap/scripts/sid
    debootstrap --arch=amd64 --variant=minbase --keyring="$KEYRING" \
      --components=main,contrib,non-free,non-free-firmware "$SUITE" "$ROOTFS" "$MIRROR" "$script"
  fi

  # ---------------------------------------------------------------- 2. packages
  log "2/4 Packages (kernel, firmware, graphics, tools)"
  if [ "$(cat "$ROOTFS/.oclauncher-pkgs" 2>/dev/null)" = "$(pkg_hash)" ]; then
    skip "packages"
  else
    cat > "$ROOTFS/etc/apt/sources.list" <<EOF
deb $MIRROR $SUITE main contrib non-free non-free-firmware
deb $MIRROR $SUITE-updates main contrib non-free non-free-firmware
deb http://security.debian.org/debian-security $SUITE-security main contrib non-free non-free-firmware
EOF
    echo "oclauncher" > "$ROOTFS/etc/hostname"
    printf '127.0.0.1 localhost\n127.0.1.1 oclauncher\n' > "$ROOTFS/etc/hosts"
    rm -f "$ROOTFS/etc/resolv.conf"; cp -L /etc/resolv.conf "$ROOTFS/etc/resolv.conf"
    bind /dev "$ROOTFS/dev"; bind /dev/pts "$ROOTFS/dev/pts"; bind /proc "$ROOTFS/proc"; bind /sys "$ROOTFS/sys"
    PKGS=$(grep -vE '^\s*(#|$)' "$HERE/packages.list" | tr '\n' ' ')
    chroot "$ROOTFS" /bin/bash -euxc "
      export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
      apt-get update
      apt-get install -y --no-install-recommends $PKGS
      systemctl enable NetworkManager
      systemctl set-default multi-user.target
      echo 'LANG=C.UTF-8' > /etc/default/locale
      passwd -d root
      apt-get clean
      rm -rf /var/lib/apt/lists/* /var/cache/apt/*.bin /tmp/*
    "
    cleanup
    rm -f "$ROOTFS/etc/resolv.conf"
    pkg_hash > "$ROOTFS/.oclauncher-pkgs"
  fi

  # ---------------------------------------------------------------- 3. app + squashfs
  log "3/4 OpenCore Launcher + compressed filesystem"
  if [ -s "$STAGE/live/filesystem.squashfs" ] && [ "$(cat "$STAGE/.content" 2>/dev/null)" = "$(content_hash)" ]; then
    skip "filesystem"
  else
    rm -rf "$ROOTFS/opt/oclauncher"; mkdir -p "$ROOTFS/opt/oclauncher"
    cp -r "$REPO/oclauncher" "$ROOTFS/opt/oclauncher/"
    find "$ROOTFS/opt/oclauncher" -name __pycache__ -prune -exec rm -rf {} +
    cp -r "$HERE/overlay/." "$ROOTFS/"
    chmod 755 "$ROOTFS/usr/local/bin/"*
    echo "$VERSION" > "$ROOTFS/etc/oclauncher-live"

    rm -rf "$STAGE"; mkdir -p "$STAGE/live"
    KVER=$(ls "$ROOTFS/boot" | sed -n 's/^vmlinuz-//p' | sort -V | tail -1)
    [ -n "$KVER" ] || die "no kernel found in the image"
    cp "$ROOTFS/boot/vmlinuz-$KVER" "$STAGE/live/vmlinuz"
    cp "$ROOTFS/boot/initrd.img-$KVER" "$STAGE/live/initrd.img"
    echo "    compressing (a few minutes)…"
    mksquashfs "$ROOTFS" "$STAGE/live/filesystem.squashfs" -comp xz -b 1M -noappend -e boot \
      -e .oclauncher-pkgs -quiet
    content_hash > "$STAGE/.content"
  fi
else
  [ -s "$STAGE/live/filesystem.squashfs" ] || die "no previous build in $STAGE: run without --iso-only"
fi

# ------------------------------------------------------------------ 4. ISO
log "4/4 Hybrid BIOS/UEFI ISO"
mkdir -p "$STAGE/boot/grub" "$OUT"
touch "$STAGE/live/oclauncher.id"
sed "s/@VERSION@/$VERSION/g" "$HERE/grub.cfg" > "$STAGE/boot/grub/grub.cfg"
grub-mkrescue -o "$ISO" "$STAGE" -- -volid OCLAUNCHER
if [ -n "${SUDO_USER:-}" ]; then
  chown "$SUDO_USER": "$ISO" "$OUT"
  chown "$SUDO_USER": "$(dirname "$WORK")"     # build/ itself, so VM test dirs can be created without sudo
fi

log "Done: $ISO ($(du -h "$ISO" | cut -f1))"
echo "Test it:   ./tools/vbox-test.sh $ISO"
echo "USB stick: sudo dd if=$ISO of=/dev/sdX bs=4M status=progress oflag=sync   (or balenaEtcher / GNOME Disks)"
