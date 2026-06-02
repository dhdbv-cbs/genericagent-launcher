from __future__ import annotations

import threading

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from launcher_app import core as lz
from launcher_app.theme import C, F

from .common import NoWheelComboBox, capture_runtime_context, runtime_context_matches

_API_ADVANCED_FIELD_META = {
    "fake_cc_system_prompt": {
        "label": "fake_cc_system_prompt",
        "kind": "bool",
        "checkbox_label": "启用 Claude Code 兼容参数",
    },
    "thinking_type": {
        "label": "thinking_type",
        "kind": "choice",
        "choices": ["adaptive", "enabled", "disabled"],
    },
    "thinking_budget_tokens": {
        "label": "thinking_budget_tokens",
        "kind": "int",
        "placeholder": "整数；留空则跟随模板默认",
    },
    "reasoning_effort": {
        "label": "reasoning_effort",
        "kind": "choice",
        "choices": ["minimal", "low", "medium", "high", "xhigh"],
    },
    "temperature": {
        "label": "temperature",
        "kind": "float",
        "placeholder": "浮点数；留空则跟随模板默认",
    },
    "max_tokens": {
        "label": "max_tokens",
        "kind": "int",
        "placeholder": "整数；留空则跟随模板默认",
    },
    "stream": {
        "label": "stream",
        "kind": "bool",
        "checkbox_label": "启用流式响应",
    },
    "max_retries": {
        "label": "max_retries",
        "kind": "int",
        "placeholder": "整数；留空则跟随模板默认",
    },
    "base_delay": {
        "label": "base_delay",
        "kind": "float",
        "placeholder": "浮点秒数；留空则跟随模板默认",
    },
    "spring_back": {
        "label": "spring_back",
        "kind": "bool",
        "checkbox_label": "启用 spring_back",
    },
    "connect_timeout": {
        "label": "connect_timeout",
        "kind": "int",
        "placeholder": "整数秒；留空则跟随模板默认",
    },
    "read_timeout": {
        "label": "read_timeout",
        "kind": "int",
        "placeholder": "整数秒；留空则跟随模板默认",
    },
    "context_win": {
        "label": "context_win",
        "kind": "int",
        "placeholder": "上下文窗口；留空则跟随模板默认",
    },
    "user_agent": {
        "label": "user_agent",
        "kind": "text",
        "placeholder": "例如 claude-cli/2.1.113 (external, cli)",
    },
}

_API_KIND_ADVANCED_FIELDS = {
    "native_claude": [
        "fake_cc_system_prompt",
        "thinking_type",
        "thinking_budget_tokens",
        "reasoning_effort",
        "temperature",
        "max_tokens",
        "stream",
        "max_retries",
        "connect_timeout",
        "read_timeout",
        "context_win",
        "user_agent",
    ],
    "native_oai": [
        "reasoning_effort",
        "temperature",
        "max_tokens",
        "stream",
        "max_retries",
        "connect_timeout",
        "read_timeout",
        "context_win",
    ],
    "mixin": [
        "max_retries",
        "base_delay",
        "spring_back",
    ],
}


