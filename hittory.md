# Pearl 移植与构建历程

这份文档记录我们做过的事情、方案变化和实际进度。**它是历史记录，不是当前构建必须满足的条件清单。** 现在的使用方法见 [README](README.md)。

## 一、先把 Linux 启动链跑起来

早期工作围绕 Redmi Note 12T Pro（Pearl / MT6895）展开：内核、设备树、Linux initramfs、boot/vendor_boot 和根分区如何接上。

- 尝试过 Debian 系统；其中一套旧 Debian + TOUCH 组合得到用户的触控反馈。
- 随后制作 Arch Linux ARM 根文件系统，旧组合已有根分区挂载、SSH、Wi-Fi、基础 GPU 驱动初始化和充电记录。
- 曾尝试 Plasma 桌面；当时主要是嵌套会话，未把它算成实体屏幕上的持久桌面已经完成。
- 制作了静态 AArch64 CINIT `/init` 和 legacy LZ4/newc initramfs：识别根文件系统标记，挂载后执行 `/sbin/init`，并处理启动所需固件。

这些记录证明了旧组合的部分能力，不代表后面每套新内核和新 rootfs 都已完成实机测试。

## 二、boot/vendor_boot 的封装路线逐渐明确

我们对不同封装方法做过试错，也参考过 Magma 项目的 Test 路线。最终以用户已经跑通的 OEM 模板路线为准：

```text
boot：gzip Linux kernel + Linux initramfs
vendor_boot：原厂模板 + Linux initramfs + 原厂完整 DTB 容器
DTBO：对应 ROM 的原厂镜像
vbmeta：从原厂文件生成禁用标志副本
```

主要经验：

- 原厂 DTB 容器与 Linux 内核里的 Pearl DTB 各有用途，直接互换曾造成启动问题。
- vendor ramdisk 的平台表既要有正确头字段，也要有真实表载荷。
- ramdisk 改变后，长度、表项、对齐和 AVB footer 一起按新内容生成。
- Android 头字段与 Linux 内核 release 是不同概念。
- 禁用 vbmeta 标志、使用 `NONE` footer 和 OEM 签名有效性是不同事项。

当时记录过固定尺寸、地址、盐、hash、symbols/fixups 等具体数值，用来辨认和分析那一批样本。现在这些旧数值已经从 README 的当前使用条件中移走；封装改为取对应 stock 模板的实际参数。

## 三、2026-11-15～16：旧内核与 ALARM 基线

较早的内核基线使用过：

- 源码 commit：`a908b7825c7d619873392e5fbbcba2e54695d0eb`。
- Clang 23.1.1。
- release：`7.2.10++`。
- 同次模块：1507 个。
- 约 10 GiB 的 Arch Linux ARM rootfs。

该组合留下了用户确认的启动、根分区、SSH、Wi-Fi，以及显示/GPU 驱动初始化和充电等记录。音频、休眠、完整桌面等功能继续按各自的实际记录区分，没有统一写成“全功能完成”。

### 为什么是 `++`

旧配置里手动设置了 `CONFIG_LOCALVERSION="+"`。当时构建没有显式传入空 `LOCALVERSION`，源码的 SCM 后缀逻辑又补了一个 `+`，于是得到 `7.2.10++`。

它不是编译了两次，也不是补丁数量的标记。后来我们把命名改成基础版本加内核 commit，去掉这类后缀混淆。

## 四、2026-11-17：转为原生 ARM 编译机和零额外 Magma 补丁

构建统一改到 SSH 别名 `build` 的原生 ARM64 编译机，4 核、约 24 GiB 内存，使用 `-j4`，不再使用本机 WSL。

这次采用 Pearl 分支的 `904c3ce0833daf168911dffa30e7957fad3b0315`：

- Magma 只作为配置起点，最终没有额外套用 Magma 源码补丁。
- 使用 Clang/LLD 23.1.5。
- release 为 `7.2.10-pearl-upstream-g904c3ce0`，同次模块 1506 个。
- 曾尝试 `O=` 构建，但分支里的内层 make 出现 `.config` 路径问题；随后用独立 in-tree 源码目录完成构建，而不是修改上游源码。
- 内核与模块归档完成并下载到本地；后续又把这次内核封装进新的 boot。
- vendor_boot 继续使用原厂模板路线；仅换内核时，vendor_boot 成品仍可能与此前相同，因为内核放在 boot 中。

旧构建中的 `pearl/upstream/g…` 是人为设置的版本说明，不是 Linux 自动要求的命名。

## 五、2026-11-17：Omarchy Mobile rootfs 与首启排障

我们制作了 Omarchy Mobile ARM64 rootfs，并接入 Pearl 模块和固件：

