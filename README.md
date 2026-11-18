# pearl-mainline

Redmi Note 12T Pro (pearl)：Actions 内核构建与 boot / vendor_boot 打包指南

本仓库提供 **Ubuntu 26 ARM64 自动内核/模块构建与 Release 发布**，并保留 **boot、vendor_boot 的封装、原厂 DTBO、三个 vbmeta 标志处理和试错记录**。rootfs 制作与设备刷写不由内核 CI 自动执行。

当前正确组合：

```text
最新 Pearl 专用分支源码 + 本仓库 configs/pearl.config → 本次 gzip kernel
boot v4        = kernel + Linux initramfs
vendor_boot v4 = 同包原厂模板 + 同一份 Linux initramfs + 原厂完整 DT_TABLE
DTBO           = 原厂完整镜像，保持原样
三个 vbmeta    = 原厂副本的 flags |= 3
```

**证据边界：**既有 7.2.10++ 组合已有实机启动和运行记录；最新源码的新构建、以及新生成的三份 vbmeta 禁用副本，分别记录构建和实机验收结果，继承旧记录不等于完成新组合验收。

## 1. Actions：本仓库配置 + 最新 Pearl 源码 + Ubuntu 默认 clang

进入 [Actions → Build Pearl kernel and release](https://github.com/cailuoli520/pearl-mainline/actions/workflows/build-kernel.yml)，点击 **Run workflow**。修改并提交 [configs/pearl.config](configs/pearl.config)、工作流或构建脚本到 `main` 也会自动构建。

| 项目 | 固定规则 |
|---|---|
| Runner | GitHub 托管 `ubuntu-26.04-arm`，原生 ARM64，不使用本机 WSL 或旧自托管编译机 |
| 编译器 | `apt install clang llvm lld`，使用 Ubuntu 默认无版本命令；不锁定 Clang 版本、不下载外部 LLVM、不手动切 alternatives |
| 内核源码 | 每次重新 `git clone` [Pearl 专用分支](https://github.com/MT6895-Mainline/linux/tree/7.2-mt6895-xiaomi-pearl)的最新 HEAD；不固定内核 commit，也不使用上游默认 Xaga 分支 |
| 配置入口 | 仅本仓库 [configs/pearl.config](configs/pearl.config)，未来直接修改此文件即可 |
| 初始来源 | Magma 配置一次性导入；来源与初次转换记录见 [magma-import.json](configs/magma-import.json)，运行时不再拉取 Magma 配置 |
| 源码补丁 | 额外 Magma 补丁为 0；用独立 in-tree 工作目录构建，不改写上游源码 |
| 并发 | `-j4`，同一主分支的工作流串行排队，不混用输出目录 |
| 发布 | 编译与打包成功后直接由 `gh release create` 自动发布；任一步出错立即停止 |

### 统一版本与同次模块

版本只保留 **基础版本 + 内核 commit 前 12 位**，例如：

```text
uname -r:       7.2.10-904c3ce0833d
模块目录:       /lib/modules/7.2.10-904c3ce0833d
模块归档:       modules-7.2.10-904c3ce0833d.tar.zst
```

CI 在临时 `.config` 设置对应 `CONFIG_LOCALVERSION`，关闭 `CONFIG_LOCALVERSION_AUTO`，每次 `make` 显式传入 `LOCALVERSION=`，避免旧 `++`。本仓库配置不被这一步覆盖。工具链、配置变化后即使版本字符串相同，也应使用**同一个 Release 的 Image 和模块整套产物**；同名本身不是 ABI 相容性证明。

### 自动发布内容

- `Image`、`Image.gz`、`pearl.dtb`
- 同次 `modules-<release>.tar.zst`
- `kernel.config`、`kernel.release`、`System.map`、`Module.symvers`
- `build-info.txt`：完整内核 commit、版本、配置来源和实际编译器版本

脚本以 `set -euo pipefail` 直接执行构建和打包，任一步失败立即停止并保存日志。不额外逐模块解析、二次解压或远端摘要复核。

此 Actions 流程发布内核、DTB、模块；boot/vendor_boot 和 rootfs 不混入这次内核发布。后续手工封装仍沿用以下已验证 OEM 模板配方；CI 通过不代表完成实体屏幕、无线、音频等实机验收。

### 原生 Ubuntu ARM64 复现

在新的工作树中使用 Ubuntu 默认工具与本仓库脚本，不用 Windows WSL：

```bash
sudo apt-get update
sudo apt-get install -y clang llvm lld make bc bison flex gawk pkg-config pahole \
  libssl-dev libelf-dev zlib1g-dev openssl git python3 xz-utils zstd kmod rsync file gzip cpio
BUILD_JOBS=4 bash scripts/build-kernel.sh
# 完整产物在 out/；后续封装使用 out/Image.gz。
```

初次导入仍保留 48-bit VA/PA、MTU3 双角色、Panthor 等板级配置。具体配置以本仓库文件及此次 `olddefconfig` 后发布的 `kernel.config` 为准，历史尺寸和 SHA256 仅用于辨认旧样本，不锁定新构建。

## 2. 原厂输入与已验证的结构

本次模板来自 **OS3.0.3.0.VLHCNXM_15.0**。准备同包原厂文件与可启动的 Linux initramfs：

```text
stock/
├── vendor_boot.img
├── dtbo.img
├── vbmeta.img
├── vbmeta_system.img
└── vbmeta_vendor.img
linux/initramfs-final.cpio.lz4
```

| 项目 | 当前成功样本 |
|---|---|
| boot | v4，header 1584 字节，固定 4096 字节对齐，signature_size=0 |
| kernel | gzip；v4 boot 没有独立 DTB 段，kernel 内部的内嵌 DTB 是另一概念 |
| ramdisk | Linux LZ4/newc，generic/vendor 两边逐字节一致 |
| vendor_boot | v4，header 2128 字节，page4096 |
| vendor DTB 区 | 原厂完整 DT_TABLE 327,372 字节；内层 FDT 327,308 字节 |
| vendor ramdisk table | 108 字节 / 1 条目 / entry_size 108，type=1，offset=0 |
| vendor 参数 | kernel 0x40000000，ramdisk 0x66f00000，tags/DTB 0x47c80000 |
| cmdline / bootconfig | boot cmdline 空；vendor 为 bootopt=64S3,32N2,64N2；bootconfig 空 |
| boot / vendor_boot 交付容量 | 各 67,108,864 字节，即 64 MiB |
| DTBO 完整镜像 | 33,554,432 字节，即 32 MiB；内层 DT_TABLE total_size 是 77,296 字节 |

DT_TABLE 的魔数为 `d7b7ab1e`，裸 FDT 为 `d00dfeed`，两者分开描述。现有 ramdisk 是 **LZ4 legacy**，魔数 `02214c18`；用 `lz4 -t` 验证，避免仅接受普通 frame 的 `04224d18`。

## 3. 准备工具与输入

需要 AOSP `mkbootimg`、`unpack_bootimg`、`avbtool.py`，以及 Python 3、gzip、lz4、cpio、file、device-tree-compiler。历史复现使用 AOSP avbtool 1.3.0；工具更新后重新做封装校验。后续代码块在同一个 Linux Bash 会话中按顺序执行，沿用前面定义的变量。

```bash
# avbtool 使用官方来源，放在本地下载目录，不提交大镜像。
mkdir -p downloads
curl --fail --location \
  'https://android.googlesource.com/platform/external/avb/+/refs/heads/main/avbtool.py?format=TEXT' \
  | base64 --decode > downloads/avbtool.py
AVB_TOOL="$PWD/downloads/avbtool.py"
STOCK="$PWD/stock"
KERNEL="${KERNEL:-$PWD/out/Image.gz}"
RAMDISK="$PWD/linux/initramfs-final.cpio.lz4"
OUT="$PWD/pack-output/pearl-pack-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT/template" "$OUT/verify"

gzip -t "$KERNEL"
lz4 -t "$RAMDISK"
lz4 -dc "$RAMDISK" | cpio -tv
# 列表中的 init 应有执行位；提取到输出目录用于格式识别，不运行它。
lz4 -dc "$RAMDISK" | cpio -i --to-stdout init ./init > "$OUT/init.check"
file "$OUT/init.check"
# file 应识别 ELF 64-bit LSB、ARM aarch64；本配方的 /init 是可执行 ELF。
python3 "$AVB_TOOL" version
sha256sum "$KERNEL" "$RAMDISK" "$STOCK"/*.img
```

## 4. boot：gzip kernel + Linux ramdisk

采用已成功样本的 v4 头部与 Android 元数据。`os_version` 是 Android 头字段，**与 Linux 内核版本号无关**。

```bash
mkbootimg --header_version 4 \
  --kernel "$KERNEL" --ramdisk "$RAMDISK" --cmdline '' \
  --os_version 16.0.0 --os_patch_level 2025-10 \
  -o "$OUT/boot.raw.img"
cp "$OUT/boot.raw.img" "$OUT/boot.img"
python3 "$AVB_TOOL" add_hash_footer --image "$OUT/boot.img" \
  --partition_size 0x4000000 --partition_name boot \
  --hash_algorithm sha256 --algorithm NONE \
  --salt 00112233445566778899aabbccddeeff \
  --prop com.android.build.boot.os_version:16
```

v4 boot 没有单独的 DTB 字段；`--dtb` 不会在 v4 boot 中写入独立 DTB 段。kernel 内部可能包含源码构建出的 DTB，保留本次源码产物，分清其与 vendor DTB 区的角色。

## 5. vendor_boot：导出 OEM 参数，只替换 ramdisk

**完整保留原厂 DT_TABLE、平台表、地址、cmdline 和 bootconfig。** 该 ROM 的原厂 base 有 684 个 symbols；同包 overlay 的 75 个外部 fixups 已离线解析通过。离线合并输出仅作验证，交付仍使用原厂未合并 DT_TABLE。

```bash
unpack_bootimg --boot_img "$STOCK/vendor_boot.img" \
  --out "$OUT/template" --format mkbootimg > "$OUT/vendor.args"

# 严格识别本页 OS3.0.3.0 的 OEM DT_TABLE，再开始重打包。
printf '%s  %s\n' \
  3d631e494c011c3c6baf23a3ff05703ecf7ea9565b287b223557aa1292f95c5b \
  "$OUT/template/dtb" | sha256sum --check --status

python3 - "$OUT/vendor.args" "$RAMDISK" "$OUT/vendor_boot.raw.img" <<'PY'
import pathlib, shlex, subprocess, sys
args = shlex.split(pathlib.Path(sys.argv[1]).read_text())
assert args.count('--vendor_ramdisk_fragment') == 1
idx = args.index('--vendor_ramdisk_fragment')
args[idx + 1] = str(pathlib.Path(sys.argv[2]).resolve())
subprocess.run(['mkbootimg', *args, '--vendor_boot', sys.argv[3]], check=True)
PY

# 原厂 DTB 区必须完整保留；此值用于识别当前 OEM 模板，而非新 kernel。
sha256sum "$OUT/template/dtb"
# 3d631e494c011c3c6baf23a3ff05703ecf7ea9565b287b223557aa1292f95c5b

cp "$OUT/vendor_boot.raw.img" "$OUT/vendor_boot.img"
python3 "$AVB_TOOL" info_image --image "$STOCK/vendor_boot.img"
# 以下 salt/fingerprint 来自本页 OS3.0.3.0 模板；其他 ROM 取各自实际元数据。
python3 "$AVB_TOOL" add_hash_footer --image "$OUT/vendor_boot.img" \
  --partition_size 0x4000000 --partition_name vendor_boot \
  --hash_algorithm sha256 --algorithm NONE \
  --salt 4035af02400bcd5016d64602509d1e5e75d21dc57f97697356ca36af946ae1f9 \
  --prop 'com.android.build.vendor_boot.fingerprint:Xiaomi/mihal/mihal:12/SP1A.210812.016/OS3.0.3.0.VLHCNXM:user/release-keys'
```

官方重打包会按新 ramdisk 同步更新头部长度和真实 platform 表项的 fragment 长度。**表载荷本身也要存在**，仅把 header 的 108/1/108 三个数值写回，等价性尚未成立。

原厂 DTBO 完整保留，包括原厂填充与镜像布局；复制后做字节比较：

```bash
cp "$STOCK/dtbo.img" "$OUT/dtbo-stock.img"
cmp "$STOCK/dtbo.img" "$OUT/dtbo-stock.img"
# 32 MiB 完整镜像，不用 77,296 字节的内层 table 替代。
```

## 6. 三个 vbmeta：都禁用 verity 与 verification

操作对象是 **vbmeta.img、vbmeta_system.img、vbmeta_vendor.img 三份**。AVB0 头部 `flags` 位于 offset 120，为 32 位大端整数：

- bit 0：`HASHTREE_DISABLED`，数值 1。
- bit 1：`VERIFICATION_DISABLED`，数值 2。
- 保留其他位，执行 `flags |= 3`。本次原厂三份 flags 都为 0，结果都是 `0x3`。

```bash
python3 - "$STOCK" "$OUT/vbmeta-disabled" <<'PY'
import pathlib, struct, sys
source, output = map(pathlib.Path, sys.argv[1:])
output.mkdir(exist_ok=False)
for name in ('vbmeta.img', 'vbmeta_system.img', 'vbmeta_vendor.img'):
    original = (source / name).read_bytes()
    assert len(original) >= 256 and original[:4] == b'AVB0'
    result = bytearray(original)
    flags = struct.unpack_from('>I', result, 120)[0]
    struct.pack_into('>I', result, 120, flags | 3)
    assert len(result) == len(original)
    assert all(a == b for i, (a, b) in enumerate(zip(original, result))
               if i < 120 or i > 123)
    assert struct.unpack_from('>I', result, 120)[0] & 3 == 3
    (output / name).write_bytes(result)
    print(name, 'flags=', hex(flags | 3), 'bytes=', len(result))
PY
```

本次三份分别保持 8192/4096/4096 字节，各只改 offset 123 的一个字节（0x00 → 0x03）。保留描述符、链式分区信息、密钥和认证数据块，处理方式等同 fastboot 的 `--disable-verity --disable-verification` 标志预处理。

**这是现有已解锁启动链的禁用标志处理，没有重新签名。** 原厂签名字节保留，但其摘要对应修改前的头部；“auth 数据未变”不等于“修改后 OEM RSA 签名有效”。这里只校验禁用位与改动范围。

boot/vendor_boot 中的 `NONE` hash footer 和这三个 vbmeta 的禁用策略分开处理：**NONE 不是删除 footer，禁用校验也不是重新生成空白 vbmeta。**

## 7. 封装验收

输出使用 `boot.img` / `vendor_boot.img`：AVB descriptor 的 partition_name 为 **boot / vendor_boot**，与实际槽位目标 boot_a、boot_b 等名字分开。`verify_image` 会按 descriptor 在同目录寻找同名镜像；其他文件名要建立正确别名后再验证。

```bash
python3 "$AVB_TOOL" verify_image --image "$OUT/boot.img"
python3 "$AVB_TOOL" verify_image --image "$OUT/vendor_boot.img"
unpack_bootimg --boot_img "$OUT/boot.img" --out "$OUT/verify/boot"
unpack_bootimg --boot_img "$OUT/vendor_boot.img" --out "$OUT/verify/vendor"
cmp "$KERNEL" "$OUT/verify/boot/kernel"
cmp "$RAMDISK" "$OUT/verify/boot/ramdisk"
cmp "$RAMDISK" "$OUT/verify/vendor/vendor_ramdisk00"
cmp "$OUT/template/dtb" "$OUT/verify/vendor/dtb"
for image in boot.img vendor_boot.img; do
  test "$(stat -c %s "$OUT/$image")" -eq 67108864
done
test "$(stat -c %s "$OUT/dtbo-stock.img")" -eq 33554432
stat -c '%n %s' "$OUT/boot.img" "$OUT/vendor_boot.img" "$OUT/dtbo-stock.img"
# 使用相对文件名，校验清单可随输出目录迁移。
(
  cd "$OUT"
  sha256sum boot.img vendor_boot.img dtbo-stock.img vbmeta-disabled/*.img > SHA256SUMS
)
```

验收至少包含：gzip/lz4 完整性、/init 的 newc 条目与 AArch64 可执行格式、两个 ramdisk 字节一致、OEM DT_TABLE 原样、真实 platform table 的 108/1/108/type1、长度字段随 payload 更新、64 MiB 容量、AVB payload 长度/salted SHA-256/footer 偏移，以及三个 vbmeta 的 flags。离线验收与最新组合的实机启动、内核版本、根挂载验证分别留记录。

## 8. 试错雷点 → 症状 → 正确做法

以下症状来自既有试错记录，保留当时的变量变化范围，区分复现证据与假说。

| 雷点 | 记录症状或混淆 | 正确做法 / 证据边界 |
|---|---|---|
| 裸 Image 代替 gzip kernel | decompress kernel image fail!!! | 本机成功链使用 gzip kernel；以 gzip -t 检查本次输入 |
| vendor ramdisk 置零 | FAR 0xffff000066f00000、panic: die | 本配方保留非空 Linux ramdisk；同步头部与表项长度 |
| 换成缺 symbols 的 native DTB | No node __symbols__ in main dtb、mt_boot.c:579 ASSERT | 保留同包 OEM DT_TABLE 与符号；关键是 base/overlay 兼容性，不是“小 DTB 必然失败” |
| 清零 platform table，同时换 DTB 容器 | hdr_loaded、#T#load images=0 | 两项同时变化，单项因果未隔离；正确配方保留真实平台表与 OEM 容器 |
| 只拼 header + ramdisk + DTB | header 声称有表，实际载荷缺少表 | 用官方 OEM 参数重打包，并核对表载荷及 fragment 长度 |
| 手拼 AVB 偏移或布局 | boot_info.c:155 g_boot_info.hdr_loaded | 官方 add_hash_footer 或已验证的 OEM NONE 元数据更新；容量/算法单项因果仍要隔离 |
| 将 chosen fixup 错误直接归因 A/B DTBO 不同 | Couldn't find 'chosen' symbol | 核对同包组件与实际槽位；实机 DTBO 读回 hash 未取得，A/B 差异仍属假说 |
| 将地址误写后偶然启动解释为字段无关 | 旧 DTB address 大端误写样本 | 使用官方工具，保留正确 u64 小端地址；个别旧样本不代替格式要求 |
| 把 DT_TABLE 与 vendor ramdisk table 混为一谈 | 327,372 字节与 108 字节混淆 | 前者是 DTB 容器，后者是 platform ramdisk 描述表 |
| 限制 ramdisk 只认普通 LZ4 frame | 错判 legacy magic 为坏包 | 本次为 02214c18；用 lz4 -t/newc 内容验证 |
| 认为给 v4 boot 的 --dtb 会产生独立 DTB 段 | 与 kernel 内嵌 DTB 混淆 | v4 无独立 DTB 段；vendor DTB 区与 kernel 内部树分别核对 |
| NONE、flags3、OEM 签名三者混说 | 把禁用位当成重签名或删除 footer | 三者分别解释和验证，保留 boot/vendor footer 与原厂 vbmeta 描述结构 |
| 复用旧成品 SHA/尺寸判定最新内核 | 新 build 被旧样本身份锁住 | 每次重算长度/hash，记录最新 commit、config、工具链和实际 release |
| 随意等长改 OEM bootargs | 把旧 mini-ramdisk 规则当当前硬条件 | 当前配方保留 OEM 树，由已验证 Linux initramfs 接续启动 |

## 9. 来源与版本记录

- [本仓库构建配置](configs/pearl.config)与[一次性 Magma 导入记录](configs/magma-import.json)：初始 Git blob 为 `3e9417ad3335bad115e6f65c62211e6aeb8beacd`；后续只读本仓库配置，不从 Magma 重新下载覆盖。
- [Pearl 专用源码分支](https://github.com/MT6895-Mainline/linux/tree/7.2-mt6895-xiaomi-pearl)与[历史源码到当前 HEAD 的比较](https://github.com/MT6895-Mainline/linux/compare/a908b7825c7d619873392e5fbbcba2e54695d0eb...904c3ce0833daf168911dffa30e7957fad3b0315)。
- [既有内核来源与运行记录](https://github.com/Magma-71453/redmi-note-12t-pro-archlinuxarm/blob/5afcd01c0e649a32f71f2413cb7823271911aedc/docs/kernel-provenance.md)：历史构建、补丁与实测基线，区别于最新 HEAD 的新构建。
- [AOSP mkbootimg](https://android.googlesource.com/platform/system/tools/mkbootimg/)与[AOSP AVB](https://android.googlesource.com/platform/external/avb/)：头部、descriptor、禁用位及工具实现依据。

内核编译和发布结果以 [Actions 运行记录](https://github.com/cailuoli520/pearl-mainline/actions/workflows/build-kernel.yml)及每个 Release 的 `build-info.txt` 为准。CI 不执行设备刷写；手工封装的原厂文件留作输入，生成结果写入独立目录，现场槽位状态与原厂 DTBO 内容单独核对。

[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)
