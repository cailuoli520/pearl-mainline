# Native ARM64 KDE rootfs 配方

此配方对应实际完成的 `build` 编译机流程。它不是 Nura/Omarchy 安装器，也不是 Xaga 整机镜像。内核由本仓库 Release 提供，rootfs 独立制作。

## 1. 环境与输入

- 原生 ARM64 Linux、root、`pacman`、`chroot`、`cc`、Python、`bsdtar`、`cpio`、`lz4`、`zstd`、`mke2fs`、`depmod`。
- `ARCH=arm64`、`MAKEFLAGS=-j4`；该节点只编小型 USB/CINIT 代码。
- 新的工作目录 `W`、安装树 `T=$W/target`、输出 `OUT=$W/artifacts`；路径全部为本次目录。
- 同一个 Release 的 `Image.gz`、`kernel.release`、`modules-<release>.tar.zst`，以及本仓库 stock 的 boot/vendor_boot。
- 现有 Pearl legacy LZ4 ramdisk；可从已有本仓库 boot 成品通过 AOSP unpack 提取。保存其来源，不把其中旧内核拿来混用。

安装树准备最低限度 dev 节点、临时解析器和证书。直接 native 安装，无需挂载宿主完整 `/dev`、`/proc` 或 `/sys`。

## 2. 软件包

```text
base archlinuxarm-keyring dbus networkmanager openssh sudo
mesa vulkan-panfrost linux-firmware-mediatek
pipewire pipewire-alsa pipewire-pulse wireplumber
bluez bluez-utils libinput
plasma-mobile plasma-settings plasma-nm sddm
qt6-virtualkeyboard qt6-wayland xdg-desktop-portal-kde discover flatpak
noto-fonts noto-fonts-cjk noto-fonts-emoji
angelfish index-fm koko qmlkonsole kalk kclock krecorder calindori
```

不加入源码构建的 Okular Kirigami 前端。软件包依赖由 pacman 正常解析，包版本跟随本次仓库。

构建 pacman 配置：`Architecture=aarch64`、`SigLevel=Required DatabaseOptional`、`LocalFileSigLevel=Required`、`ParallelDownloads=4`，仓库 core/extra/alarm 使用：

```text
https://mirrors.tuna.tsinghua.edu.cn/archlinuxarm/$arch/$repo
```

使用正常编译节点的 Arch/Arch ARM 公共信任，通过 `pacman --root "$T"` 安装；包管理器自身的签名流程保留，它不是附加镜像验收。

如该容器需要离线方式：`--noscriptlet`，构建配置暂用 `NoExtract=usr/share/libalpm/hooks/*.hook`，安装后从本次缓存中的所属包恢复 hook 文件；target 的运行时 pacman.conf 移除这项和构建用 `DisableSandbox`。执行正常文件型设置：sysusers、locale、glib schemas、MIME、字体缓存、服务 enable。不要在构建容器 start 服务。

## 3. 账户、中文和桌面

先运行 `chroot "$T" systemd-sysusers`。再确保 wheel/audio/video/input 组存在，创建 `mobian` UID/GID 1000，加入这些组，Bash shell，设置 `1234`，锁定 root。重试时读取本次 target 的 passwd/group，已有账户保持一致，避免重复 groupadd 中断。

- locale.gen：`en_US.UTF-8` 与 `zh_CN.UTF-8`，运行正常 `locale-gen`。
- locale.conf：`LANG=zh_CN.UTF-8`、`LANGUAGE=zh_CN:zh`。
- localtime：`../usr/share/zoneinfo/Asia/Shanghai`。
- KDE 的 kdeglobals/plasma-localerc 写中文设置；mobian 的 home、`.config` 和用户资源由该用户拥有。
- 显示倍率由 KWin/Wayland 输出配置负责；`/etc/environment` 保留语言和输入法设置即可。全局 `QT_SCALE_FACTOR=2` 会额外放大 Qt 界面，应让 Qt 跟随合成器输出倍率。
- root 标记：`/etc/mt6895-rootfs`。
- ext4 label：`kdepearl`；fstab：`LABEL=kdepearl / ext4 defaults,noatime 0 1`。

