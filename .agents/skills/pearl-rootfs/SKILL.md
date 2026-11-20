---
name: pearl-rootfs
description: 在原生 ARM64 编译机制作 Redmi Note 12T Pro（Pearl）的 Arch Linux ARM + KDE Plasma Mobile rootfs，整合同次内核模块和双 USB ACM 日志/命令通道；省略 Okular 手机前端源码编译、额外验收和本机 WSL。
---

# Pearl KDE rootfs

把 Pearl 的 rootfs、启动 ramdisk 与 USB 诊断整理为一个可复用流程。用户要求制作或重建时执行；仅讨论方案、阅读记录或编辑本 skill 时，只处理所请求的文档/代码。

## 默认方案

- 编译节点：会话中的 SSH 别名 `build`，原生 ARM64；实际服务器操作使用 `ssh_manager` 的真实参数。
- 线程：4；软件包从官方 Arch Linux ARM 仓库安装。
- 桌面：KDE Plasma Mobile、SDDM、Qt 屏幕键盘。
- 应用：Angelfish、Index、Koko、QMLKonsole、Kalk、KClock、KRecorder、Calindori。
- 省略耗时的 Okular Kirigami 手机前端源码构建；Docker、Compose、Node、npm、base-devel 不列入预装清单。
- 简体中文 `zh_CN.UTF-8`、`Asia/Shanghai`、清华 Arch Linux ARM 镜像。
- 默认账户 `mobian / 1234`，UID/GID 1000，root 锁定；用户另给账户设置时采用其设置。
- 内核、模块来自同一个本仓库 Release；rootfs 工作不自动重编内核，也不套用 Xaga 的内核/DTS。
- 新 initramfs 提供日志和命令两个 ACM 口；成功切根后，rootfs 接续持续日志及普通账户登录。
- 仅执行正常安装、编译、格式生成和报错停止；没有逐模块 ELF、全图 SHA、二次解压、e2fsck、mock 或远端摘要验收阶段。
- 成功后只给最终目录/成品地址与登录信息；内部失败重试留在日志，不向用户交付多套修订候选。

## 资源按需读取

| 资源 | 内容 |
|---|---|
| [references/build.md](references/build.md) | 编译环境、软件包、中文/账户、模块/固件、ext4/sparse/zstd 与空间管理 |
| [references/usb.md](references/usb.md) | CINIT、静态 BusyBox、双 ACM、systemd 服务、boot/vendor_boot 的接入 |
| [scripts/usb-helper.c](scripts/usb-helper.c) | 本次实际编译使用的 early/root USB 日志与 getty 实现 |
| [scripts/patch-cinit.py](scripts/patch-cinit.py) | 将双 ACM 启动/救援与有界切根处理接入克隆的 Pearl CINIT；参数是目标 init.c |

脚本是 skill 资源，默认在编译机执行；本机可以传输文件，但 rootfs 安装、CINIT/helper 编译、镜像生成均留在编译机。

## 顺序

### 1. 准备一个独立工作目录

读取当前服务器别名、架构、可用工具和空间。当前配方面向已有 `pacman`、`chroot`、编译器、`cpio/lz4/zstd/mke2fs/depmod` 的 ARM64 编译节点。

使用新的 `/root/pearl-rootfs-<时间>` 目录，子目录为 `inputs`、`scripts`、`target`、`cache`、`artifacts`、`logs`。已有系统、旧成品和 stock 原件保持原样；清理仅限本次目录内的临时树/缓存。

环境未提供 `CAP_SYS_ADMIN` 时采用 native `pacman --root` + 文件型配置 + `mke2fs -d`，不反复尝试 unshare/mount，不退回本机 WSL。

### 2. 固定本次输入组合

从用户指定或当前选择的一次 Release 取得 `Image.gz`、`kernel.release`、`modules-<release>.tar.zst`；先选定 tag，后续全部按这个 tag 下载，避免 latest 在过程中变化。

从本仓库 stock 取得 boot/vendor_boot 模板。准备现有 Pearl Linux legacy LZ4/newc ramdisk作为固件/CPIO基础，随后覆盖其中 `/init` 与 USB 工具。旧 ramdisk 不用来替换本次内核或模块。

记录实际输入出处和版本即可；历史版本号、hash、旧 ramdisk 大小及 ROM 头字段不作为新产物的硬性值。

### 3. 安装和配置 KDE rootfs

读 [build.md](references/build.md)，安装预构建软件包、配置中文/时区/镜像/账户，装入同次模块并执行正常 `depmod`，复制 Pearl 固件。

重点顺序：`systemd-sysusers` 先于 `useradd -G`；固件清单采用实际字段 `installedPath`。离线安装抑制 scriptlet/hook 时，恢复对应的文件型配置与正常 hook 文件，避免把离线限制永久留在系统里。

### 4. 编译并接入双 USB 通道

读 [usb.md](references/usb.md)，上传两个源码资源；使用现成静态 BusyBox，仅编译小型 helper 和 CINIT 文件目标。

- early：非阻塞读取 kmsg，保留日志及暂时的静态救援 shell；不会等待 PC 连接才继续启动。
- root：持续输出 journal 和日志文件，命令口由 agetty 提供普通账户认证。
- 切根失败：留在对应阶段的救援模式；成功则原 PID1 `exec /sbin/init`。

真实串口发现/采集/发送命令时先加载 `superconnectx-mcp` 并遵循其只读发现和写租约规则；本构建流程本身不打开 COM、不执行手机命令。

### 5. 生成一套最终成品

新 CINIT/helper/BusyBox 写进 newc + legacy LZ4 ramdisk；boot/vendor_boot 从对应 stock 导出的参数重打包，保留完整 OEM DTB 容器与 ramdisk 表，由工具处理长度、对齐与 `NONE` hash footer。

从 target 生成 ext4，再编码 Android sparse（RAW 块不超过 16 MiB，零区域为 DONT_CARE），`zstd -T4` 压缩。只在镜像生成成功后清理本次临时 target；压缩成功后可清理本次 raw 中间件。

默认输出目录里只给一套：

```text
rootfs.img.zst       # 解压得到 Android sparse rootfs.img
rootfs.img          # 解压后的可刷写 sparse 文件
boot.img
vendor_boot.img
login-info.txt
BUILD-INFO.json
```

`BUILD-INFO.json` 记录实际内核版本、镜像类型、账户、来源与完成时间。构建完成就是产物已生成，不写成手机桌面已实测。设备刷写或仓库/Release 上传分别按用户当前请求执行。
