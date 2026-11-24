# pearl-mainline

Redmi Note 12T Pro（Pearl / MT6895）的内核构建与启动镜像项目。

**当前方案：最新 Pearl 分支源码 + 本仓库配置 + Ubuntu 默认 Clang/LLVM，同次构建官方 Xaga initramfs 并封装 boot/vendor_boot。** 历史产物的版本、大小、hash、Android 头字段和工具链版本，不作为新构建的固定前提。

项目的试错、rootfs 尝试和方案变化放在 [hittory.md](hittory.md)，本页只说明现在怎么使用。

## 1. 构建内核

打开 [Actions → Build Pearl kernel, initramfs, boot images and release](https://github.com/cailuoli520/pearl-mainline/actions/workflows/build-kernel.yml)，点击 **Run workflow**。

修改并提交以下内容到 `main` 也会触发构建：

- [configs/pearl.config](configs/pearl.config)：实际使用的内核配置。
- [scripts/build-kernel.sh](scripts/build-kernel.sh)：构建脚本。
- [scripts/pack-boot.py](scripts/pack-boot.py)：按 stock 模板封装启动镜像。
- [stock](stock)：原厂 boot/vendor_boot 模板。
- [build-kernel.yml](.github/workflows/build-kernel.yml)：Actions 工作流。

| 项目 | 当前做法 |
|---|---|
| 源码 | 每次浅克隆 [MT6895-Mainline/linux 的 Pearl 分支](https://github.com/MT6895-Mainline/linux/tree/7.2-mt6895-xiaomi-pearl)最新 HEAD，不锁定某个内核 commit |
| initramfs | 每次浅克隆 [MT6895-Mainline/initramfs 的 xaga-mt6895 分支](https://github.com/MT6895-Mainline/initramfs/tree/xaga-mt6895)最新 HEAD，使用官方 Makefile，不附加本地补丁 |
| 配置 | 只复制本仓库配置；Magma 是初始来源，运行时不再下载覆盖 |
| Runner | GitHub 托管 `ubuntu-26.04-arm`，原生 ARM64 |
| 工具链 | Ubuntu 默认无版本号 `clang llvm lld`，不固定 Clang 版本 |
| 编译 | `-j4`，先构建官方 initramfs，再在独立 in-tree 目录构建 Pearl 内核、DTB 和同次模块 |
| 镜像封装 | 使用 AOSP mkbootimg/avbtool，从 stock 读取参数，封装同次载荷为 boot/vendor_boot |
| 发布 | 构建与打包成功后自动创建 GitHub Release |
| 错误处理 | `set -euo pipefail`，任一步失败立即停止并保留日志 |

不附加逐模块解析、二次解压、远端摘要复核等验收脚本，也不在本机 WSL 构建。

配置最初导入自 Magma，来源记录见 [configs/magma-import.json](configs/magma-import.json)。以后直接修改仓库中的配置即可；额外 Magma 源码补丁为 0。

## 2. 版本与产物

版本格式为：

```text
<内核基础版本>-<内核 commit 前12位>
```

例如 `7.2.10-904c3ce0833d`，仅是一次实际构建的示例，不固定后续源码版本。

脚本设置对应的 `CONFIG_LOCALVERSION`，关闭 `CONFIG_LOCALVERSION_AUTO`，并显式传入空 `LOCALVERSION=`，避免旧的 `++` 后缀。模块目录和归档使用同一个版本名。

Release 包含：

```text
Image
Image.gz
initramfs.cpio.lz4
boot.img
vendor_boot.img
pearl.dtb
modules-<release>.tar.zst
kernel.config
kernel.release
System.map
Module.symvers
build-info.txt
```

**使用同一 Release 的内核和模块。** 配置或工具链不同的两次构建，即使版本名相同，也不混用模块。

[查看 Releases](https://github.com/cailuoli520/pearl-mainline/releases) · [查看 Actions 日志](https://github.com/cailuoli520/pearl-mainline/actions)

## 3. 原厂模板：stock

[stock](stock) 保存来自 `pearl_images_OS3.0.3.0.VLHCNXM_15.0/images` 的五份原件：

- [boot.img](stock/boot.img)
- [vendor_boot.img](stock/vendor_boot.img)
- [vbmeta.img](stock/vbmeta.img)
- [vbmeta_system.img](stock/vbmeta_system.img)
- [vbmeta_vendor.img](stock/vbmeta_vendor.img)

这些文件作为封装输入，保持原版。需要禁用 AVB 标志时，另生成三个 vbmeta 的派生副本，不覆盖 stock 原件。DTBO 沿用设备对应 ROM 的原厂镜像；当前仓库没有加入 DTBO 文件。

## 4. boot 与 vendor_boot 封装

**Actions 在内核和 initramfs 构建完成后，自动封装 boot.img 与 vendor_boot.img，并与其他同次产物一起发布。** rootfs 仍独立制作，stock 原件和手机现有镜像不会被改写。

封装使用：

```text
本次构建的 Image.gz
+ 本次官方 Xaga initramfs.cpio.lz4
+ stock 原厂模板
→ boot.img / vendor_boot.img
```

- **boot**：从原厂头部取得参数，替换为本次内核和 Linux initramfs。
- **vendor_boot**：沿用原厂完整 DTB 容器、地址、cmdline、bootconfig 和 ramdisk 表，用同一份 initramfs 替换平台片段，保留片段的 type/name/board ID。其他 recovery/DLKM 片段如存在则保持原载荷；长度、偏移与对齐由官方打包工具维护。
- **设备树**：内核构建的 Pearl DTB 与 vendor_boot 的原厂 DTB 区是两件事，不把 Linux DTB 直接当成原厂模板替换。
- **AVB**：生成 Linux 镜像时使用 `NONE` hash footer；三个 vbmeta 的禁用副本单独处理。生成 footer 是封装步骤，不追加验收流程，也不是 OEM 重新签名。

Android 版本、补丁日期、地址、盐、props、rollback 与逻辑分区容量分别从对应 stock 模板取值，不再照抄旧样本的硬编码参数。新镜像正常重算 hash/footer，不复用原镜像的 digest、签名或旧载荷长度。原厂 GKI 签名只对应原厂载荷；本流程不提供 GKI 重签参数，新无签名 Linux boot v4 的签名长度由 mkbootimg 正常生成 0。

[pack-boot.py](scripts/pack-boot.py) 使用 AOSP 参数导出列表调用 mkbootimg，再调用 avbtool；任何正常构建或封装命令失败就停止。只处理输入模板，不追加输出二次解包、hash 对比、ELF 扫描或刷机步骤。

### initramfs 来源

Actions 每次从 [官方 initramfs 仓库的 `xaga-mt6895` 分支](https://github.com/MT6895-Mainline/initramfs/tree/xaga-mt6895)克隆最新源码，执行上游 `make clean` 和 `make`，生成 **newc cpio + legacy LZ4** 格式的 `initramfs.cpio.lz4`。编译复用现有 Ubuntu Clang/LLD，通过 Makefile 的 `CC` 参数指定 AArch64 目标；不套用历史 Pearl CINIT/双 USB 补丁。

内核和 initramfs 各自的实际 commit 记录在同一 Release 的 `build-info.txt`。该 initramfs 保留上游 Xaga 的分区、固件和启动默认行为；**构建成功不等于已经在 Pearl 实机验证**，本流程不刷写手机。

**使用边界：当前上游启动代码会尝试清零 EXPDB 分区的前 4 字节，默认路径为 `/dev/sdc11`。这里原样保留该行为，不改写 Pearl 的分区映射，生成 Android 格式的 boot/vendor_boot 不代表已完成 Pearl 实机适配。**

此前手工封装 boot/vendor_boot 使用的 Pearl 定制 CINIT ramdisk 是另一套输入，其 `/etc/mt6895-rootfs` 识别和双 USB 行为不会被本次代码改动替换。

## 5. rootfs

本仓库当前自动流程负责内核、DTB、模块、官方 Xaga initramfs 和 boot/vendor_boot 封装；rootfs 仍独立制作并放入设备根分区。

### Pearl rootfs skill

[pearl-rootfs](.agents/skills/pearl-rootfs/SKILL.md) 已整理原生 ARM64 编译机的 KDE Plasma Mobile 配方、中文/账户/镜像设置，以及双 USB ACM 日志和命令登录通道。它省略耗时的 Okular 手机前端源码构建与额外验收，使用同一 Release 的内核和模块。

在 DSH 中以本仓库为工作目录时，默认项目技能目录 `.agents/skills` 可发现 `pearl-rootfs`；调用后按需读取配方和源码资源。该 skill 是手工构建方法，不会单独触发内核 Actions，也没有安装或修改全局 Agent preset。

USB 方案会编译新的小型 CINIT/helper，并以既有 ramdisk 作为固件/CPIO 基础重新封装；启动失败保留救援，成功后接续 rootfs 的普通账户登录。完整说明与资源见 [skill 目录](.agents/skills/pearl-rootfs)。

既有 Arch Linux ARM、Omarchy Mobile 和 Nura + Phosh 的尝试、完成情况与遗留问题，见 [项目历程](hittory.md)。历史系统跑通的功能不自动算作新内核、新 rootfs 的实机结果。

---

[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)