SDDM 的 Wayland greeter 明确使用已安装的 KWin，避免默认寻找未安装的 Weston：

```ini
[General]
DisplayServer=wayland
InputMethod=qtvirtualkeyboard
[Theme]
Current=breeze
[Wayland]
CompositorCommand=/usr/bin/kwin_wayland --no-global-shortcuts --no-lockscreen
SessionDir=/usr/share/wayland-sessions
```

用户会话选择 `plasma-mobile.desktop`。当前普通账户登录，串口 getty 也采用密码认证。启用 SDDM、NetworkManager、sshd、Bluetooth、timesyncd，默认 graphical.target；mask 两个 wait-online 服务，避免离线启动长时间等待。

设置 journal 持久化：`Storage=persistent`、`SystemMaxUse=128M`，准备 `/var/log/journal`。出图前移除 machine-id，让 systemd 首启生成新 ID；`/var/lib/dbus/machine-id` 使用 `../../../etc/machine-id` 相对链接。

为安装树初始化自己的 pacman keyring；使用公共 Arch/Arch ARM keyring，不复制用户的 SSH/GitHub凭据。生成 `login-info.txt`，记录本次账户而不是沿用其他 rootfs 的默认值。

## 4. 模块与固件

将模块归档的 `<release>/` 解到 `$T/usr/lib/modules/<release>`，执行 `depmod -b "$T" "$release"`。固定本次 Release 后保持 Image 与模块同组；模块没有来自另一个内核目录。

固件来源是现有 Pearl ramdisk 的可移植载荷，以及正常安装的 MediaTek firmware 包。保留层级和 Mali CSF 的架构路径，精确排除：

```text
lib/firmware/WIFI
lib/firmware/BT_Addr
lib/firmware/mediatek/mt6895/WIFI
lib/firmware/mediatek/mt6895/BT_Addr
lib/firmware/remoteproc_scp
lib/firmware/mediatek/mt6895/remoteproc_scp
```

这里的两个 SCP 名称对应旧载荷的零内容占位。不要按 `WIFI*` 通配排除真正的 WIFI_RAM_CODE。使用已有 `firmware-manifest-r5.json` 时，实际目标字段是 **installedPath**，不是 path/relativePath。

## 5. 镜像与空间

本次逻辑 ext4 大小为 8 GiB；后续按用户需要和实际安装体积设置，不把旧 10 GiB 当要求。

```bash
truncate -s 8G "$OUT/rootfs.ext4.img"
mke2fs -q -F -b 4096 -L kdepearl -m 0 \
  -E root_owner=0:0,lazy_itable_init=0,lazy_journal_init=0 \
  -d "$T" "$OUT/rootfs.ext4.img"
```

正常生成成功后再清理本次 target 与缓存。清理前明确打印/确认解析后的本次绝对目录，所有旧系统与旧成品保持原样。

Android sparse 编码为 v1、file header 28、chunk header 12、block size 4096。连续非零区域分成不大于 16 MiB 的 RAW，零区域写 DONT_CARE，文件结束回填总 block/chunk 数；不是把 raw 文件简单改名。

```bash
zstd -T4 -10 --quiet "$OUT/rootfs.img" -o "$OUT/rootfs.img.zst"
```

压缩成功后可删除本次 raw 中间文件。`rootfs.img.zst` 压缩的是 **sparse**，解压后直接得到 `rootfs.img`。不附加全图 hash、反向展开、e2fsck、ELF 或外设测试阶段。

成品写 `BUILD-INFO.json` 与最终地址；硬件是否实际启动由后续手机日志记录。本次历史完成目录是 `/root/pearl-kde-usb-20261120-2144/artifacts`，属于来源记录，未来构建使用新的目录。
