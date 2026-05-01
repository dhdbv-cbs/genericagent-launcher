# macOS Release Runbook

适用范围：

- 当前仓库的 `未做 Apple Developer 签名 / 未 notarize 的 .dmg` 公开发布形态
- 不包含 Apple Developer 账号、Developer ID 签名、notarization、内部更新器

## 发版前提

发布前必须同时满足：

1. `python -m pytest tests -q` 通过
2. `macos-validate` workflow 通过
3. 至少一份真实 mac 设备 smoke 记录通过
4. 公开资产合同完整：
   `.dmg`、`.sha256`、`README-macOS.txt`、`install-metadata.json`

## 发版步骤

### 1. 选择版本号

- 版本号沿用 `x.y.z`
- tag 形式固定为 `vx.y.z`

### 2. 本地预检查

执行：

```bash
python -m pytest tests -q
```

确认：

- README 中 mac 安装说明仍是 `未做 Apple Developer 签名 / 未 notarize 的 dmg 手动安装`
- 手动升级说明仍是 `手动替换当前实际安装路径中的 GenericAgent Launcher.app`（默认是 `/Applications/...`，如果用户级安装则是 `~/Applications/...`）
- 没有把产品边界重新扩散到 Apple Developer 签名、公证或内部更新器

### 3. 触发 release workflow

两种方式都可以：

- 推送 tag：`v<version>`
- GitHub Actions `workflow_dispatch`，输入 `version`

### 4. CI 期望结果

`build-macos` 必须完成：

1. `python -m pytest tests -q`
2. source startup smoke
3. `tools/build_macos_release.py`
4. packaged app startup smoke
5. `tools/validate_macos_release.py`
6. 上传 Release 资产

### 5. 公开资产核对

Release 页面必须出现：

- `GenericAgentLauncher-macos-<version>.dmg`
- `GenericAgentLauncher-macos-<version>.sha256`
- `README-macOS.txt`
- `install-metadata.json`

其中 `install-metadata.json` 必须反映：

- `platform = macos`
- `install_mode = manual_dmg`
- `recommended_install_target = /Applications/GenericAgent Launcher.app`
- `user_install_target = ~/Applications/GenericAgent Launcher.app`
- `supports_internal_updater = false`
- `requires_system_python = true`
- `build_arch = x86_64`（当前 GitHub `macos-15-intel` 发布合同）
- `runner_label = macos-15-intel`
- `developer_id_signed = false`
- `apple_developer_signed = false`
- `notarized = false`
- `pyinstaller_may_ad_hoc_sign = true`
- `version / commit / build_time` 有值且和本次发布一致

### 6. 人工 smoke

按下面文档执行：

- [macos-manual-smoke-checklist.md](./macos-manual-smoke-checklist.md)
- [macos-smoke-report-template.md](./macos-smoke-report-template.md)

至少覆盖：

- Finder 拖入 `/Applications`（或在用户级安装场景下拖入 `~/Applications`）
- Gatekeeper 阻拦后走 `System Settings -> Privacy & Security -> Open Anyway`
- Finder 右键 `Open` 作为兼容性备选路径
- 系统 Python 自动探测
- 手动指定 `venv/bin/python`
- 手动替换 `.app` 升级

### 7. CI 诊断证据

`build-macos` 日志中还应保留以下诊断输出，便于追查 runner/产物漂移：

- `uname -m`
- `sw_vers`
- `file` / `lipo -info` 针对 `Contents/MacOS/GenericAgentLauncher`
- `codesign -dv --verbose=4`
- `codesign --verify --deep --strict`
- `spctl --assess --type execute --verbose=4`（允许失败，仅作 Gatekeeper 诊断）

## 发布后记录

每次公开 release 至少保留以下证据：

- workflow 链接
- 通过的 tag / commit sha
- smoke 报告
- 如果存在已知问题，写清楚阻断程度和临时绕过方式
