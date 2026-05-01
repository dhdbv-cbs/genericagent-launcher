# macOS 手工 Smoke Checklist

适用范围：

- 当前仓库的 `未做 Apple Developer 签名 / 未 notarize 的 .dmg` 安装形态
- 不包含 Apple Developer 签名、notarization、内部更新器、私有 Python 安装器

## 安装与首次打开

1. 从 Release 或 CI 产物获取 `GenericAgentLauncher-macos-<version>.dmg`
2. 校对可选资产是否齐全：
   `GenericAgentLauncher-macos-<version>.sha256`、`README-macOS.txt`、`install-metadata.json`
   并打开 `install-metadata.json`，确认当前公开发布合同仍是 `build_arch = x86_64`、`runner_label = macos-15-intel`
3. 打开 `.dmg`，确认窗口内包含：
   `GenericAgent Launcher.app`、`Applications` 别名、`README-macOS.txt`
4. 把 app 拖到 `/Applications`（推荐）；如果只安装给当前用户，也可拖到 `~/Applications`
5. 从实际安装路径启动：默认是 `/Applications/GenericAgent Launcher.app`，用户级安装时则是 `~/Applications/GenericAgent Launcher.app`
6. 如果 Gatekeeper 拦截，先记录被阻拦提示，再到 `System Settings -> Privacy & Security -> Open Anyway` 放行
7. 如果当前系统版本仍提供该入口，再补测 Finder 右键 `Open` 作为兼容性备选路径
8. 进入主窗口后，确认关于页“安装状态”卡片能识别：
   当前 App 路径、推荐安装路径、用户数据目录、手动升级模式

## 首次环境准备

1. 在欢迎页测试“定位已有 GenericAgent”路径
2. 在欢迎页测试“下载 GenericAgent”路径
3. mac 模式下确认下载页没有私有 Python 安装器入口
4. 在“载入内核”页留空 `python_exe`，确认会自动尝试 `python3` / `python`，并在 Finder 启动场景下补试常见 Homebrew 绝对路径（如 `/opt/homebrew/bin/python3`、`/usr/local/bin/python3`）
5. 如果使用项目虚拟环境，手动填写 `venv/bin/python`，确认依赖检查能通过
6. 人为制造缺依赖场景时，确认依赖检查窗口会给出实时日志和失败说明

## 共享功能回归

1. 创建、切换、搜索、固定、删除会话
2. 打开 API 页面，保存配置并触发模型拉取
3. 打开通讯渠道页面，确认页面可加载、可保存、可刷新
4. 打开远程设备 / VPS 页面，完成最基础的连接测试
5. 启动并停止 LAN Web，确认状态文字和按钮状态正确
6. 验证无托盘场景下的悬浮窗 / 隐藏 / 恢复行为

## 手动升级

1. 下载更新版本的 `GenericAgentLauncher-macos-<version>.dmg`
2. 关闭当前 app
3. 手动替换 `/Applications/GenericAgent Launcher.app`；如果用户级安装，则改为手动替换 `~/Applications/GenericAgent Launcher.app`
4. 重新打开后确认：
   版本号更新
   原有用户数据仍在
   会话列表和配置未丢失

## 失败记录

每次 smoke 至少记录以下信息：

- 测试日期
- macOS 版本
- 芯片架构（Intel / Apple Silicon）
- Python 来源（系统 Python / Homebrew / 项目 venv）
- 失败步骤、报错截图、是否可复现
