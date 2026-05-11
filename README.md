# GenericAgent Launcher

一个面向 [GenericAgent](https://github.com/lsdefine/GenericAgent) 的桌面启动器。

它不重新实现 GenericAgent 内核，而是把下载、配置、启动、聊天、渠道管理、远程设备接入和常用运维入口整理成一个更适合普通用户使用的桌面界面。当前公开发布面向 `Windows` 和 `macOS`。

## 这个项目解决什么问题

如果你直接使用上游 GenericAgent，通常还要自己处理这些事情：

- 下载或同步项目源码
- 准备 Python、Git 和渠道依赖
- 维护 `mykey.py`、启动参数和运行目录
- 管理聊天入口、渠道入口和远程设备
- 处理 Windows 安装、升级和回滚

这个仓库主要就是把上面这些“启动器层”的工作接住。

## 下载

Release 页面：
[Releases](https://github.com/dhdbv-cbs/genericagent-launcher/releases/latest)

### Windows

普通用户手动安装或升级，下载：

- `GenericAgentLauncher-Setup-<version>.exe`

用于软件内更新识别的发布资产：

- `GenericAgentLauncher-app-<version>.zip`
- `manifest.json`
- `manifest.sig`
- `sha256sums.txt`

说明：

- 手动安装只需要 `Setup.exe`
- 其余 4 个文件是 Windows 启动器内部更新链路使用的资产，不是手动安装包
- Windows 发布采用安装包 + 外部 `Updater` + 原子切换 + 自动回滚

### macOS

当前公开发布提供两个架构：

- `GenericAgentLauncher-macos-arm64-<version>.dmg`
- `GenericAgentLauncher-macos-x86_64-<version>.dmg`

配套校验和说明资产：

- `GenericAgentLauncher-macos-arm64-<version>.sha256`
- `GenericAgentLauncher-macos-x86_64-<version>.sha256`
- `README-macOS-arm64.txt`
- `README-macOS-x86_64.txt`
- `install-metadata-arm64.json`
- `install-metadata-x86_64.json`

说明：

- `arm64` 面向 Apple Silicon Mac
- `x86_64` 面向 Intel Mac
- macOS 当前不支持应用内自动更新，只支持手动替换 `.app`
- macOS 当前不提供 Apple Developer 签名和 notarization
- 首次被 Gatekeeper 拦截时，先尝试启动一次，再到 `System Settings -> Privacy & Security -> Open Anyway` 放行

## 当前能力

- 图形化下载或定位 GenericAgent 项目目录
- 首次启动自动准备 `mykey.py`
- 聊天界面、会话管理、搜索、固定、删除、重命名
- API 卡片配置、模型列表拉取、手动模型名输入
- 启动器会话与渠道会话分离管理
- 本机 / 远程设备双入口侧边栏
- 微信、Telegram、QQ、飞书、企业微信、钉钉等渠道配置
- 微信扫码登录与重新绑定
- 定时任务面板、执行记录与调度器启停
- 局域网 Web 接口启停与自启动
- VPS SSH 配置、连接测试、终端、一键 Docker 部署
- Windows 内部更新、更新诊断与失败回滚
- macOS 安装状态检查、手动升级说明与 dmg 安装提示

## 普通用户快速开始

### 1. 安装启动器

- Windows：运行 `GenericAgentLauncher-Setup-<version>.exe`
- macOS：下载对应架构的 `.dmg`，把 `GenericAgent Launcher.app` 拖到 `/Applications` 或 `~/Applications`

### 2. 准备 GenericAgent

进入启动器后，你可以二选一：

- 直接下载 GenericAgent
- 定位已有的 GenericAgent 项目目录

如果是已有目录，目标目录里通常应包含 `launch.pyw` 和 `agentmain.py`。

### 3. 配置 API

第一次进入如果没有可用渠道，启动器通常会引导到“设置 -> API”。

基础流程：

1. 新建 API 卡片
2. 选择 API 格式
3. 填写 URL、API Key、模型名
4. 选择适配 GenericAgent 的模板
5. 点击“仅保存”或“保存并重启内核”

### 4. 开始聊天或接入渠道

- 如果你只想直接聊天，进入聊天页即可
- 如果你要接 Telegram、微信等渠道，去“设置 -> 通讯渠道”
- 如果你要接远程设备或 VPS，去“设置 -> VPS / 远程设备”

## 平台差异

### Windows

- 安装目录：`%LocalAppData%\\Programs\\GenericAgentLauncher`
- 数据目录：`%LocalAppData%\\GenericAgentLauncher`
- 升级方式：支持软件内更新
- 安全校验：`Ed25519 manifest 签名 + SHA256`
- 失败保护：外部更新器 + 自动回滚

### macOS

- 推荐安装路径：`/Applications/GenericAgent Launcher.app`
- 允许的用户级路径：`~/Applications/GenericAgent Launcher.app`
- 数据目录：`~/Library/Application Support/GenericAgentLauncher`
- 升级方式：下载新的 `.dmg` 后手动替换 `.app`
- 当前限制：无内部更新、无 Apple Developer 签名、无 notarization

## 依赖说明

### 启动器本身

如果你使用已经打包好的程序：

- Windows 安装包用户不需要自己配置启动器的 Python 依赖
- macOS `.app` 用户同样不需要手动配置 Qt 运行环境

### GenericAgent 本体

无论你使用 Windows 安装包、macOS `.app`，还是直接跑源码，GenericAgent 本体仍然依赖：

- 系统 Python
- Git

部分通讯渠道还会有各自的 Python 依赖，例如：

- `python-telegram-bot`
- `qq-botpy`
- `lark-oapi`
- `wecom_aibot_sdk`
- `dingtalk-stream>=0.20`

是否需要安装这些依赖，取决于你是否真的启用对应渠道。

macOS 当前固定依赖系统 Python。启动器会优先尝试 `python` / `python3`，并在 Finder 启动场景下补试常见 Homebrew 路径；如果你已有虚拟环境，也可以在启动器里手动指定 `venv/bin/python`。

## 从源码运行

如果你要开发、调试或自行打包：

```bash
pip install -r requirements.txt
python launcher.py
```

## 打包

### 版本真源

发布前先更新：

```text
release/VERSION
```

Windows 和 macOS 的发包流程都应以这个文件为准；如果显式传入版本参数，传入值也必须与它一致。

### Windows 本地打包

```bash
build.bat
```

如果要在本地生成可用于内部更新的签名资产，可以先生成一套本地密钥：

```bash
python tools/generate_update_signing_keypair.py
```

默认会生成：

- `local_keys/update_signing_private_key.pem`
- `local_keys/update_signing_public_key.pem`
- `update_public_key.pem`

其中：

- `local_keys/` 已忽略，不会进 Git
- `build.bat` 检测到本地 key 后会自动用于内部更新签名
- `local_keys/update_signing_private_key.pem` 只用于 `manifest.json` / `manifest.sig` 的内部更新签名，不是 Windows `exe/installer` 的 Authenticode 程序签名私钥
- 当前仓库默认打包链路不包含 `signtool` 或 Inno Setup 程序签名步骤；如果你看到 `build.bat` 成功，只能说明更新资产已签，不代表 Windows 主程序、`Updater.exe` 或 `Setup.exe` 已做程序签名
- 正式发布不允许 unsigned manifest 兜底

Windows 打包完成后，常见产物包括：

- `release/<version>/installer/GenericAgentLauncher-Setup-<version>.exe`
- `release/<version>/update/GenericAgentLauncher-app-<version>.zip`
- `release/<version>/update/manifest.json`
- `release/<version>/update/manifest.sig`
- `release/<version>/update/sha256sums.txt`

### macOS 本地打包

```bash
python tools/build_macos_release.py --version "$(python tools/resolve_release_version.py)" --out release
```

说明：

- 必须在 macOS 上执行
- 当前产物是 GitHub 分发用的手动安装包
- 不做 Apple Developer 签名和 notarization

常见产物包括：

- `release/<version>/macos/GenericAgent Launcher.app`
- `release/<version>/macos/GenericAgentLauncher-macos-<arch>-<version>.dmg`
- `release/<version>/macos/GenericAgentLauncher-macos-<arch>-<version>.sha256`
- `release/<version>/macos/README-macOS.txt`
- `release/<version>/macos/install-metadata.json`

## 项目结构

```text
launcher.py                        启动器统一入口
launcher_bootstrap.py              Windows 安装版稳定入口
updater.py                         Windows 外部更新器入口
launcher_app/                      Qt 主界面、主题、共享后端 facade
qt_chat_parts/                     聊天前端拆分模块
launcher_core_parts/               启动器后端拆分模块
bridge.py                          启动器与 GenericAgent 内核之间的桥接层
installer/                         Inno Setup 安装脚本
build.bat                          Windows 打包脚本
GenericAgentLauncher.spec          Windows 主程序打包配置
LauncherBootstrap.spec             Windows 启动入口打包配置
Updater.spec                       Windows 外部更新器打包配置
GenericAgentLauncher.mac.spec      macOS PyInstaller 打包配置
release/VERSION                    当前发布版本真源
tools/resolve_release_version.py   发布版本解析与一致性校验脚本
tools/build_release_bundle.py      Windows 发布包与内部更新资产生成脚本
tools/build_macos_release.py       macOS release bundle 生成脚本
```

## 协作建议

如果你想参与维护：

- 可复现 bug 优先提 `Issues`
- 使用交流和方向讨论更适合提 `Discussions`
- 想直接提交代码，请先看 [Contributing](CONTRIBUTING.md)

## 社区友链

本项目目前也在这个社区持续推广和交流：

- [LINUX DO](https://linux.do)

## 致谢

感谢 [GenericAgent 原作者 lsdefine](https://github.com/lsdefine/GenericAgent)。

本仓库只负责桌面启动器层，不替代上游 GenericAgent 本体。核心 Agent 能力、模型调用、工具执行和原生前端支持仍然来自上游项目。

## License

本仓库使用 [MIT License](LICENSE)。

上游 [GenericAgent](https://github.com/lsdefine/GenericAgent) 同样使用 MIT License，但两者仍然是不同仓库。分发本启动器时，建议同时保留对上游项目的致谢说明。