- Omarchy Mobile 源码基线为 `c4623572be1c141d602cf99a83ae0bf9e3eea09c`。
- 安装了 907 个正常签名软件包。
- 原始 ext4 镜像约 10.5 GiB，压缩文件约 3 GiB。
- 本地分片传输曾中断；后面用户取得了 raw 文件并继续实际刷机测试。
- 大 raw 镜像经过 Windows fastboot 时出现 `std::bad_alloc`，因此后续故障修复尽量采用小型 sparse 补丁，而不是反复搬运整张大图。

### 账户问题

初始镜像的 root 被锁定，普通用户尚未创建，首启配置仍是空模板。用户实际启动后，这成了直接问题。

随后提供小补丁，填写 `yg` 账户与初始密码，并调整首启服务和 SDDM 的先后关系。这个阶段的账户方案属于 Omarchy 的历史尝试，不是现在内核仓库的要求。

### USB ACM 日志通道

一开始主要盯着 USB 网络与 SSH，后来补上 USB CDC ACM：

- 手机端使用 `/dev/ttyGS0` 一类设备，Windows 侧出现 COM 口。
- 首先做只输出启动日志的通道，避免依赖尚未完成创建的账户。
- 用户实际通过 COM4 发回日志，确认通道工作。
- 日志中的 `not attached` 是绑定瞬间的状态；后续已经收到数据，说明它不等于持续连接失败。

### 首启被桌面 UFW 配置挡住

较完整的日志显示，硬件脚本继续执行，明确失败点在 UFW：其规则需要的部分 netfilter 功能与当时内核配置不对应。

我们没有为这个问题重编内核，而是把桌面 UFW 设置从手机首启流程中排除，保留网络、SSH 和显示服务。用户随后发回：

```text
=== firstboot done ===
Finished Omarchy Mobile first-boot setup.
Authentication for user "yg" successful
Session started true
```

这证明首启与自动登录链路通过。实体屏幕上的完整桌面表现没有据此自动标为完成。之后用户暂停 Omarchy，转向保存内核产物和尝试其他 rootfs。

## 六、2026-11-17：第一次 GitHub Release

将 boot、vendor_boot 和同次模块发布到本仓库：

