# Pearl 内核配置

`pearl.config` 是本仓库唯一的构建配置入口。直接修改并提交此文件即可改变下一次构建；Actions 不会在运行时下载 Magma 配置覆盖它。

初始配置导入自：

- 仓库：`Magma-71453/redmi-note-12t-pro-archlinuxarm`
- 提交：`5afcd01c0e649a32f71f2413cb7823271911aedc`
- 路径：`artifacts/kernel-7.2.10++/config`
- Git blob：`3e9417ad3335bad115e6f65c62211e6aeb8beacd`
- 原始 SHA256：`870afb391e9eb1bf4eb19cda7fe1485b59a0478ebebc95e6e11d9541f2050508`

首次导入仅把 `CONFIG_LOCALVERSION="+"` 清空，其他选项保留。导入记录见 `magma-import.json`；它只记录来源，不是后续配置内容的锁定校验。

## 由 CI 管理的版本字段

构建时只在临时源码目录中的 `.config` 设置：

```text
CONFIG_LOCALVERSION="-<内核提交前12位>"
# CONFIG_LOCALVERSION_AUTO is not set
```

每次 `make` 显式传入 `LOCALVERSION=`，最终版本为 `内核基础版本-内核commit12`，例如 `7.2.10-904c3ce0833d`。这两个字段由 CI 负责，其他配置由本仓库文件决定。

`olddefconfig` 使用此次 Ubuntu 默认编译器与最新 Pearl 源码迁移配置，迁移后的完整 `kernel.config` 随产物发布。脚本使用 `set -euo pipefail`，拉取、编译或打包失败立即停止，不追加逐模块解析、二次解压或远端摘要复核。

内核、DTB 和模块来自同一次编译。额外 Magma 源码补丁为 0。不同配置或不同 Ubuntu 默认工具链即使得到相同版本字符串，也应按对应 Release 整套使用；不要手动混合不同构建的模块。
