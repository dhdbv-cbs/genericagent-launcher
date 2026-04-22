from __future__ import annotations

import threading

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from launcher_app import core as lz
from launcher_app.theme import C, F

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
}


class ApiEditorMixin:
    def _api_on_ui_thread(self, fn):
        QTimer.singleShot(0, self, fn)

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
        return [lz.SIMPLE_FORMAT_LABEL[k] for k in ("claude_native", "oai_chat", "oai_responses")]

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
        return "custom-claude" if kind == "native_claude" else "custom-oai"

    def _api_infer_format_key(self, kind, data):
        if kind == "native_claude":
            return "claude_native"
        if kind == "native_oai":
            return "oai_responses" if data.get("api_mode") == "responses" else "oai_chat"
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
        valid_tpl_keys = {k for k, _ in self._api_template_choices(format_key)}
        if tpl_key not in valid_tpl_keys:
            tpl_key = "custom-claude" if kind == "native_claude" else "custom-oai"
        advanced_values = {}
        for key in self._api_known_advanced_keys_for_kind(kind):
            if key in data:
                advanced_values[key] = data.get(key)
        raw_extra = dict(data)
        for key in ("name", "apikey", "apibase", "model", "user_agent"):
            raw_extra.pop(key, None)
        for key in _API_ADVANCED_FIELD_META:
            raw_extra.pop(key, None)
        for key in lz.TEMPLATE_INDEX.get(tpl_key, {}).get("defaults", {}):
            raw_extra.pop(key, None)
        raw_extra.pop("api_mode", None)
        return {
            "var": cfg["var"],
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

    def _api_build_save_configs(self):
        configs = []
        used_names = set()
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
            base_name = self._api_base_name(state, idx) or f"api-{idx + 1}"
            name = base_name
            serial = 2
            while name in used_names:
                name = f"{base_name}-{serial}"
                serial += 1
            used_names.add(name)
            data["name"] = name
            configs.append({"var": state["var"], "kind": kind, "data": data})
        return configs + list(self._qt_api_hidden_configs)

    def _reload_api_editor_state(self):
        if not hasattr(self, "settings_api_notice"):
            return
        self._clear_layout(self.settings_api_list_layout)
        if not lz.is_valid_agent_dir(self.agent_dir):
            self.settings_api_notice.setText("请先选择有效的 GenericAgent 目录。")
            return
        py_path, parsed = self._load_mykey_source()
        self._qt_api_py_path = py_path
        self._qt_api_parse_error = parsed.get("error") or ""
        self._qt_api_hidden_configs = [
            {"var": c["var"], "kind": c["kind"], "data": dict(c["data"])}
            for c in parsed["configs"]
            if c["kind"] not in ("native_claude", "native_oai")
        ]
        self._qt_api_state = [
            self._api_make_simple_state(c)
            for c in parsed["configs"]
            if c["kind"] in ("native_claude", "native_oai")
        ]
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
        self.settings_api_notice.setText("\n".join(notices))
        self._render_api_cards()

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
            delete_btn = QPushButton("删除")
            delete_btn.setStyleSheet(self._action_button_style())
            delete_btn.clicked.connect(lambda _=False, i=idx: self._qt_api_delete(i))
            head.addWidget(delete_btn, 0)
            body.addLayout(head)

            row1 = QHBoxLayout()
            row1.setSpacing(10)
            row1.addWidget(QLabel("协议"), 0)
            format_box = QComboBox()
            format_box.addItems(self._api_format_options())
            format_box.setCurrentText(lz.SIMPLE_FORMAT_LABEL.get(state.get("format"), "Chat Completions"))
            format_box.setStyleSheet(self._api_combo_style())
            row1.addWidget(format_box, 1)
            row1.addWidget(QLabel("模板"), 0)
            tpl_box = QComboBox()
            tpl_choices = self._api_template_choices(state.get("format"))
            tpl_map = {k: lbl for k, lbl in tpl_choices}
            tpl_box.addItems([lbl for _, lbl in tpl_choices])
            tpl_box.setCurrentText(tpl_map.get(state.get("tpl_key"), tpl_choices[0][1] if tpl_choices else ""))
            tpl_box.setStyleSheet(self._api_combo_style())
            row1.addWidget(tpl_box, 1)
            body.addLayout(row1)

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
            model_box = QComboBox()
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
            fetch_btn.setEnabled(not state.get("model_fetching"))
            fetch_btn.setStyleSheet(self._action_button_style())
            fetch_btn.clicked.connect(lambda _=False, s=state: self._qt_api_fetch_models(s))
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

            def sync_summary(s=state, label=summary):
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
                    f"{fmt.get('hint', '')} 模板默认模型：{model}；"
                    f"{'，'.join(notes) if notes else '自动写入模板里的默认参数'}。"
                )

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
                        widget = QComboBox()
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
                model_widget.setCurrentText(s.get("model") or "")
                render_advanced_fields()
                sync_advanced_fold()
                for widget in ua_row_widgets:
                    if widget is not None:
                        widget.setVisible(self._api_supports_user_agent(s))
                status_label.setText("")
                sync_summary()

            def on_tpl_change(choice, s=state, status_label=status, model_widget=model_box):
                rev = {lbl: key for key, lbl in self._api_template_choices(s.get("format"))}
                previous_default = self._api_default_model_for_state(s)
                s["tpl_key"] = rev.get(choice, s.get("tpl_key"))
                s["model_status"] = ""
                s["raw_extra"] = self._api_prune_managed_extra(s.get("raw_extra"), drop_template=True, drop_format=False)
                self._api_apply_template_model(s, previous_default)
                model_widget.setCurrentText(s.get("model") or "")
                status_label.setText("")
                render_advanced_fields()
                sync_advanced_fold()
                sync_summary()

            format_box.currentTextChanged.connect(on_format_change)
            tpl_box.currentTextChanged.connect(on_tpl_change)
            url_edit.textChanged.connect(lambda text, s=state: s.__setitem__("apibase", text))
            key_edit.textChanged.connect(lambda text, s=state: s.__setitem__("apikey", text))
            model_box.currentTextChanged.connect(lambda text, s=state: s.__setitem__("model", text.strip()))
            advanced_toggle.toggled.connect(sync_advanced_fold)
            render_advanced_fields()
            sync_advanced_fold()
            ua_edit.textChanged.connect(lambda text, s=state: s.__setitem__("user_agent", text.strip()))
            sync_summary()
            self.settings_api_list_layout.addWidget(card)
        self.settings_api_list_layout.addStretch(1)

    def _qt_api_add_channel(self, format_key, *, render=True):
        fmt = self._api_format_meta(format_key)
        kind = fmt.get("kind")
        if kind not in ("native_claude", "native_oai"):
            return
        existing = {s["var"] for s in self._qt_api_state}
        existing.update({c["var"] for c in self._qt_api_hidden_configs})
        var = lz.auto_config_var(kind, existing)
        tpl_key = fmt.get("default_template", "openai")
        defaults = dict(lz.TEMPLATE_INDEX.get(tpl_key, {}).get("defaults") or {})
        self._qt_api_state.append(
            {
                "var": var,
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
        state["model_fetching"] = True
        state["model_status"] = "正在拉取模型列表…"
        self._render_api_cards()

        def worker():
            try:
                models = lz._fetch_remote_models(state.get("format"), state.get("apibase"), state.get("apikey"))

                def done_ok():
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
            txt = lz.serialize_mykey_py(
                configs=self._api_build_save_configs(),
                extras=self._qt_api_extras,
                passthrough=self._qt_api_passthrough,
            )
            with open(self._qt_api_py_path, "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        restarted = self._restart_running_channels(show_errors=False)
        if restart:
            self._restart_bridge()
            QMessageBox.information(self, "已保存", "已写入 mykey.py，并已重启聊天内核。")
        else:
            extra = f"\n已自动重启 {restarted} 个由启动器托管的通讯渠道。" if restarted else ""
            QMessageBox.information(self, "已保存", "已写入 mykey.py。聊天内核需重启后才会读取新配置。" + extra)
        self._reload_api_editor_state()
        self._reload_channels_editor_state()

    def _open_raw_mykey_editor(self):
        if not lz.is_valid_agent_dir(self.agent_dir):
            QMessageBox.warning(self, "目录无效", "请先选择有效的 GenericAgent 目录。")
            return
        py_path, _ = self._load_mykey_source()
        try:
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
            f"QTextEdit {{ background: {C['field_bg']}; color: {C['code_text']}; border: 1px solid {C['stroke_default']}; border-radius: {F['radius_md']}px; font-family: Consolas, 'Microsoft YaHei UI'; font-size: 13px; }}"
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
                with open(py_path, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception as e:
                QMessageBox.critical(dlg, "保存失败", str(e))
                return
            restarted = self._restart_running_channels(show_errors=False)
            if restart:
                self._restart_bridge()
            self._reload_api_editor_state()
            self._reload_channels_editor_state()
            msg = "已写入 mykey.py。"
            if restarted:
                msg += f"\n已自动重启 {restarted} 个由启动器托管的通讯渠道。"
            if restart:
                msg += "\n聊天内核也已重启。"
            QMessageBox.information(dlg, "已保存", msg)
            dlg.accept()

        cancel_btn.clicked.connect(dlg.reject)
        save_btn.clicked.connect(lambda: do_save(False))
        restart_btn.clicked.connect(lambda: do_save(True))
        dlg.exec()
