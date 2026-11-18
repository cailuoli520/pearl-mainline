#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
JOBS=${BUILD_JOBS:-4}
SOURCE="$ROOT/.work/linux"
STAGING="$ROOT/.work/modules"
OUT="$ROOT/out"

export PATH="/usr/bin:/bin:$PATH"
export ARCH=arm64 LLVM=1 LLVM_IAS=1 LOCALVERSION=''
mkdir -p "$ROOT/.work" "$OUT"
git clone --depth=1 --single-branch --branch 7.2-mt6895-xiaomi-pearl \
  https://github.com/MT6895-Mainline/linux.git "$SOURCE"
COMMIT=$(git -C "$SOURCE" rev-parse HEAD)
SHORT=${COMMIT:0:12}

# Only this repository's config; no runtime Magma download or source patches.
cp "$ROOT/configs/pearl.config" "$SOURCE/.config"
cd "$SOURCE"
./scripts/config --file .config --set-str LOCALVERSION "-$SHORT" --disable LOCALVERSION_AUTO
make LLVM=1 LLVM_IAS=1 ARCH=arm64 LOCALVERSION= olddefconfig
make LLVM=1 LLVM_IAS=1 ARCH=arm64 LOCALVERSION= -j"$JOBS" Image.gz dtbs modules
RELEASE=$(make -s LLVM=1 LLVM_IAS=1 ARCH=arm64 LOCALVERSION= kernelrelease)
make LLVM=1 LLVM_IAS=1 ARCH=arm64 LOCALVERSION= -j"$JOBS" INSTALL_MOD_PATH="$STAGING" modules_install
MODDIR="$STAGING/lib/modules/$RELEASE"
for link in build source; do
  if [[ -L "$MODDIR/$link" ]]; then unlink "$MODDIR/$link"; fi
done
depmod -b "$STAGING" "$RELEASE"

cp arch/arm64/boot/Image arch/arm64/boot/Image.gz "$OUT/"
cp arch/arm64/boot/dts/mediatek/mt6895-xiaomi-pearl.dtb "$OUT/pearl.dtb"
cp .config "$OUT/kernel.config"
cp include/config/kernel.release System.map Module.symvers "$OUT/"
tar --numeric-owner --owner=0 --group=0 -C "$STAGING/lib/modules" -cf - "$RELEASE" \
  | zstd -T"$JOBS" -10 --no-progress -o "$OUT/modules-$RELEASE.tar.zst"
{
  printf 'Kernel: %s\nKernel commit: %s\n' "$RELEASE" "$COMMIT"
  printf 'Source: MT6895-Mainline/linux branch 7.2-mt6895-xiaomi-pearl\n'
  printf 'Config: this repository configs/pearl.config\n'
  printf 'Runner: Ubuntu 26.04 ARM64; default Ubuntu clang/LLVM\n'
  clang --version
  ld.lld --version
} > "$OUT/build-info.txt"
if [[ -n ${GITHUB_OUTPUT:-} ]]; then printf 'kernel_release=%s\n' "$RELEASE" >> "$GITHUB_OUTPUT"; fi