class ApiEditorMixin:
    def _launcher_is_closing(self) -> bool:
        return bool(getattr(self, "_closing_in_progress", False) or getattr(self, "_force_exit_requested", False))

    def _qt_object_alive(self, obj) -> bool:
        if obj is None:
            return False
        try:
            return bool(isValid(obj))
        except Exception:
            return False

    def _api_on_ui_thread(self, fn):
        callback = fn if callable(fn) else (lambda: None)
        owner = self
        app = QApplication.instance()

        def run():
            if not self._qt_object_alive(owner):
                return
            if self._launcher_is_closing():
                return
            try:
                callback()
            except RuntimeError as e:
                text = str(e or "")
                if "already deleted" in text or "Internal C++ object" in text:
                    return
                raise

        try:
            if self._qt_object_alive(app):
                QTimer.singleShot(0, app, run)
            else:
                QTimer.singleShot(0, run)
        except RuntimeError:
            pass

    def _api_combo_style(self):
        return (
            f"QComboBox {{ background: {C['field_bg']}; color: {C['text']}; "
            f"border: 1px solid {C['stroke_default']}; border-radius: {F['radius_md']}px; "
            f"padding: 6px 28px 6px 10px; min-height: 20px; }}"
            f"QComboBox:hover {{ border-color: {C['stroke_hover']}; }}"
            f"QComboBox:focus {{ border-color: {C['stroke_focus']}; }}"
            f"QComboBox::drop-down {{ border: none; width: 22px; }}"
            f"QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; "
            f"border-left: 5px solid transparent; border-right: 5px solid transparent; "
            f"border-top: 6px solid {C['muted']}; margin-right: 8px; }}"
            f"QComboBox QAbstractItemView {{ background: {C['layer1']}; color: {C['text']}; "
            f"border: 1px solid {C['stroke_hover']}; border-radius: {F['radius_md']}px; padding: 4px; "
            f"selection-background-color: {C['accent_soft_bg']}; selection-color: {C['text']}; outline: 0; }}"
            f"QComboBox QLineEdit {{ background: transparent; color: {C['text']}; border: none; padding: 0; }}"
        )

    def _api_format_options(self):
        return [lz.SIMPLE_FORMAT_LABEL[k] for k in ("claude_native", "oai_chat", "oai_responses", "mixin")]

    def _api_format_from_label(self, label):
        for format_key, txt in lz.SIMPLE_FORMAT_LABEL.items():
            if txt == label:
                return format_key
        return "oai_chat"

    def _api_format_meta(self, format_key):
        return lz.SIMPLE_FORMAT_RULES.get(format_key) or lz.SIMPLE_FORMAT_RULES["oai_chat"]

    def _api_template_choices(self, format_key):
        keys = self._api_format_meta(format_key).get("templates", [])
        return [(k, lz.TEMPLATE_INDEX[k]["label"]) for k in keys if k in lz.TEMPLATE_INDEX]

    def _api_infer_template_key(self, kind, data):
        best_key = None
        best_score = -1
        for tpl_key, meta in lz.TEMPLATE_INDEX.items():
            if meta.get("kind") != kind:
                continue
            defaults = dict(meta.get("defaults") or {})
            if not defaults:
                continue
            matched = True
            score = 0
            for dk, dv in defaults.items():
                if dk == "apibase" and dv == "":
                    continue
                if data.get(dk) != dv:
                    matched = False
                    break
                score += 1
            if matched and score > best_score:
                best_key = tpl_key
                best_score = score
        if best_key:
            return best_key
        if kind == "native_claude":
            return "custom-claude"
        if kind == "mixin":
            return "mixin"
        return "custom-oai"

    def _api_infer_format_key(self, kind, data):
        if kind == "native_claude":
            return "claude_native"
        if kind == "native_oai":
            return "oai_responses" if data.get("api_mode") == "responses" else "oai_chat"
        if kind == "mixin":
            return "mixin"
        return "oai_chat"

    def _api_prune_managed_extra(self, raw_extra, *, drop_template=False, drop_format=False):
        extra = dict(raw_extra or {})
        if drop_template:
            for key in lz.TEMPLATE_MANAGED_KEYS:
                extra.pop(key, None)
        if drop_format:
            extra.pop("api_mode", None)
        return extra

    def _api_make_simple_state(self, cfg):
        data = dict(cfg.get("data") or {})
        kind = cfg.get("kind") or "native_oai"
        format_key = self._api_infer_format_key(kind, data)
        tpl_key = self._api_infer_template_key(kind, data)
        stored_name = str(data.get("name") or "").strip()
        valid_tpl_keys = {k for k, _ in self._api_template_choices(format_key)}
        if tpl_key not in valid_tpl_keys:
            if kind == "native_claude":
                tpl_key = "custom-claude"
            elif kind == "mixin":
                tpl_key = "mixin"
            else:
                tpl_key = "custom-oai"
        advanced_values = {}
        for key in self._api_known_advanced_keys_for_kind(kind):
            if key in data:
                advanced_values[key] = data.get(key)
        raw_extra = dict(data)
        for key in ("name", "apikey", "apibase", "model", "user_agent", "llm_nos"):
            raw_extra.pop(key, None)
        for key in _API_ADVANCED_FIELD_META:
            raw_extra.pop(key, None)
        for key in lz.TEMPLATE_INDEX.get(tpl_key, {}).get("defaults", {}):
            raw_extra.pop(key, None)
        raw_extra.pop("api_mode", None)
        state = {
            "var": cfg["var"],
            "name": stored_name,
            "persisted_name": stored_name,
            "auto_name_locked": False,
            "format": format_key,
            "tpl_key": tpl_key,
            "apibase": data.get("apibase", ""),
            "apikey": data.get("apikey", ""),
            "model": data.get("model", lz.TEMPLATE_INDEX.get(tpl_key, {}).get("defaults", {}).get("model", "")),
            "advanced_values": advanced_values,
            "advanced_expanded": False,
            "raw_extra": raw_extra,
            "model_choices": [],
            "model_status": "",
            "model_fetching": False,
        }
        if kind == "mixin":
            state["llm_nos"] = list(data.get("llm_nos") or [])
        return state

    def _api_default_model_for_state(self, state):
        tpl_key = state.get("tpl_key")
        return str(lz.TEMPLATE_INDEX.get(tpl_key, {}).get("defaults", {}).get("model", "") or "").strip()

    def _api_state_kind(self, state):
        fmt = self._api_format_meta((state or {}).get("format"))
        tpl = lz.TEMPLATE_INDEX.get((state or {}).get("tpl_key"), {})
        return fmt.get("kind") or tpl.get("kind") or "native_oai"

    def _api_sync_state_var_kind(self, state, kind=None):
        target_kind = kind or self._api_state_kind(state)
        existing = {s.get("var") for s in self._qt_api_state if s is not state}
        existing.update({c.get("var") for c in self._qt_api_hidden_configs})
        state["var"] = lz.sync_config_var_kind(target_kind, state.get("var"), existing)
        return state["var"]

    def _api_known_advanced_keys_for_kind(self, kind):
        return list(_API_KIND_ADVANCED_FIELDS.get(kind, []))

    def _api_advanced_field_keys(self, state):
        kind = self._api_state_kind(state)
        ordered = []
        for key in self._api_known_advanced_keys_for_kind(kind):
            if key not in ordered:
                ordered.append(key)
        for key in (lz.TEMPLATE_INDEX.get((state or {}).get("tpl_key"), {}).get("defaults") or {}):
            if key in _API_ADVANCED_FIELD_META and key not in ordered:
                ordered.append(key)
        for key in ((state or {}).get("advanced_values") or {}):
            if key in _API_ADVANCED_FIELD_META and key not in ordered:
                ordered.append(key)
        return ordered

    def _api_advanced_value(self, state, key):
        values = dict((state or {}).get("advanced_values") or {})
        if key in values:
            return values.get(key)
        defaults = dict(lz.TEMPLATE_INDEX.get((state or {}).get("tpl_key"), {}).get("defaults") or {})
        if key in defaults:
            return defaults.get(key)
        meta = _API_ADVANCED_FIELD_META.get(key) or {}
        return False if meta.get("kind") == "bool" else ""

    def _api_set_advanced_value(self, state, key, value):
        values = dict((state or {}).get("advanced_values") or {})
        meta = _API_ADVANCED_FIELD_META.get(key) or {}
        kind = meta.get("kind")
        if kind == "bool":
            values[key] = bool(value)
        else:
            text = "" if value is None else str(value).strip()
            if text:
                values[key] = text
            else:
                values.pop(key, None)
        state["advanced_values"] = values

    def _api_normalize_advanced_value(self, key, value):
        meta = _API_ADVANCED_FIELD_META.get(key) or {}
        kind = meta.get("kind")
        if kind == "bool":
            return bool(value)
        text = str(value or "").strip()
        if not text:
            return None
        if kind == "int":
            try:
                return int(text)
            except Exception as e:
                raise ValueError(f"{meta.get('label', key)} 需要填写整数。") from e
        if kind == "float":
            try:
                return float(text)
            except Exception as e:
                raise ValueError(f"{meta.get('label', key)} 需要填写数字。") from e
        return text

    def _api_supports_user_agent(self, state):
        fmt = self._api_format_meta((state or {}).get("format"))
        return fmt.get("kind") == "native_claude"

    def _api_is_mixin_state(self, state):
        return self._api_state_kind(state) == "mixin"

    def _api_preview_visible_names(self):
        used_names = {
            str(((cfg.get("data") or {}).get("name")) or "").strip()
            for cfg in (getattr(self, "_qt_api_hidden_configs", []) or [])
            if str(((cfg.get("data") or {}).get("name")) or "").strip()
        }
        preview = []
        for idx, state in enumerate(getattr(self, "_qt_api_state", []) or []):
            preview_name = self._api_persisted_name(state, idx, used_names)
            used_names.add(preview_name)
            preview.append(preview_name)
        return preview

    def _api_available_mixin_target_names(self):
        names = []
        for state, preview_name in zip(getattr(self, "_qt_api_state", []) or [], self._api_preview_visible_names()):
            if self._api_is_mixin_state(state):
                continue
            name = str(preview_name or "").strip()
            if name:
                names.append(name)
        return names

    def _api_mixin_refs_text(self, state):
        refs = list((state or {}).get("llm_nos") or [])
        return ", ".join(str(item).strip() for item in refs if str(item).strip())

    def _api_parse_mixin_refs(self, text):
        refs = []
        for raw in str(text or "").replace("\n", ",").replace("，", ",").split(","):
            token = str(raw or "").strip()
            if not token:
                continue
            if token.isdigit():
                refs.append(int(token))
            else:
                refs.append(token)
        return refs

    def _api_apply_template_model(self, state, previous_default=""):
        new_default = self._api_default_model_for_state(state)
        current = (state.get("model") or "").strip()
        if (not current) or (previous_default and current == previous_default):
            state["model"] = new_default

    def _api_base_name(self, state, idx):
        raw = (state.get("apibase") or "").strip()
        host = ""
        if raw:
            try:
                parsed = lz.urlparse(raw if "://" in raw else f"https://{raw}")
                host = (parsed.netloc or parsed.path.split("/", 1)[0]).strip()
            except Exception:
                host = ""
        if host:
            host = host.split("@")[-1].split(":", 1)[0].strip().lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                return host
        return f"{lz.TEMPLATE_INDEX.get(state.get('tpl_key'), {}).get('label', 'api')}-{idx + 1}"

    def _api_effective_name(self, state, idx):
        custom_name = str((state or {}).get("name") or "").strip()
        if custom_name:
            return custom_name
        if bool((state or {}).get("auto_name_locked")):
            persisted_name = str((state or {}).get("persisted_name") or "").strip()
            if persisted_name:
                return persisted_name
        return self._api_base_name(state, idx) or f"api-{idx + 1}"

    def _api_unique_name(self, base_name, used_names, *, fallback):
        root_name = str(base_name or "").strip() or str(fallback or "").strip()
        name = root_name or "api"
        candidate = name
        serial = 2
        while candidate in used_names:
            candidate = f"{name}-{serial}"
            serial += 1
        return candidate

    def _api_persisted_name(self, state, idx, used_names):
        return self._api_unique_name(self._api_effective_name(state, idx), used_names, fallback=f"api-{idx + 1}")

    def _api_auto_generated_name(self, state, idx, used_names):
        return self._api_unique_name(self._api_base_name(state, idx), used_names, fallback=f"api-{idx + 1}")

    def _api_build_save_configs(self):
        visible_configs = []
        used_names = {
            str(((cfg.get("data") or {}).get("name")) or "").strip()
            for cfg in (getattr(self, "_qt_api_hidden_configs", []) or [])
            if str(((cfg.get("data") or {}).get("name")) or "").strip()
        }
        for idx, state in enumerate(self._qt_api_state):
            fmt = self._api_format_meta(state.get("format"))
            tpl = lz.TEMPLATE_INDEX.get(state.get("tpl_key"), {})
            kind = fmt.get("kind") or tpl.get("kind") or "native_oai"
            self._api_sync_state_var_kind(state, kind)
            data = dict(tpl.get("defaults") or {})
            data.update(dict(state.get("raw_extra") or {}))
            api_mode = fmt.get("api_mode")
            if api_mode:
                data["api_mode"] = api_mode
            else:
                data.pop("api_mode", None)
            if kind == "mixin":
                refs = []
                for target in list(state.get("llm_nos") or []):
                    if isinstance(target, int):
                        refs.append(int(target))
                        continue
                    target_text = str(target or "").strip()
                    if target_text:
                        refs.append(target_text)
                data["llm_nos"] = refs
                for key in ("apikey", "apibase", "model", "user_agent"):
                    data.pop(key, None)
            else:
                apibase = (state.get("apibase") or "").strip()
                apikey = (state.get("apikey") or "").strip()
                model = (state.get("model") or "").strip()
                if apibase:
                    data["apibase"] = apibase
                elif not data.get("apibase"):
                    data.pop("apibase", None)
                if apikey:
                    data["apikey"] = apikey
                else:
                    data.pop("apikey", None)
                if model:
                    data["model"] = model
                else:
                    data.pop("model", None)
            for key in self._api_advanced_field_keys(state):
                values = dict(state.get("advanced_values") or {})
                if key not in values:
                    continue
                normalized = self._api_normalize_advanced_value(key, values.get(key))
                if normalized is None:
                    data.pop(key, None)
                else:
                    data[key] = normalized
            if kind == "native_claude":
                user_agent = str(state.get("user_agent") or "").strip()
                if user_agent:
                    data["user_agent"] = user_agent
                else:
                    data.pop("user_agent", None)
            else:
                data.pop("user_agent", None)
            name = self._api_persisted_name(state, idx, used_names)
            used_names.add(name)
            state["persisted_name"] = name
            state["auto_name_locked"] = not bool(str(state.get("name") or "").strip())
            data["name"] = name
            visible_configs.append({"var": state["var"], "kind": kind, "data": data})
        ordered = []
        visible_iter = iter(visible_configs)
        hidden_configs = list(getattr(self, "_qt_api_hidden_configs", []) or [])
        used_hidden_slots = set()
        for slot in list(getattr(self, "_qt_api_order_slots", []) or []):
            if slot == "visible":
                next_visible = next(visible_iter, None)
                if next_visible is not None:
                    ordered.append(next_visible)
                continue
            if isinstance(slot, tuple) and len(slot) == 2 and slot[0] == "hidden":
                try:
                    hidden_idx = int(slot[1])
                except Exception:
                    continue
                if 0 <= hidden_idx < len(hidden_configs):
                    ordered.append(hidden_configs[hidden_idx])
                    used_hidden_slots.add(hidden_idx)
        ordered.extend(list(visible_iter))
        for hidden_idx, hidden_cfg in enumerate(hidden_configs):
            if hidden_idx in used_hidden_slots:
                continue
            ordered.append(hidden_cfg)
        return ordered

    def _qt_api_move_disabled_reason(self, idx, delta):
        total = len(getattr(self, "_qt_api_state", []) or [])
        if total <= 1:
            return "当前只有一张 API 卡片，无需调整顺序。"
        if not (0 <= idx < total):
            return "当前卡片不存在。"
        target_idx = idx + int(delta or 0)
        if target_idx < 0:
            return "已经在最上方。"
        if target_idx >= total:
            return "已经在最下方。"
        return ""

    def _qt_api_move(self, idx, delta):
        reason = self._qt_api_move_disabled_reason(idx, delta)
        if reason:
            return False
        target_idx = idx + int(delta or 0)
        self._qt_api_state[idx], self._qt_api_state[target_idx] = (
            self._qt_api_state[target_idx],
            self._qt_api_state[idx],
        )
        self._render_api_cards()
        return True

    def _api_source_status(self) -> str:
        return str(getattr(self, "_qt_api_source_status", "") or "").strip().lower()

    def _set_api_source_status(self, status: str, *, py_path: str | None = None, error_text: str | None = None):
        self._qt_api_source_status = str(status or "").strip().lower()
        if py_path is not None:
            self._qt_api_py_path = str(py_path or "")
        if error_text is not None:
            self._qt_api_parse_error = str(error_text or "")

    def _api_source_allows_save(self) -> bool:
        return self._api_source_status() in {"ready", "parse_error"}

    def _api_source_allows_raw_edit(self) -> bool:
        return self._api_source_status() in {"ready", "parse_error", "load_failed"}

    def _api_source_disabled_reason(self, action: str, *, is_remote_target: bool = False) -> str:
        status = self._api_source_status()
        verb = str(action or "").strip() or "执行该操作"
        if is_remote_target and verb == "保存并重启内核":
            return "远端目标下不会在本机重启聊天内核；写回远端 mykey.py 后请在服务器侧重启对应进程。"
        if status == "invalid_dir":
            return "请先选择有效的 GenericAgent 目录。"
        if status == "loading":
            return "正在读取当前目标的 mykey.py，请稍候。"
        if status == "load_failed":
            if verb == "直接编辑文件":
                return ""
            return "当前 mykey.py 读取失败；如需修复请先用“直接编辑文件”处理原文。"
        if status == "parse_error":
            return ""
        if status == "ready":
            return ""
        return f"当前状态不可{verb}。"

    def _apply_api_button_state(self, button, enabled, *, enabled_tooltip="", disabled_tooltip=""):
        if button is None:
            return
        button.setEnabled(bool(enabled))
        tooltip = enabled_tooltip if bool(enabled) else disabled_tooltip
        try:
            button.setToolTip(str(tooltip or ""))
        except Exception:
            pass

    def _api_model_fetch_disabled_reason(self, state) -> str:
        item = state if isinstance(state, dict) else {}
        if bool(item.get("model_fetching")):
            return "当前正在拉取该配置的模型列表，请稍候。"
        return ""

    def _refresh_api_source_actions(self):
        target_ctx_getter = getattr(self, "_settings_target_context", None)
        target_ctx = target_ctx_getter() if callable(target_ctx_getter) else {"is_remote": False}
        is_remote_target = bool((target_ctx or {}).get("is_remote"))
        status = self._api_source_status()
        can_save = self._api_source_allows_save()
        can_raw = self._api_source_allows_raw_edit()
        add_btn = getattr(self, "settings_api_add_btn", None)
        save_btn = getattr(self, "settings_api_save_btn", None)
        restart_btn = getattr(self, "settings_api_restart_btn", None)
        raw_btn = getattr(self, "settings_api_raw_btn", None)
        self._apply_api_button_state(
            add_btn,
            can_save,
            enabled_tooltip="新增一张 API 配置卡片。",
            disabled_tooltip=self._api_source_disabled_reason("新增 API 卡片", is_remote_target=is_remote_target),
        )
        self._apply_api_button_state(
            save_btn,
            can_save,
            enabled_tooltip="把当前 API 配置写回 mykey.py。",
            disabled_tooltip=self._api_source_disabled_reason("保存", is_remote_target=is_remote_target),
        )
        restart_allowed = can_save and (not is_remote_target)
        self._apply_api_button_state(
            restart_btn,
            restart_allowed,
            enabled_tooltip="保存后立即重启本机聊天内核。",
            disabled_tooltip=self._api_source_disabled_reason("保存并重启内核", is_remote_target=is_remote_target),
        )
        self._apply_api_button_state(
            raw_btn,
            can_raw,
            enabled_tooltip="直接编辑当前目标的 mykey.py 原文。",
            disabled_tooltip=self._api_source_disabled_reason("直接编辑文件", is_remote_target=is_remote_target),
        )

    def _apply_loaded_api_source(self, py_path, parsed):
        if bool((parsed or {}).get("load_failed")):
            self._reset_api_source_state(error_text=parsed.get("error") or "读取配置失败", status="load_failed")
            notices = [str(py_path or "").strip()]
            if self._qt_api_parse_error:
                notices.append(f"当前读取失败：{self._qt_api_parse_error}。请先修复读取问题，当前页面不会覆盖现有配置。")
            self.settings_api_notice.setText("\n".join([line for line in notices if line]))
            self._refresh_api_source_actions()
            return
        self._qt_api_py_path = py_path
        self._qt_api_parse_error = parsed.get("error") or ""
        self._qt_api_hidden_configs = []
        self._qt_api_state = []
        self._qt_api_order_slots = []
        for config in list(parsed.get("configs") or []):
            kind = str(config.get("kind") or "").strip()
            if kind in ("native_claude", "native_oai", "mixin"):
                self._qt_api_state.append(self._api_make_simple_state(config))
                self._qt_api_order_slots.append("visible")
                continue
            hidden_idx = len(self._qt_api_hidden_configs)
            self._qt_api_hidden_configs.append({"var": config["var"], "kind": kind, "data": dict(config["data"])})
            self._qt_api_order_slots.append(("hidden", hidden_idx))
        auto_names = set()
        for idx, state in enumerate(self._qt_api_state):
            stored_name = str((state or {}).get("persisted_name") or (state or {}).get("name") or "").strip()
            state["persisted_name"] = stored_name
            auto_name = self._api_auto_generated_name(state, idx, auto_names)
            if stored_name == auto_name:
                state["name"] = ""
                state["auto_name_locked"] = True
            else:
                state["name"] = stored_name
                state["auto_name_locked"] = False
            auto_names.add(stored_name or auto_name)
        if not self._qt_api_state:
            self._qt_api_add_channel("oai_chat", render=False)
        self._qt_api_extras = dict(parsed.get("extras") or {})
        self._qt_api_passthrough = list(parsed.get("passthrough") or [])
        notices = [py_path]
        if self._qt_api_parse_error:
            notices.append(f"当前解析失败：{self._qt_api_parse_error}。继续保存会覆盖为启动器可识别的格式。")
        if self._qt_api_hidden_configs:
            notices.append(f"检测到 {len(self._qt_api_hidden_configs)} 条旧式或高级配置，本页保存时会原样保留。")
        if self._qt_api_passthrough:
            notices.append(f"检测到 {len(self._qt_api_passthrough)} 条表单不直接编辑的原文项，保存时会原样保留。")
        self._set_api_source_status("parse_error" if self._qt_api_parse_error else "ready")
        self.settings_api_notice.setText("\n".join(notices))
        self._render_api_cards()
        self._refresh_api_source_actions()

    def _reset_api_source_state(self, *, error_text="", status="invalid_dir"):
        self._qt_api_py_path = ""
        self._qt_api_parse_error = str(error_text or "")
        self._qt_api_hidden_configs = []
        self._qt_api_state = []
        self._qt_api_order_slots = []
        self._qt_api_extras = {}
        self._qt_api_passthrough = []
        self._set_api_source_status(status, py_path="", error_text=error_text)
        refresher = getattr(self, "_refresh_api_source_actions", None)
        if callable(refresher):
            refresher()

    def _reload_api_editor_state(self):
        if not hasattr(self, "settings_api_notice"):
            return
        self._clear_layout(self.settings_api_list_layout)
        target_ctx_getter = getattr(self, "_settings_target_context", None)
        target_ctx = target_ctx_getter() if callable(target_ctx_getter) else {"is_remote": False}
        if (not bool((target_ctx or {}).get("is_remote"))) and (not lz.is_valid_agent_dir(self.agent_dir)):
            self._reset_api_source_state(error_text="请先选择有效的 GenericAgent 目录。", status="invalid_dir")
            self.settings_api_notice.setText("请先选择有效的 GenericAgent 目录。")
            return
        if bool((target_ctx or {}).get("is_remote")):
            if bool(getattr(self, "_qt_api_remote_loading", False)):
                self.settings_api_notice.setText("正在读取远端 mykey.py…")
                self._set_api_source_status("loading")
                self._refresh_api_source_actions()
                return
            self._qt_api_remote_loading = True
            context = capture_runtime_context(self, include_settings_target=True)
            self._set_api_source_status("loading")
            self._refresh_api_source_actions()
            self.settings_api_notice.setText("正在读取远端 mykey.py…")
            loading = QLabel("正在从远端设备拉取 API 配置，请稍候…")
            loading.setObjectName("mutedText")
            self.settings_api_list_layout.addWidget(loading)

            def worker():
                py_path, parsed = self._load_mykey_source()

                def done():
                    # 旧逻辑等价检查：
                    # if int(current_token or 0) != int(target_token or 0):
                    if not runtime_context_matches(self, context, include_settings_target=True):
                        return
                    self._qt_api_remote_loading = False
                    self._clear_layout(self.settings_api_list_layout)
                    self._apply_loaded_api_source(py_path, parsed)

                self._api_on_ui_thread(done)

            threading.Thread(target=worker, name="settings-api-remote-load", daemon=True).start()
            return
        py_path, parsed = self._load_mykey_source()
        self._apply_loaded_api_source(py_path, parsed)

    def _render_api_cards(self):
        self._clear_layout(self.settings_api_list_layout)
        if not self._qt_api_state:
            empty = QLabel("（还没有 API 卡片，点上方“添加 API 卡片”开始）")
            empty.setStyleSheet(f"font-size: 13px; color: {C['muted']}; padding: 12px 0;")
            self.settings_api_list_layout.addWidget(empty)
            return
        for idx, state in enumerate(self._qt_api_state):
            card = self._panel_card()
            body = QVBoxLayout(card)
            body.setContentsMargins(16, 14, 16, 14)
            body.setSpacing(10)

            head = QHBoxLayout()
            title = QLabel(f"API 卡片 {idx + 1}")
            title.setObjectName("cardTitle")
            head.addWidget(title, 0)
            meta = QLabel(
                f"{lz.SIMPLE_FORMAT_LABEL.get(state.get('format'), state.get('format', ''))} · "
                f"{lz.TEMPLATE_INDEX.get(state.get('tpl_key'), {}).get('label', '未选择模板')}"
            )
            meta.setObjectName("mutedText")
            head.addWidget(meta, 0)
            head.addStretch(1)
            move_up_btn = QPushButton("上移")
            move_up_btn.setStyleSheet(self._action_button_style())
            move_up_btn.clicked.connect(lambda _=False, i=idx: self._qt_api_move(i, -1))
            move_up_reason = self._qt_api_move_disabled_reason(idx, -1)
            self._apply_api_button_state(
                move_up_btn,
                not bool(move_up_reason),
                enabled_tooltip="把这张 API 卡片上移一位。",
                disabled_tooltip=move_up_reason,
            )
            head.addWidget(move_up_btn, 0)
            move_down_btn = QPushButton("下移")
            move_down_btn.setStyleSheet(self._action_button_style())
            move_down_btn.clicked.connect(lambda _=False, i=idx: self._qt_api_move(i, 1))
            move_down_reason = self._qt_api_move_disabled_reason(idx, 1)
            self._apply_api_button_state(
                move_down_btn,
                not bool(move_down_reason),
                enabled_tooltip="把这张 API 卡片下移一位。",
                disabled_tooltip=move_down_reason,
            )
            head.addWidget(move_down_btn, 0)
            delete_btn = QPushButton("删除")
            delete_btn.setStyleSheet(self._action_button_style())
            delete_btn.clicked.connect(lambda _=False, i=idx: self._qt_api_delete(i))
            head.addWidget(delete_btn, 0)
            body.addLayout(head)

            row1 = QHBoxLayout()
            row1.setSpacing(10)
            row1.addWidget(QLabel("名称"), 0)
            name_edit = QLineEdit()
            name_edit.setPlaceholderText("可选：留空则自动用域名或模板名生成")
            name_edit.setText(str(state.get("name") or ""))
            row1.addWidget(name_edit, 1)
            row1.addWidget(QLabel("协议"), 0)
            format_box = NoWheelComboBox()
            format_box.addItems(self._api_format_options())
            format_box.setCurrentText(lz.SIMPLE_FORMAT_LABEL.get(state.get("format"), lz.SIMPLE_FORMAT_LABEL["oai_chat"]))
            format_box.setStyleSheet(self._api_combo_style())
            row1.addWidget(format_box, 1)
            row1.addWidget(QLabel("模板"), 0)
            tpl_box = NoWheelComboBox()
            tpl_choices = self._api_template_choices(state.get("format"))
            tpl_map = {k: lbl for k, lbl in tpl_choices}
            tpl_box.addItems([lbl for _, lbl in tpl_choices])
            tpl_box.setCurrentText(tpl_map.get(state.get("tpl_key"), tpl_choices[0][1] if tpl_choices else ""))
            tpl_box.setStyleSheet(self._api_combo_style())
            row1.addWidget(tpl_box, 1)
            body.addLayout(row1)

            if self._api_is_mixin_state(state):
                row2 = QHBoxLayout()
                row2.setSpacing(10)
                row2.addWidget(QLabel("故障转移链"), 0)
                refs_edit = QLineEdit()
                refs_edit.setPlaceholderText("按顺序填写非 mixin API 卡片名，逗号分隔；兼容旧索引写法")
                refs_edit.setText(self._api_mixin_refs_text(state))
                row2.addWidget(refs_edit, 1)
                body.addLayout(row2)
                status = QLabel("")
                status.setWordWrap(True)
                status.setObjectName("mutedText")
                body.addWidget(status)
                model_box = None
                ua_edit = None
                ua_row_widgets = ()
            else:
                row2 = QHBoxLayout()
                row2.setSpacing(10)
                row2.addWidget(QLabel("URL"), 0)
                url_edit = QLineEdit()
                url_edit.setPlaceholderText("例如 https://api.openai.com/v1")
                url_edit.setText(str(state.get("apibase") or ""))
                row2.addWidget(url_edit, 1)
                body.addLayout(row2)

                row3 = QHBoxLayout()
                row3.setSpacing(10)
                row3.addWidget(QLabel("模型"), 0)
                model_box = NoWheelComboBox()
                model_box.setEditable(True)
                model_box.setStyleSheet(self._api_combo_style())
                model_choices = list(state.get("model_choices") or [])
                current_model = (state.get("model") or self._api_default_model_for_state(state) or "").strip()
                if current_model and current_model not in model_choices:
                    model_choices.insert(0, current_model)
                if not model_choices:
                    model_choices = [current_model] if current_model else [""]
                model_box.addItems(model_choices)
                model_box.setCurrentText(current_model)
                row3.addWidget(model_box, 1)
                fetch_btn = QPushButton("拉取模型")
                fetch_btn.setStyleSheet(self._action_button_style())
                fetch_btn.clicked.connect(lambda _=False, s=state: self._qt_api_fetch_models(s))
                fetch_disabled_reason = self._api_model_fetch_disabled_reason(state)
                self._apply_api_button_state(
                    fetch_btn,
                    not bool(fetch_disabled_reason),
                    enabled_tooltip="从当前 API 地址拉取可用模型列表。",
                    disabled_tooltip=fetch_disabled_reason,
                )
                row3.addWidget(fetch_btn, 0)
                body.addLayout(row3)

                row4 = QHBoxLayout()
                row4.setSpacing(10)
                row4.addWidget(QLabel("Key"), 0)
                key_edit = QLineEdit()
                key_edit.setEchoMode(QLineEdit.Password)
                key_edit.setPlaceholderText("API Key")
                key_edit.setText(str(state.get("apikey") or ""))
                row4.addWidget(key_edit, 1)
                show_btn = QPushButton("显示")
                show_btn.setCheckable(True)
                show_btn.setStyleSheet(self._action_button_style())

                def toggle_key(checked, edit=key_edit, btn=show_btn):
                    edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
                    btn.setText("隐藏" if checked else "显示")

                show_btn.toggled.connect(toggle_key)
                row4.addWidget(show_btn, 0)
                body.addLayout(row4)

                row5 = QHBoxLayout()
                row5.setSpacing(10)
                row5.addWidget(QLabel("UA"), 0)
                ua_edit = QLineEdit()
                ua_edit.setPlaceholderText("可选：自定义 user_agent，例如 claude-cli/2.1.113 (external, cli)")
                ua_edit.setText(str(state.get("user_agent") or ""))
                row5.addWidget(ua_edit, 1)
                body.addLayout(row5)
                ua_row_widgets = (ua_edit, row5.itemAt(0).widget())
                for widget in ua_row_widgets:
                    if widget is not None:
                        widget.setVisible(self._api_supports_user_agent(state))

                status = QLabel(state.get("model_status") or "")
                status.setWordWrap(True)
                status.setObjectName("mutedText")
                body.addWidget(status)

            summary = QLabel("")
            summary.setWordWrap(True)
            summary.setObjectName("mutedText")
            body.addWidget(summary)

            def sync_summary(s=state, label=summary, state_idx=idx):
                card_name = self._api_effective_name(s, state_idx)
                name_note = f"当前卡片名：{card_name}"
                if not str(s.get("name") or "").strip():
                    name_note += "（留空时自动生成）"
                if self._api_is_mixin_state(s):
                    refs = [str(item).strip() for item in list(s.get("llm_nos") or []) if str(item).strip()]
                    available = self._api_available_mixin_target_names()
                    ref_note = " -> ".join(refs) if refs else "（尚未填写）"
                    available_note = "、".join(available) if available else "（当前还没有可引用的非 mixin API 卡片）"
                    label.setText(
                        f"{name_note}；当前故障转移顺序：{ref_note}；可引用 API 会话名：{available_note}；保存时会校验引用是否存在。"
                    )
                    status.setText(f"当前可引用的 API 会话名：{available_note}")
                    return
                fmt = self._api_format_meta(s.get("format"))
                defaults = dict(lz.TEMPLATE_INDEX.get(s.get("tpl_key"), {}).get("defaults") or {})
                model = (s.get("model") or defaults.get("model") or "请手动填写模型名").strip()
                notes = []
                if defaults.get("fake_cc_system_prompt"):
                    notes.append("自动带 Claude Code 兼容参数")
                if fmt.get("api_mode"):
                    notes.append(f"api_mode={fmt['api_mode']}")
                if defaults.get("read_timeout"):
                    notes.append(f"read_timeout={defaults['read_timeout']}")
                if (s.get("user_agent") or "").strip():
                    notes.append("已自定义 user_agent")
                if str(self._api_advanced_value(s, "user_agent") or "").strip():
                    notes.append("已自定义 user_agent")
                label.setText(
                    f"{name_note}；{fmt.get('hint', '')} 模板默认模型：{model}；"
                    f"{'，'.join(notes) if notes else '自动写入模板里的默认参数'}。"
                )
                status.setText(str(s.get("model_status") or ""))

            advanced_toggle = QPushButton()
            advanced_toggle.setCheckable(True)
            advanced_toggle.setChecked(bool(state.get("advanced_expanded", False)))
            advanced_toggle.setStyleSheet(self._action_button_style(kind="subtle"))
            body.addWidget(advanced_toggle)

            advanced_wrap = QWidget()
            advanced_layout = QVBoxLayout(advanced_wrap)
            advanced_layout.setContentsMargins(0, 4, 0, 0)
            advanced_layout.setSpacing(8)
            body.addWidget(advanced_wrap)

            def sync_advanced_fold(checked=None, s=state, btn=advanced_toggle, panel=advanced_wrap):
                flag = bool(btn.isChecked() if checked is None else checked)
                s["advanced_expanded"] = flag
                btn.setText(("▾ " if flag else "▸ ") + "高级参数")
                panel.setVisible(flag)

            def render_advanced_fields(s=state, layout=advanced_layout):
                while layout.count():
                    item = layout.takeAt(0)
                    widget = item.widget()
                    child = item.layout()
                    if child is not None:
                        self._clear_layout(child)
                    if widget is not None:
                        widget.deleteLater()
                field_keys = self._api_advanced_field_keys(s)
                if not field_keys:
                    empty = QLabel("当前模板没有额外高级参数。")
                    empty.setObjectName("mutedText")
                    layout.addWidget(empty)
                    return
                for key in field_keys:
                    meta = _API_ADVANCED_FIELD_META.get(key) or {}
                    row_host = QWidget()
                    row = QHBoxLayout(row_host)
                    row.setContentsMargins(0, 0, 0, 0)
                    row.setSpacing(10)
                    label = QLabel(meta.get("label", key))
                    label.setMinimumWidth(138)
                    row.addWidget(label, 0)
                    kind = meta.get("kind")
                    current_value = self._api_advanced_value(s, key)
                    if kind == "bool":
                        widget = QCheckBox(meta.get("checkbox_label", "启用"))
                        widget.setChecked(bool(current_value))
                        widget.toggled.connect(lambda checked, st=s, k=key: self._api_set_advanced_value(st, k, checked))
                    elif kind == "choice":
                        widget = NoWheelComboBox()
                        widget.setStyleSheet(self._api_combo_style())
                        widget.addItem("跟随模板/默认", "")
                        for choice in meta.get("choices", []):
                            widget.addItem(str(choice), str(choice))
                        current_text = str(current_value or "").strip()
                        idx = widget.findData(current_text)
                        widget.setCurrentIndex(idx if idx >= 0 else 0)
                        widget.currentIndexChanged.connect(lambda _=0, w=widget, st=s, k=key: self._api_set_advanced_value(st, k, w.currentData()))
                    else:
                        widget = QLineEdit()
                        widget.setPlaceholderText(meta.get("placeholder", "留空则跟随模板/默认"))
                        widget.setText("" if current_value in (None, "") else str(current_value))
                        widget.textChanged.connect(lambda text, st=s, k=key: self._api_set_advanced_value(st, k, text))
                    row.addWidget(widget, 1)
                    layout.addWidget(row_host)

            def on_format_change(choice, s=state, tpl_widget=tpl_box, model_widget=model_box, status_label=status):
                format_key = self._api_format_from_label(choice)
                was_mixin = self._api_is_mixin_state(s)
                previous_default = self._api_default_model_for_state(s)
                s["format"] = format_key
                self._api_sync_state_var_kind(s)
                s["model_status"] = ""
                s["raw_extra"] = self._api_prune_managed_extra(s.get("raw_extra"), drop_template=True, drop_format=True)
                new_choices = self._api_template_choices(format_key)
                new_map = {k: lbl for k, lbl in new_choices}
                current_key = s.get("tpl_key")
                if current_key not in new_map:
                    current_key = self._api_format_meta(format_key).get("default_template", new_choices[0][0] if new_choices else "")
                s["tpl_key"] = current_key
                self._api_apply_template_model(s, previous_default)
                tpl_widget.blockSignals(True)
                tpl_widget.clear()
                tpl_widget.addItems([lbl for _, lbl in new_choices])
                tpl_widget.setCurrentText(new_map.get(current_key, ""))
                tpl_widget.blockSignals(False)
                if model_widget is not None:
                    model_widget.setCurrentText(s.get("model") or "")
                if self._api_is_mixin_state(s):
                    s["llm_nos"] = list(s.get("llm_nos") or [])
                render_advanced_fields()
                sync_advanced_fold()
                for widget in ua_row_widgets:
                    if widget is not None:
                        widget.setVisible(self._api_supports_user_agent(s))
                status_label.setText("")
                sync_summary()
                if was_mixin != self._api_is_mixin_state(s):
                    self._render_api_cards()

            def on_tpl_change(choice, s=state, status_label=status, model_widget=model_box):
                rev = {lbl: key for key, lbl in self._api_template_choices(s.get("format"))}
                previous_default = self._api_default_model_for_state(s)
                s["tpl_key"] = rev.get(choice, s.get("tpl_key"))
                s["model_status"] = ""
                s["raw_extra"] = self._api_prune_managed_extra(s.get("raw_extra"), drop_template=True, drop_format=False)
                self._api_apply_template_model(s, previous_default)
                if model_widget is not None:
                    model_widget.setCurrentText(s.get("model") or "")
                status_label.setText("")
                render_advanced_fields()
                sync_advanced_fold()
                sync_summary()

            format_box.currentTextChanged.connect(on_format_change)
            tpl_box.currentTextChanged.connect(on_tpl_change)
            name_edit.textChanged.connect(lambda text, s=state: (s.__setitem__("name", text.strip()), sync_summary()))
            if self._api_is_mixin_state(state):
                refs_edit.textChanged.connect(
                    lambda text, s=state: (s.__setitem__("llm_nos", self._api_parse_mixin_refs(text)), sync_summary())
                )
            else:
                url_edit.textChanged.connect(lambda text, s=state: (s.__setitem__("apibase", text), sync_summary()))
                key_edit.textChanged.connect(lambda text, s=state: s.__setitem__("apikey", text))
                model_box.currentTextChanged.connect(lambda text, s=state: s.__setitem__("model", text.strip()))
            advanced_toggle.toggled.connect(sync_advanced_fold)
            render_advanced_fields()
            sync_advanced_fold()
            if ua_edit is not None:
                ua_edit.textChanged.connect(lambda text, s=state: s.__setitem__("user_agent", text.strip()))
            sync_summary()
            self.settings_api_list_layout.addWidget(card)
        self.settings_api_list_layout.addStretch(1)

    def _qt_api_add_channel(self, format_key, *, render=True):
        fmt = self._api_format_meta(format_key)
        kind = fmt.get("kind")
        if kind not in ("native_claude", "native_oai", "mixin"):
            return
        existing = {s["var"] for s in self._qt_api_state}
        existing.update({c["var"] for c in self._qt_api_hidden_configs})
        var = lz.auto_config_var(kind, existing)
        tpl_key = fmt.get("default_template", "openai")
        defaults = dict(lz.TEMPLATE_INDEX.get(tpl_key, {}).get("defaults") or {})
        self._qt_api_state.append(
            {
                "var": var,
                "name": "",
                "format": format_key,
                "tpl_key": tpl_key,
                "apibase": defaults.get("apibase", ""),
                "apikey": "",
                "model": defaults.get("model", ""),
                "advanced_values": {},
                "advanced_expanded": False,
                "user_agent": "",
                "raw_extra": {},
                "model_choices": [],
                "model_status": "",
                "model_fetching": False,
                "llm_nos": list(defaults.get("llm_nos") or []),
            }
        )
        if render:
            self._render_api_cards()

    def _qt_api_delete(self, idx):
        if 0 <= idx < len(self._qt_api_state):
            del self._qt_api_state[idx]
            self._render_api_cards()

    def _qt_api_fetch_models(self, state):
        base = (state.get("apibase") or "").strip()
        if not base:
            state["model_status"] = "请先填写 URL，再拉取模型。"
            self._render_api_cards()
            return
        if state.get("model_fetching"):
            return
        context = capture_runtime_context(self, include_settings_target=True)
        state["model_fetch_token"] = int(state.get("model_fetch_token", 0) or 0) + 1
        fetch_token = int(state.get("model_fetch_token", 0) or 0)
        state["model_fetching"] = True
        state["model_status"] = "正在拉取模型列表…"
        self._render_api_cards()

        def worker():
            try:
                models = lz._fetch_remote_models(state.get("format"), state.get("apibase"), state.get("apikey"))

                def done_ok():
                    if not runtime_context_matches(self, context, include_settings_target=True):
                        return
                    if int(state.get("model_fetch_token", 0) or 0) != fetch_token:
                        return
                    if state not in list(getattr(self, "_qt_api_state", []) or []):
                        return
                    state["model_fetching"] = False
                    state["model_choices"] = models
                    if models and not (state.get("model") or "").strip():
                        state["model"] = models[0]
                    state["model_status"] = f"已拉取 {len(models)} 个模型，可直接选择或继续手输。"
                    self._render_api_cards()

                self._api_on_ui_thread(done_ok)
            except Exception as e:
                err_text = str(e)

                def done_err():
                    if not runtime_context_matches(self, context, include_settings_target=True):
                        return
                    if int(state.get("model_fetch_token", 0) or 0) != fetch_token:
                        return
                    if state not in list(getattr(self, "_qt_api_state", []) or []):
                        return
                    state["model_fetching"] = False
                    state["model_status"] = f"拉取失败：{err_text}"
                    self._render_api_cards()

                self._api_on_ui_thread(done_err)

        threading.Thread(target=worker, daemon=True).start()

    def _qt_api_save(self, restart=False):
        if not self._qt_api_py_path:
            QMessageBox.warning(self, "无法保存", "还没有可用的 mykey.py。")
            return
        try:
            configs = self._api_build_save_configs()
            ref_errors = list(lz.validate_api_config_references(configs))
            if ref_errors:
                raise RuntimeError("API 配置引用无效：\n" + "\n".join(ref_errors))
            txt = lz.serialize_mykey_py(
                configs=configs,
                extras=self._qt_api_extras,
                passthrough=self._qt_api_passthrough,
            )
            writer = getattr(self, "_settings_target_write_mykey_text", None)
            if callable(writer):
                ok, path_text, err = writer(txt)
                if not ok:
                    raise RuntimeError(err or "写入 mykey.py 失败。")
                self._qt_api_py_path = str(path_text or self._qt_api_py_path)
            else:
                with open(self._qt_api_py_path, "w", encoding="utf-8") as f:
                    f.write(txt)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        target_ctx_getter = getattr(self, "_settings_target_context", None)
        target_ctx = target_ctx_getter() if callable(target_ctx_getter) else {"is_remote": False, "label": "本机"}
        is_remote_target = bool((target_ctx or {}).get("is_remote"))
        restarted = 0 if is_remote_target else self._restart_running_channels(show_errors=False)
        if restart and (not is_remote_target):
            self._restart_bridge()
            QMessageBox.information(self, "已保存", "已写入 mykey.py，并已重启聊天内核。")
        else:
            extra = f"\n已自动重启 {restarted} 个由启动器托管的通讯渠道。" if restarted else ""
            if is_remote_target:
                QMessageBox.information(self, "已保存", "已写入远端 mykey.py。远端渠道请在对应服务器侧重启进程后生效。")
            else:
                QMessageBox.information(self, "已保存", "已写入 mykey.py。聊天内核需重启后才会读取新配置。" + extra)
        self._reload_api_editor_state()
        self._reload_channels_editor_state()

    def _open_raw_mykey_editor(self):
        target_ctx_getter = getattr(self, "_settings_target_context", None)
        target_ctx = target_ctx_getter() if callable(target_ctx_getter) else {"is_remote": False}
        if (not bool((target_ctx or {}).get("is_remote"))) and (not lz.is_valid_agent_dir(self.agent_dir)):
            QMessageBox.warning(self, "目录无效", "请先选择有效的 GenericAgent 目录。")
            return
        py_path, _ = self._load_mykey_source()
        try:
            reader = getattr(self, "_settings_target_read_mykey_text", None)
            if callable(reader):
                ok, text, display_path, err = reader()
                if not ok:
                    raise RuntimeError(err or "读取 mykey.py 失败。")
                py_path = str(display_path or py_path)
                original = str(text or "")
            else:
                with open(py_path, "r", encoding="utf-8") as f:
                    original = f.read()
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("直接编辑 mykey.py")
        dlg.resize(920, 720)
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        path_label = QLabel(py_path)
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_label.setObjectName("mutedText")
        layout.addWidget(path_label)

        hint = QLabel("这里是 Qt 下的原文编辑入口。保存后会直接写回 mykey.py；如果你需要高级字段或手写结构，用这个入口最稳。")
        hint.setWordWrap(True)
        hint.setObjectName("softTextSmall")
        layout.addWidget(hint)

        editor = QTextEdit()
        editor.setPlainText(original)
        editor.setStyleSheet(
            f"QTextEdit {{ background: {C['field_bg']}; color: {C['code_text']}; border: 1px solid {C['stroke_default']}; border-radius: {F['radius_md']}px; font-family: {F['font_family_mono']}; font-size: 13px; }}"
        )
        layout.addWidget(editor, 1)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        cancel_btn = QPushButton("关闭")
        cancel_btn.setStyleSheet(self._action_button_style())
        btns.addWidget(cancel_btn, 0)
        btns.addStretch(1)
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet(self._action_button_style())
        btns.addWidget(save_btn, 0)
        restart_btn = QPushButton("保存并重启内核")
        restart_btn.setStyleSheet(self._action_button_style(primary=True))
        restart_btn.setEnabled(not bool((target_ctx or {}).get("is_remote")))
        if bool((target_ctx or {}).get("is_remote")):
            restart_btn.setToolTip("远端目标下不会在本机重启聊天内核。")
        btns.addWidget(restart_btn, 0)
        layout.addLayout(btns)

        def do_save(restart=False):
            text = editor.toPlainText()
            try:
                compile(text, py_path, "exec")
            except Exception as e:
                QMessageBox.warning(dlg, "语法错误", str(e))
                return
            try:
                writer = getattr(self, "_settings_target_write_mykey_text", None)
                if callable(writer):
                    ok, path_text, err = writer(text)
                    if not ok:
                        raise RuntimeError(err or "写入 mykey.py 失败。")
                    if path_text:
                        path_label.setText(str(path_text))
                else:
                    with open(py_path, "w", encoding="utf-8") as f:
                        f.write(text)
            except Exception as e:
                QMessageBox.critical(dlg, "保存失败", str(e))
                return
            target_ctx_getter = getattr(self, "_settings_target_context", None)
            target_ctx = target_ctx_getter() if callable(target_ctx_getter) else {"is_remote": False}
            is_remote_target = bool((target_ctx or {}).get("is_remote"))
            restarted = 0 if is_remote_target else self._restart_running_channels(show_errors=False)
            if restart and (not is_remote_target):
                self._restart_bridge()
            self._reload_api_editor_state()
            self._reload_channels_editor_state()
            msg = "已写入 mykey.py。"
            if is_remote_target:
                msg = "已写入远端 mykey.py。"
            if restarted:
                msg += f"\n已自动重启 {restarted} 个由启动器托管的通讯渠道。"
            if restart and (not is_remote_target):
                msg += "\n聊天内核也已重启。"
            QMessageBox.information(dlg, "已保存", msg)
            dlg.accept()

        cancel_btn.clicked.connect(dlg.reject)
        save_btn.clicked.connect(lambda: do_save(False))
        restart_btn.clicked.connect(lambda: do_save(True))
        dlg.exec()
