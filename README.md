# GenericAgent Launcher

一个面向 [GenericAgent](https://github.com/lsdefine/GenericAgent) 的桌面启动器。

这个项目不是重新实现一套智能体内核，而是给 GenericAgent 提供一个更适合普通用户上手的 Windows 桌面入口，把下载、配置、启动、聊天和常见设置集中到一个界面里。

## 下载

- 直接下载安装包（推荐）：
  [Releases / GenericAgentLauncher-Setup-*.exe](https://github.com/dhdbv-cbs/genericagent-launcher/releases/latest)

如果你只是普通用户，优先使用 Release 页面里的安装包。

说明：

- `GenericAgentLauncher-Setup-*.exe` 是给用户手动安装 / 升级用的
- `GenericAgentLauncher-app-*.zip`、`manifest.json`、`manifest.sig`、`sha256sums.txt` 是启动器内部更新链路使用的发布资产，不是手动安装包

## 致谢

感谢 [GenericAgent 原作者 lsdefine](https://github.com/lsdefine/GenericAgent)。

本启动器建立在 GenericAgent 本体之上。上游项目负责核心 Agent 能力、模型调用、工具执行和原生前端支持；这个仓库主要负责桌面启动、配置整理和更直接的使用体验。

## 社区友链

- [LINUX DO](https://linux.do)

## 当前功能

- 图形化下载或定位 GenericAgent 项目目录
- 首次启动自动准备 `mykey.py`
- 卡片式 API 配置界面
- 聊天界面、会话侧边栏、会话搜索、固定/删除/重命名等基础会话管理
- 模型列表拉取和手动模型名输入
- 启动器会话与渠道会话分离管理
- 本机 / 远程设备双入口侧边栏
- 远程设备列表、自动 SSH 开关、按设备查看和创建启动器会话
- 悬浮窗 / 托盘模式
- 通讯渠道设置与日志查看
  当前已接入：`微信`、`Telegram`、`QQ`、`飞书`、`企业微信`、`钉钉`
- 微信扫码登录与重新绑定（支持本机和远端设备）
- 定时任务面板、执行记录与调度器启停
- 局域网 Web 接口启停与自启动
- VPS SSH 配置、连接测试、终端、一键 Docker 部署
- 启动器内部更新、更新诊断与失败回滚

## 社区协作

这个仓库希望按“可以被社区共同维护”的方向来整理，但目前仍以个人维护为主。

如果你想参与：

- 可复现 bug 请优先提 `Issues`
- 想法、方向和使用交流更适合提 `Discussions`
- 想直接提交代码，请先看 [Contributing](CONTRIBUTING.md)

仓库公开后，建议开启：

- `Issues`
- `Discussions`
- `Pull Requests`
- 默认分支保护

## 使用方式

### 安装包运行（推荐）

生产发布采用安装包架构：

- 安装目录：`%LocalAppData%\Programs\GenericAgentLauncher`
- 用户数据目录：`%LocalAppData%\GenericAgentLauncher`
- 更新机制：应用内检查 + 外部 `Updater` 原子切换 + 自动回滚
- 健康确认：`启动确认 + 存活确认` 两阶段校验，失败自动回滚
- 更新包校验：`Ed25519 签名 + SHA256`
- 更新诊断：关于页内置“更新诊断”卡片，可查看最近任务状态、错误码、`updater.log` 尾部

对普通用户来说，Release 页面里真正需要手动下载的通常只有安装包：

- `GenericAgentLauncher-Setup-<version>.exe`

其余更新资产用于应用内更新：

- `GenericAgentLauncher-app-<version>.zip`
- `manifest.json`
- `manifest.sig`
- `sha256sums.txt`

发布时需要配置更新签名私钥（CI 环境变量）：

- `UPDATE_SIGNING_PRIVATE_KEY_PEM`（GitHub Actions secret）
- `UPDATE_SIGNING_PUBLIC_KEY_PEM`（GitHub Actions secret）

但仍需要注意：

- GenericAgent 内核运行本身仍依赖系统 Python
- 首次下载或接入上游项目时，建议系统中已安装 Git

可选的生产加固环境变量：

- `GA_LAUNCHER_REQUIRE_AUTHENTICODE=1`
  强制更新后主程序必须通过 Windows Authenticode 校验
- `GA_LAUNCHER_HEALTH_MIN_ALIVE_SECONDS`
  更新后存活确认延时（默认 6 秒）
- `GA_LAUNCHER_HEALTH_STARTUP_TIMEOUT_SECONDS`
  启动确认超时（默认 45 秒）

### 从源码运行

如果你要开发、调试或自行打包：

```bash
pip install -r requirements.txt
python launcher.py
```

重新打包：

```bash
build.bat 0.1.8
```

`build.bat` 会自动查找 Inno Setup 编译器，查找顺序如下：

1. 环境变量 `INNO_ISCC` 指向的 `ISCC.exe`
2. 仓库内 `tools\InnoSetup\ISCC.exe`
3. 仓库内 `temp\InnoSetup\ISCC.exe`
4. 用户安装目录 `%LocalAppData%\Programs\Inno Setup 6`
5. 系统默认安装目录 `Program Files\Inno Setup 6`
6. `PATH` 中的 `iscc`

## 为什么源码文件不多

这个仓库现在已经拆成了明确的模块目录，不再维持“根目录大单文件”那种结构。

它本身只负责“启动器”这一层，而不是把 GenericAgent 整个内核重新实现一遍。当前主要结构是：

- `launcher.py`
  启动器统一入口，开发运行和打包都从这里启动
- `launcher_app/`
  启动器主包，包含 Qt 主窗口、主题系统和共享后端 facade
- `qt_chat_parts/`
  聊天界面拆分模块，负责会话列表、导航、下载页、聊天渲染、设置页等
- `launcher_core_parts/`
  启动器后端拆分模块，负责配置、会话、token 统计、模型接口、运行时辅助等
- `bridge.py`
  启动器和 GenericAgent 内核之间的桥接层，负责进程通信和事件转发
- `GenericAgentLauncher.spec`
  PyInstaller 打包配置
- `build.bat`
  Windows 下的打包脚本

也就是说：

- 这个仓库“文件少”，不代表内容少
- 现在主前端已经迁移到 Qt，不再以 Tk 为主架构
- 真正的 Agent 能力、模型调用、工具执行和原生前端仍然来自上游 GenericAgent

如果以后功能继续增加，仓库当然也可以再拆模块，但当前这种体量下，文件数量少本身并不是问题，关键是行为是否稳定、配置是否清晰、打包是否可靠

## 依赖说明

### 普通用户

如果你只运行已经打包好的 `exe`：

- 不需要安装启动器本身的 Python 依赖
- 不需要手动配置 Qt 运行环境

### GenericAgent 本体

无论你使用 exe 还是源码，GenericAgent 本体仍然有自己的运行依赖。最关键的是：

- 系统 Python
- Git

另外，某些通讯渠道还有各自的 Python 依赖，例如：

- `python-telegram-bot`
- `qq-botpy`
- `lark-oapi`
- `wecom_aibot_sdk`
- `dingtalk-stream>=0.20`
- 微信扫码链路相关依赖

这些依赖是否需要安装，取决于你是否真的启用对应渠道。

启动器的依赖检查默认使用 `auto` 安装策略：

- 优先尝试 `uv pip install --python <python_exe> ...`
- 若 `uv` 不可用或安装失败，自动回退到 `pip`

可通过环境变量调整：

- `GA_LAUNCHER_DEP_INSTALLER=auto|uv|pip`
- `GA_LAUNCHER_UV_EXE=<uv 可执行文件路径>`

## 快速开始

### 1. 获取本仓库

```bash
git clone <your-repo-url>
cd genericagent-launcher
```

### 2. 启动器启动

开发模式：

```bash
python launcher.py
```

打包后默认会生成：

```text
dist/GenericAgentLauncher/              主程序（onedir）
dist/LauncherBootstrap.exe              稳定启动入口
dist/Updater.exe                        外部更新器
release/<version>/installer/*.exe       安装包
release/<version>/update/*              内部更新资产
```

## 使用教程

### 自动下载 GenericAgent

1. 打开启动器。
2. 在欢迎页选择“下载 GenericAgent”。
3. 选择安装位置。
4. 点击“开始下载”。
5. 下载完成后，启动器会自动尝试载入内核。

### 使用已有的 GenericAgent 目录

1. 打开启动器。
2. 选择“定位已有 GenericAgent”。
3. 选中包含 `launch.pyw` 和 `agentmain.py` 的项目根目录。
4. 点击“载入内核”。

## API 配置

第一次进入时，如果没有可用渠道，启动器会自动引导到“设置 -> API”。

基本流程：

1. 打开“设置”。
2. 进入 `API`。
3. 添加一张 API 卡片。
4. 选择 API 格式。
5. 填写 URL、API Key、模型名。
6. 选择适配 GenericAgent 的模板。
7. 点击“仅保存”或“保存并重启内核”。

说明：

- `API 格式` 是协议层，如 `Claude 原生`、`Chat Completions`、`Responses`
- `链接模板` 是为了适配 GenericAgent 上游项目中的配置习惯，不等于协议本身

## 通讯渠道

设置页中的“通讯渠道”用于管理 GenericAgent 原项目支持的 Bot 前端。

当前接入：

- 微信
- Telegram / 纸飞机
- QQ
- 飞书
- 企业微信
- 钉钉

说明：

- 渠道凭证写入 `mykey.py`
- 是否自动启动由启动器自己的 `launcher_config.json` 管理
- 这些渠道会各自启动独立的 GenericAgent 进程，不与当前聊天页共用会话
- 微信卡片支持直接扫码登录 / 重新绑定，也支持远端设备 Token 同步
- 渠道并行能力以当前上游实现为准；如遇端口占用，优先升级到最新内核版本后再重试

## 远程设备 / VPS / 局域网

- 左侧边栏区分“本机”和“其他设备”，可按设备查看启动器会话与渠道会话
- 远程设备支持单独配置 `host / username / port / SSH key / agent_dir / python_cmd`
- 每台远程设备可单独开启或关闭“自动 SSH”，关闭后后台刷新与探测不会主动连接该设备
- 新建启动器会话时可直接选择本机或远程设备，会话会归档到对应设备分类下
- 部分设置支持按目标设备分别同步，不再一刀切共用
- VPS 面板支持：
  - SSH 连接测试
  - 终端连接
  - 远端 `mykey.py` 同步
  - 一键 Docker 部署
- 局域网接口面板支持：
  - 启动 / 停止局域网 Web 接口
  - 自启动
  - 查看访问地址与运行日志

## 聊天界面

### 发消息

- 在底部输入框内输入内容
- 点击“发送”
- 或直接按 `Enter`
- `Shift+Enter` 换行

### 中断生成

- 回复进行中时，右下会显示“中断”
- 点击后会向内核发送停止信号
- 当前已经生成出的内容会尽量保留，并在消息末尾标记为已中断

### 会话管理

- 左侧侧边栏可以新建、切换、搜索和管理会话
- 新建启动器会话时可选择目标设备
- 远程设备会话会通过 SSH 同步远端缓存
- 右键会话卡片可进行更多操作
- 主窗口可缩小到托盘，并保留悬浮对话窗继续使用

## 项目结构

```text
launcher.py                 启动器统一入口
launcher_bootstrap.py       安装版稳定入口
updater.py                  外部更新器入口
launcher_app/               Qt 主界面 + 主题 + 共享后端 facade
qt_chat_parts/              聊天前端拆分模块
launcher_core_parts/        启动器后端拆分模块
bridge.py                   启动器与 GenericAgent 内核之间的桥接层
build.bat                   Windows 打包脚本
GenericAgentLauncher.spec   PyInstaller 打包配置
LauncherBootstrap.spec      Bootstrap 打包配置
Updater.spec                Updater 打包配置
installer/                  Inno Setup 安装脚本
tools/build_release_bundle.py  发布包与内部更新资产生成脚本
requirements.txt            启动器依赖
```

## 已知说明

- 本仓库是 GenericAgent 的桌面启动器，不是上游项目本体
- 如果修改源码后 exe 看起来没变化，通常是旧进程或旧打包产物仍在被使用
- 配置文件与更新状态默认放在 `%LocalAppData%\GenericAgentLauncher\config` 与 `state`
- 启动器内部更新依赖签名后的 `manifest.json / manifest.sig / app zip` 资产；如果只做手动发版，普通用户只需要安装包

## License

本仓库使用 [MIT License](LICENSE)。

上游 [GenericAgent](https://github.com/lsdefine/GenericAgent) 同样使用 MIT License，但两者仍然是不同仓库。发布和分发本启动器时，建议同时保留对上游项目的致谢说明。
