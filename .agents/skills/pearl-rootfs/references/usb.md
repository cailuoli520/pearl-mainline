# Pearl 双 USB ACM 与启动镜像

## 1. 准备资源

从 `MT6895-Mainline/initramfs` 的 `7.2-mt6895-xiaomi-pearl` 分支克隆 CINIT 到本次 `$W/inputs/cinit-source`。将 skill 的两个源码文件上传到 `$W/scripts`；使用 SSH 工具实际 schema，文件传输采用程序生成的数据，避免手工复制大段 Base64。

准备预编译静态 AArch64 BusyBox（本次实际采用 Alpine busybox-static 1.37.0-r31）。通过正常官方 APK 仓库和包工具取得二进制，放到本次 inputs，省略 BusyBox 源码编译。

只编两个小目标：

```bash
python3 "$W/scripts/patch-cinit.py" "$W/inputs/cinit-source/init.c"
mkdir -p "$W/inputs/usb/bin"
cc -static -Os -s -Wall -Wextra \
  -o "$W/inputs/usb/bin/pearl-usb" "$W/scripts/usb-helper.c"
make -C "$W/inputs/cinit-source" -j4 CC=cc BOOT_PARTITION=/dev/sdc86 \
  "$W/inputs/cinit-source/root/init"
```

最后一个是 Makefile 的绝对文件目标，只编 `/init`，不会顺带下载固件或生成其他 ramdisk。用户根分区变化时调整 BOOT_PARTITION；默认 userdata 的现有节点为 `/dev/sdc86`。源码补丁保留 `init.c.original`，后续 source 改动不匹配时停止并处理实际错误，不盲目重复补丁。

## 2. 布局

```text
initramfs:
  /init             新 CINIT
  /bin/pearl-usb    静态 helper
  /bin/busybox      静态 BusyBox
  /bin/sh           -> busybox
  /bin/getty        -> busybox

rootfs:
  /usr/lib/pearl-usb/usb-helper
  /usr/lib/pearl-usb/busybox
  /usr/lib/pearl-usb/sh -> busybox
```

可同时放入常用 BusyBox 救援命令链接。制作 rootfs 时始终包含 root 布局，保证切根后的失败路径仍有静态工具。

## 3. 两个通道

helper 建一个 configfs gadget：`acm.log` 与 `acm.shell`，CDC composite `EF/02/01`、`1d6b:0104`。从 `port_num` 取真实 ttyGS 名称；Windows 的 COM 编号由设备枚举决定，省略固定 COM4/COM5 假设。

- early：非阻塞读取 `/dev/kmsg`，保存 `/pearl-early.log`，日志口有 256 KiB 有界缓冲、CRLF 与迟连接回放；命令口是启动阶段的静态救援 shell。
- root：持续读取本次 journal，保存 `/var/log/pearl-usb/boot.log`；命令口用 agetty，登录普通账户 `mobian`。
- UDC 发现最多等 8 秒；采用已经注册的控制器，只解除实际占用该 UDC 的旧 gadget 一次，保持 USB role 原配置。PC 未连接不会阻塞 PID1 引导。

这是 Linux gadget 就绪后的 USB 通道，不是 LK/BootROM 的硬件 UART。开启前已被有限 kmsg 缓冲覆盖的文字由其他早期日志来源处理；不要把它写成所有启动阶段均已抓到。

## 4. systemd 接续

将以下服务放到 target 的 `/etc/systemd/system/pearl-usb.service`，离线 enable，不在编译机运行：

```ini
[Unit]
Description=Pearl USB log and login ports
After=systemd-udev-trigger.service local-fs.target
Before=sddm.service

[Service]
Type=simple
ExecStart=/usr/lib/pearl-usb/usb-helper root --busybox /usr/lib/pearl-usb/busybox --agetty /usr/bin/agetty
Restart=always
RestartSec=1
KillMode=control-group
TimeoutStopSec=4

[Install]
WantedBy=multi-user.target
```

常驻服务没有 `--rescue` 参数，普通登录维持密码认证。仅 CINIT 切根/exec 失败时启动：

```text
/usr/lib/pearl-usb/usb-helper root --rescue
  --busybox /usr/lib/pearl-usb/busybox
  --rescue-shell /usr/lib/pearl-usb/sh
```

## 5. CINIT 行为

资源补丁把 AArch64 getdents64 从旧 217 修为 61，PID1 用 `clone(SIGCHLD)` 启动独立 helper，继续挂载根分区（60×500ms 重试）。重要阶段打印成功/errno，挂载耗尽则留下 early 救援。

成功切根前使用 subreaper/PPid 子树处理 getty 的 setsid 子进程，有界 TERM/KILL 清理。保存早期日志到 `/newroot/var/log/pearl-usb/initramfs.log`，移动 sys/dev 时连同 configfs/devpts 保留。检查实际切根调用返回值；最终 systemd 仍由原 PID1 exec，失败留救援而不是静默 sleep。

以上返回值处理属于正常引导错误处理，不是额外镜像验收。

## 6. boot/vendor_boot 打包

将新 `/init`、helper、BusyBox 与链接覆盖到现有 Pearl newc 载荷目录，正常生成 newc 后以 legacy LZ4 压缩；boot 与 vendor_boot 使用同一份新 ramdisk。

用 AOSP `unpack_bootimg --format mkbootimg` 导出 stock 参数：boot 替换本次 Image.gz 与 ramdisk，vendor_boot 仅替换 vendor ramdisk fragment。保留 OEM 的完整 DTB 容器、地址、cmdline、bootconfig 和真实 ramdisk 表。

使用 AOSP `avbtool add_hash_footer --algorithm NONE` 完成 Linux 镜像封装；盐、props、rollback、容量来自对应 stock 的实际元数据。stock 原件保持原样，输出是本次 artifacts 里的新文件。

省略输出二次 unpack、CRC/hash 对比、ELF 扫描、硬件 fixture 等验收。正常打包命令报错就停止；用户当前请求只要成品时，不自动刷手机。
