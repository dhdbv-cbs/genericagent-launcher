# GenericAgent Launcher

一个面向 [GenericAgent](https://github.com/lsdefine/GenericAgent) 的桌面启动器。

这个项目不是重新实现一套智能体内核，而是给 GenericAgent 提供一个更适合普通用户上手的 Windows 桌面入口，把下载、配置、启动、聊天和常见设置集中到一个界面里。

## 下载

- 直接下载 exe：
  [Releases / GenericAgentLauncher.exe](https://github.com/dhdbv-cbs/genericagent-launcher/releases/latest)

如果你只是普通用户，优先使用 Release 页面里的 `GenericAgentLauncher.exe`。

## 致谢

感谢 [GenericAgent 原作者 lsdefine](https://github.com/lsdefine/GenericAgent)。

本启动器建立在 GenericAgent 本体之上。上游项目负责核心 Agent 能力、模型调用、工具执行和原生前端支持；这个仓库主要负责桌面启动、配置整理和更直接的使用体验。

## 社区友链

- [LINUX DO](https://linux.do)

## 当前功能

- 图形化下载或定位 GenericAgent 项目目录
- 首次启动自动准备 `mykey.py`
- 卡片式 API 配置界面
- 聊天界面、会话侧边栏、会话搜索、基础会话管理
- 模型列表拉取和手动模型名输入
- 通讯渠道设置
  当前已接入：`微信`、`Telegram`、`QQ`、`飞书`、`企业微信`、`钉钉`
- 设置页占位入口
  当前保留：`定时任务`、`使用计数`、`关于`

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

### 直接使用 exe

如果你只是想使用启动器，直接运行：

```text
dist/GenericAgentLauncher.exe
```

这种方式下：

- 不需要安装启动器自己的 Python 依赖
- 不需要执行 `pip install -r requirements.txt`
- 启动器界面依赖已经被打进 exe

但仍需要注意：

- GenericAgent 内核运行本身仍依赖系统 Python
- 首次下载或接入上游项目时，建议系统中已安装 Git

### 从源码运行

如果你要开发、调试或自行打包：

```bash
pip install -r requirements.txt
python launcher.py
```

重新打包：

```bash
build.bat
```

## 为什么源码文件不多

这个仓库的源码文件看起来比较少，这是正常的。

原因是这个仓库本身只负责“启动器”这一层，而不是把 GenericAgent 整个内核重新实现一遍。当前主要结构就是：

- `launcher.py`
  启动器主界面，包含下载、聊天、设置、API 配置、通讯渠道管理等桌面交互
- `bridge.py`
  启动器和 GenericAgent 内核之间的桥接层，负责进程通信和事件转发
- `GenericAgentLauncher.spec`
  PyInstaller 打包配置
- `build.bat`
  Windows 下的打包脚本

也就是说：

- 这个仓库“文件少”，不代表内容少
- 主要功能大量集中在 `launcher.py`
- 真正的 Agent 能力、模型调用、工具执行和原生前端仍然来自上游 GenericAgent

如果以后功能继续增加，仓库当然也可以再拆模块，但当前这种体量下，文件数量少本身并不是问题，关键是行为是否稳定、配置是否清晰、打包是否可靠

## 依赖说明

### 普通用户

如果你只运行已经打包好的 `exe`：

- 不需要安装启动器本身的 Python 依赖
- 不需要手动配置 `customtkinter`

### GenericAgent 本体

无论你使用 exe 还是源码，GenericAgent 本体仍然有自己的运行依赖。最关键的是：

- 系统 Python
- Git

另外，某些通讯渠道还有各自的 Python 依赖，例如：

- `python-telegram-bot`
- `qq-botpy`
- `lark-oapi`
- `wecom_aibot_sdk`
- `dingtalk-stream`
- 微信扫码链路相关依赖

这些依赖是否需要安装，取决于你是否真的启用对应渠道。

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

打包产物默认在：

```text
dist/GenericAgentLauncher.exe
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
- 微信卡片支持直接扫码登录 / 重新绑定
- `QQ` 和 `微信` 沿用上游单实例限制，不能同时启动

## 聊天界面

### 发消息

- 在底部输入框内输入内容
- 点击“发送”
- 或使用 `Ctrl+Enter`

### 中断生成

- 回复进行中时，右下会显示“中断”
- 点击后会向内核发送停止信号
- 当前已经生成出的内容会尽量保留，并在消息末尾标记为已中断

### 会话管理

- 左侧侧边栏可以新建、切换、搜索和管理会话
- 右键会话卡片可进行更多操作

## 项目结构

```text
launcher.py                 启动器主界面
bridge.py                   启动器与 GenericAgent 内核之间的桥接层
build.bat                   Windows 打包脚本
GenericAgentLauncher.spec   PyInstaller 打包配置
requirements.txt            启动器依赖
```

## 已知说明

- 本仓库是 GenericAgent 的桌面启动器，不是上游项目本体
- 某些设置页仍处于占位阶段
- 如果修改源码后 exe 看起来没变化，通常是旧进程或旧打包产物仍在被使用
- 打包后的配置文件默认读取启动器所在目录下的 `launcher_config.json`

## License

本仓库使用 [MIT License](LICENSE)。

上游 [GenericAgent](https://github.com/lsdefine/GenericAgent) 同样使用 MIT License，但两者仍然是不同仓库。发布和分发本启动器时，建议同时保留对上游项目的致谢说明。