[7.2.10-pearl-upstream-g904c3ce0 Release](https://github.com/cailuoli520/pearl-mainline/releases/tag/pearl-7.2.10-upstream-g904c3ce0-20261117)。

这批文件对应当时的纯 Pearl 分支构建。发布产物与完成新组合的设备测试分开记录，rootfs 没有混入这次内核 Release。

## 七、2026-11-18：Nura + Phosh 尝试

用户希望复用 xaga 的 Nura 用户态、替换当前 rootfs，并保留官方或原有账户。

检索到的发布与构建记录没有给出可确认直接复用的 xaga Nura 成品。因此实际采用的是官方 Nura/Alpine 软件包组装 ARM64 用户态，再接入 Pearl，而不是把它写成“已经下载官方 xaga 镜像”。

构建继续在原生 ARM 编译机完成：

- Nura v26.06 / Alpine 3.24。
- Phosh 0.55、Phoc、greetd/phrog、OpenRC。
- 786 个签名软件包。
- 默认账户 `user / 147147`，UID 10000，root 锁定。
- Pearl 同次内核模块与所需固件。
- 4 GiB ext4 根文件系统，同时生成 Android sparse 和 zstd 文件。
- 独立的 OpenRC 启动整理服务与持续 USB ACM 日志通道，不再沿用复杂的 Omarchy 创建账户流程。

### 内部修订解决了什么

这些是同一套系统的内部修订，不是要求用户选择多套不同系统：

| 阶段 | 变化 |
|---|---|
| 初版 | 完成真实 Nura 用户态、模块和镜像 |
| 第二步 | 把 OpenRC 日志开关从错误的 `rc_log` 改成 `rc_logger` |
| 第三步 | 改善串口日志的跨块 CRLF 处理与终端异常时的文件描述符清理 |
| 第四步 | 修复空 `machine-id` 引发的 D-Bus 首启问题 |
| 最后整理 | 补入 105 份可移植固件，排除设备自己的 WIFI/BT_Addr 校准文件和零内容 SCP 占位文件 |

### 实际手机日志揭示的问题

用户发回的日志显示，设备当时运行的是较早的 `7.2.10++` 内核和第三步 rootfs，并非刚生成的最终组合：

- 根分区已挂载，CINIT 已交接到 `/sbin/init`。
- 空 `/etc/machine-id` 使 `dbus-uuidgen` 报错，导致启动整理、D-Bus、elogind 等服务连锁失败，greeter 退出。
- GPU 驱动初始化和触摸固件更新已有日志，但登录界面与完整桌面仍未通过。
- initramfs 里的旧 nvdata 路径也出现挂载失败，这是另一项启动输入问题，没有把它误写成 rootfs 账户问题。

我们修改了启动整理逻辑：空 ID 先移除再生成，D-Bus 使用同一个规范 ID，并给已启动的旧 rootfs 制作了一个小型数据块修复补丁。

最终镜像已在编译机生成、下载并整理到单一输出目录。**打成最终单一合集并完成交付的收尾尚未结束时，用户删除了本地材料，转向 GitHub Actions。** 因此这段工作记录为“构建与准备完成、完整最终交付被后续方向取代”，不写成整机桌面已经跑通。

## 八、2026-11-18：把构建迁移到 GitHub Actions

用户重新确定了更简单的目标：

1. 初始 Magma 配置放进本仓库，以后直接修改仓库文件。
2. 每次构建都克隆最新 Pearl 分支，不锁内核 commit。
3. 使用 Ubuntu 26 ARM64 Runner 与 Ubuntu 默认 Clang/LLVM，不固定工具链版本。
4. 内核名称只保留基础版本和 commit。
5. 构建成功后自动发布 Release。

实现落在 [构建脚本](scripts/build-kernel.sh)和[工作流](.github/workflows/build-kernel.yml)。[本仓库配置](configs/pearl.config)以后不被运行时外部下载覆盖，初始来源放在 [导入记录](configs/magma-import.json)。

### 去掉做重了的检验

实现过程中曾加上逐模块 ELF/vermagic 解析、归档字节比较、发布前远端摘要复核，以及一套模拟测试。用户明确要求去掉这些额外环节。

最终删除了那套验收脚本，把流程收敛为：

```text
clone 最新源码 → 复制仓库配置 → make → modules_install
→ 打包同次模块 → gh release create
```

脚本保留 `set -euo pipefail`：正常构建、下载或打包命令报错就停止，不再把通用二进制检验叠进受控编译流程。

### 第一轮 CI 结果

- 实现提交：[cddf882](https://github.com/cailuoli520/pearl-mainline/commit/cddf882ae23966a80d4eb5f7b00b330f1f192f6c)。
- [第一轮 Actions](https://github.com/cailuoli520/pearl-mainline/actions/runs/44263807132)成功完成。
- 该轮源码为 `904c3ce0833daf168911dffa30e7957fad3b0315`，版本为 `7.2.10-904c3ce0833d`。
- [对应 Release](https://github.com/cailuoli520/pearl-mainline/releases/tag/7.2.10-904c3ce0833d-build-1-a1)于当天自动发布。

这只是这一轮解析到的 commit 和版本；后续构建仍取执行时的最新 Pearl HEAD。

## 九、2026-11-18：原厂文件入库和本轮 boot/vendor_boot 打包

用户提供 `pearl_images_OS3.0.3.0.VLHCNXM_15.0/images` 目录，将其中五份原件放入 [stock](stock)：

- boot、vendor_boot。
- vbmeta、vbmeta_system、vbmeta_vendor。

提交为 [c902443](https://github.com/cailuoli520/pearl-mainline/commit/c902443166b322fe39719cf58069fdbc4d042a4f)。stock 中保留原版字节，三个 vbmeta 的禁用标志没有在原件上修改。

随后在编译机使用第一轮 CI 的内核、stock 模板和已有 CINIT initramfs，直接生成：

```text
boot.img
vendor_boot.img
pearl-boot-vendor-7.2.10-904c3ce0833d.zip
```

两份镜像各 64 MiB，合集约 45 MB，已本地交付。这些数字记录本轮成品，不是后续编译的硬性门槛。

本轮按对应原厂模板提取头部和 vendor 参数，替换 Linux 内核/ramdisk，生成 `NONE` footer，没有运行额外校验。initramfs 是以前制作的那份 Linux ramdisk，不是新内核源码或 Nura 自动生成的。

**这个手工封装步骤目前尚未接入 Actions，boot/vendor_boot 也没有冒称为内核工作流自动发布的文件。**

## 十、现在保留下来的做法

- 当前使用说明只写最新流程，历史样本细节放在本文件。
- 取最新 Pearl 分支；“最新”不等于其他机型分支或 Linux 通用主线。
- 配置由本仓库管理，Magma 只保留初始来源。
- 内核和模块使用同次构建；版本保持简短明确。
- stock 原件留作输入，封装结果单独生成，参数跟随实际模板。
- 把内核、initramfs、rootfs 的职责分开，不把未接入的流程写成已经自动完成。
- 构建失败就停止并看错误日志，不再添加那套过度验收。
- 新产物生成、文件交付、设备实际刷入、实体屏幕运行分别记录，不用旧成果替新组合下结论。
