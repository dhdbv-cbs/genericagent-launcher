from __future__ import annotations

import os
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from launcher_app import core as lz
from launcher_app.theme import C

_SCROLLBAR_STYLE = """
QScrollBar:vertical { width: 10px; background: transparent; border: none; margin: 2px; }
QScrollBar::handle:vertical {
    background: rgba(148,163,184,0.28); border-radius: 4px; min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: rgba(148,163,184,0.50); }
QScrollBar::handle:vertical:pressed { background: rgba(148,163,184,0.70); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; background: none; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""


class _ThemeCropPreview(QWidget):
    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self._pixmap = pixmap
        self._zoom_percent = 100
        self._offset = QPointF(0.0, 0.0)
        self._dragging = False
        self._last_pos = QPointF(0.0, 0.0)
        self.setMinimumSize(520, 320)
        self.setCursor(Qt.OpenHandCursor)

    def _fit_scale(self) -> float:
        pw = float(max(1, self._pixmap.width()))
        ph = float(max(1, self._pixmap.height()))
        ww = float(max(1, self.width()))
        wh = float(max(1, self.height()))
        # Start from full-image visible ("contain"), then user can zoom in/out.
        return min(ww / pw, wh / ph)

    def _scale(self) -> float:
        return self._fit_scale() * max(1.0, float(self._zoom_percent) / 100.0)

    def _scaled_size(self):
        scale = self._scale()
        return float(self._pixmap.width()) * scale, float(self._pixmap.height()) * scale

    def _top_left(self) -> QPointF:
        sw, sh = self._scaled_size()
        x = (self.width() - sw) / 2.0 + self._offset.x()
        y = (self.height() - sh) / 2.0 + self._offset.y()
        return QPointF(x, y)

    def _clamp_offset(self):
        sw, sh = self._scaled_size()
        max_dx = max(0.0, (sw - self.width()) / 2.0)
        max_dy = max(0.0, (sh - self.height()) / 2.0)
        x = min(max(self._offset.x(), -max_dx), max_dx)
        y = min(max(self._offset.y(), -max_dy), max_dy)
        self._offset = QPointF(x, y)

    def set_zoom_percent(self, value: int):
        self._zoom_percent = max(20, min(500, int(value or 100)))
        self._clamp_offset()
        self.update()

    def crop_norm(self):
        scale = self._scale()
        top_left = self._top_left()
        src_w = float(max(1, self._pixmap.width()))
        src_h = float(max(1, self._pixmap.height()))
        x = max(0.0, min(src_w, (-top_left.x()) / scale))
        y = max(0.0, min(src_h, (-top_left.y()) / scale))
        w = max(1.0, min(src_w - x, float(self.width()) / scale))
        h = max(1.0, min(src_h - y, float(self.height()) / scale))
        return {
            "x": x / src_w,
            "y": y / src_h,
            "w": w / src_w,
            "h": h / src_h,
        }

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self._dragging = True
        self._last_pos = event.position()
        self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return super().mouseMoveEvent(event)
        pos = event.position()
        delta = pos - self._last_pos
        self._last_pos = pos
        self._offset = QPointF(self._offset.x() + delta.x(), self._offset.y() + delta.y())
        self._clamp_offset()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), Qt.black)
        top_left = self._top_left()
        sw, sh = self._scaled_size()
        target = QRectF(top_left.x(), top_left.y(), sw, sh)
        source = QRectF(0, 0, self._pixmap.width(), self._pixmap.height())
        painter.drawPixmap(target, self._pixmap, source)
        pen = QPen(Qt.white)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))


class _ThemeCropDialog(QDialog):
    def __init__(self, image_path: str, target_size: QSize, parent=None):
        super().__init__(parent)
        self._target_size = QSize(max(320, int(target_size.width())), max(240, int(target_size.height())))
        self._crop_norm = None
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            raise ValueError("无法读取图片，请更换一张文件。")

        self.setWindowTitle("裁切背景图片")
        self.setModal(True)
        self.resize(980, 760)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        info = QLabel(
            f"输出尺寸：{self._target_size.width()} x {self._target_size.height()}（启动器当前尺寸）\n"
            "拖动图片调整位置，滑动缩放后点击“确定裁切”。100% 为完整显示原图。"
        )
        info.setWordWrap(True)
        info.setObjectName("mutedText")
        root.addWidget(info)

        ratio = float(self._target_size.width()) / float(max(1, self._target_size.height()))
        max_w, max_h = 920, 560
        width = max_w
        height = int(width / ratio)
        if height > max_h:
            height = max_h
            width = int(height * ratio)
        # Keep the preview ratio strictly aligned with target output ratio.
        # For tall targets (like floating window), forcing a large min width
        # would distort the preview ratio and produce visible mismatch.
        width = max(260, width)
        height = max(240, int(width / ratio))
        if height > max_h:
            height = max_h
            width = max(220, int(height * ratio))

        self.preview = _ThemeCropPreview(pixmap, self)
        self.preview.setFixedSize(width, height)
        root.addWidget(self.preview, 0, Qt.AlignCenter)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(8)
        zoom_title = QLabel("缩放")
        zoom_title.setObjectName("bodyText")
        zoom_row.addWidget(zoom_title, 0)
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(20, 500)
        self.zoom_slider.setValue(100)
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        zoom_row.addWidget(self.zoom_slider, 1)
        self.zoom_value = QLabel("100%")
        self.zoom_value.setObjectName("softTextSmall")
        self.zoom_value.setFixedWidth(56)
        zoom_row.addWidget(self.zoom_value, 0)
        root.addLayout(zoom_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn, 0)
        ok_btn = QPushButton("确定裁切")
        ok_btn.clicked.connect(self._accept_crop)
        buttons.addWidget(ok_btn, 0)
        root.addLayout(buttons)

    def _on_zoom_changed(self, value):
        zoom = max(20, int(value or 100))
        self.preview.set_zoom_percent(zoom)
        self.zoom_value.setText(f"{zoom}%")

    def _accept_crop(self):
        self._crop_norm = self.preview.crop_norm()
        self.accept()

    def crop_norm(self):
        return dict(self._crop_norm or {})


class SettingsPanelMixin:
    def _theme_combo_style(self):
        styler = getattr(self, "_api_combo_style", None)
        if callable(styler):
            return styler()
        field_bg = str(C.get("field_bg") or "#ffffff")
        text = str(C.get("text") or "#1a1f2b")
        border = str(C.get("stroke_default") or "#c7cfdd")
        border_hover = str(C.get("stroke_hover") or "#9aa6bc")
        selection_bg = str(C.get("accent_soft_bg") or "#dbe7ff")
        arrow = str(C.get("muted") or "#6b7280")
        return (
            f"QComboBox {{ background: {field_bg}; color: {text}; "
            f"border: 1px solid {border}; border-radius: 8px; padding: 6px 28px 6px 10px; min-height: 20px; }}"
            f"QComboBox:hover {{ border-color: {border_hover}; }}"
            "QComboBox::drop-down { border: none; width: 22px; }"
            "QComboBox::down-arrow { image: none; width: 0px; height: 0px; border-left: 5px solid transparent; "
            f"border-right: 5px solid transparent; border-top: 6px solid {arrow}; margin-right: 8px; }}"
            f"QComboBox QAbstractItemView {{ background: {field_bg}; color: {text}; border: 1px solid {border}; "
            f"border-radius: 8px; padding: 4px; selection-background-color: {selection_bg}; selection-color: {text}; outline: 0; }}"
        )

    def _apply_theme_combo_style(self, combo):
        if combo is None:
            return
        try:
            combo.setStyleSheet(self._theme_combo_style())
        except Exception:
            pass

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_wrap = QFrame()
        top_wrap.setObjectName("settingsTopbar")
        top = QHBoxLayout(top_wrap)
        top.setContentsMargins(24, 14, 24, 14)
        top.setSpacing(10)
        back_btn = QPushButton("←  返回聊天")
        back_btn.setStyleSheet(self._sidebar_button_style(subtle=True))
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(self._show_chat_page)
        self._settings_top_back_btn = back_btn
        top.addWidget(back_btn, 0)
        title = QLabel("设置")
        title.setObjectName("cardTitle")
        top.addWidget(title, 0)
        top.addStretch(1)
        layout.addWidget(top_wrap)

        body = QFrame()
        body.setObjectName("settingsBody")
        body_row = QHBoxLayout(body)
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)
        layout.addWidget(body, 1)

        nav = QFrame()
        nav.setObjectName("settingsNav")
        nav.setFixedWidth(220)
        nav_col = QVBoxLayout(nav)
        nav_col.setContentsMargins(10, 16, 10, 16)
        nav_col.setSpacing(2)
        nav_label = QLabel("分类")
        nav_label.setObjectName("sectionLabel")
        nav_col.addWidget(nav_label)
        body_row.addWidget(nav, 0)

        content_wrap = QFrame()
        content_wrap.setStyleSheet("background: transparent;")
        content_col = QVBoxLayout(content_wrap)
        content_col.setContentsMargins(24, 20, 24, 20)
        content_col.setSpacing(12)
        self.settings_status_label = QLabel("")
        self.settings_status_label.setWordWrap(True)
        self.settings_status_label.setObjectName("mutedText")
        content_col.addWidget(self.settings_status_label)
        self.settings_stack = QStackedWidget()
        content_col.addWidget(self.settings_stack, 1)
        body_row.addWidget(content_wrap, 1)

        self._settings_nav_buttons = {}
        self._settings_pages = {}
        categories = [
            ("api", "🔑  API"),
            ("channels", "💬  通讯渠道"),
            ("schedule", "⏰  定时任务"),
            ("personal", "🧩  个性设置"),
            ("theme", "🎨  主题设置"),
            ("usage", "🧾  使用日志"),
            ("about", "ℹ  关于"),
        ]

        def make_page():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setStyleSheet(f"QScrollArea {{ border: none; background: transparent; }}" + _SCROLLBAR_STYLE)
            inner = QWidget()
            scroll.setWidget(inner)
            inner_layout = QVBoxLayout(inner)
            inner_layout.setContentsMargins(0, 0, 0, 0)
            inner_layout.setSpacing(12)
            return scroll, inner_layout

        for key, label_text in categories:
            btn = QPushButton(label_text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._sidebar_button_style(subtle=True))
            btn.clicked.connect(lambda _=False, k=key: self._show_settings_category(k))
            nav_col.addWidget(btn)
            self._settings_nav_buttons[key] = btn

            page_widget, page_layout = make_page()
            self.settings_stack.addWidget(page_widget)
            self._settings_pages[key] = {"widget": page_widget, "layout": page_layout}

        nav_col.addStretch(1)

        api_layout = self._settings_pages["api"]["layout"]
        api_layout.addWidget(
            self._settings_intro(
                "API 配置",
                "这里直接维护 GenericAgent 的 mykey.py。保存后可以按需只保存，或保存并重启内核。",
            )
        )
        api_card = self._panel_card()
        api_box = QVBoxLayout(api_card)
        api_box.setContentsMargins(20, 18, 20, 18)
        api_box.setSpacing(10)
        api_title = QLabel("配置卡片")
        api_title.setObjectName("cardTitle")
        api_box.addWidget(api_title)
        api_desc = QLabel("下方卡片会写回当前目录内的 mykey.py。")
        api_desc.setObjectName("cardDesc")
        api_box.addWidget(api_desc)
        api_toolbar = QHBoxLayout()
        api_toolbar.setSpacing(8)
        api_add_btn = QPushButton("+ 添加 API 卡片")
        api_add_btn.setStyleSheet(self._action_button_style(primary=True))
        api_add_btn.clicked.connect(lambda: self._qt_api_add_channel("oai_chat"))
        api_toolbar.addWidget(api_add_btn, 0)
        api_save_btn = QPushButton("仅保存")
        api_save_btn.setStyleSheet(self._action_button_style())
        api_save_btn.clicked.connect(lambda: self._qt_api_save(restart=False))
        api_toolbar.addWidget(api_save_btn, 0)
        api_restart_btn = QPushButton("保存并重启内核")
        api_restart_btn.setStyleSheet(self._action_button_style())
        api_restart_btn.clicked.connect(lambda: self._qt_api_save(restart=True))
        api_toolbar.addWidget(api_restart_btn, 0)
        api_raw_btn = QPushButton("直接编辑文件")
        api_raw_btn.setStyleSheet(self._action_button_style())
        api_raw_btn.clicked.connect(self._open_raw_mykey_editor)
        api_toolbar.addWidget(api_raw_btn, 0)
        api_toolbar.addStretch(1)
        api_box.addLayout(api_toolbar)
        self.settings_api_notice = QLabel("")
        self.settings_api_notice.setWordWrap(True)
        self.settings_api_notice.setObjectName("mutedText")
        api_box.addWidget(self.settings_api_notice)
        self.settings_api_list = QWidget()
        self.settings_api_list_layout = QVBoxLayout(self.settings_api_list)
        self.settings_api_list_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_api_list_layout.setSpacing(10)
        api_box.addWidget(self.settings_api_list)
        api_layout.addWidget(api_card)
        api_layout.addStretch(1)

        ch_layout = self._settings_pages["channels"]["layout"]
        ch_layout.addWidget(
            self._settings_intro(
                "通讯渠道",
                "这里接的是 GenericAgent 原项目的渠道脚本。它们各自启动独立进程，不和当前聊天主区共用上下文。",
            )
        )
        channels_card = self._panel_card()
        channels_box = QVBoxLayout(channels_card)
        channels_box.setContentsMargins(20, 18, 20, 18)
        channels_box.setSpacing(10)
        channels_title = QLabel("渠道配置")
        channels_title.setObjectName("cardTitle")
        channels_box.addWidget(channels_title)
        channels_desc = QLabel("可在这里维护字段、查看运行状态，并直接启动或停止由启动器托管的渠道进程。")
        channels_desc.setWordWrap(True)
        channels_desc.setObjectName("cardDesc")
        channels_box.addWidget(channels_desc)
        channel_toolbar = QHBoxLayout()
        channel_toolbar.setSpacing(8)
        channel_save_btn = QPushButton("保存通讯配置")
        channel_save_btn.setStyleSheet(self._action_button_style(primary=True))
        channel_save_btn.clicked.connect(lambda: self._qt_channels_save(silent=False))
        channel_toolbar.addWidget(channel_save_btn, 0)
        channel_refresh_btn = QPushButton("刷新状态")
        channel_refresh_btn.setStyleSheet(self._action_button_style())
        channel_refresh_btn.clicked.connect(self._reload_channels_editor_state)
        channel_toolbar.addWidget(channel_refresh_btn, 0)
        channel_stop_btn = QPushButton("停止全部")
        channel_stop_btn.setStyleSheet(self._action_button_style())
        channel_stop_btn.clicked.connect(self._stop_all_managed_channels)
        channel_toolbar.addWidget(channel_stop_btn, 0)
        channel_toolbar.addStretch(1)
        channels_box.addLayout(channel_toolbar)
        self.settings_channels_notice = QLabel("")
        self.settings_channels_notice.setWordWrap(True)
        self.settings_channels_notice.setObjectName("mutedText")
        channels_box.addWidget(self.settings_channels_notice)
        self.settings_channels_list = QWidget()
        self.settings_channels_list_layout = QVBoxLayout(self.settings_channels_list)
        self.settings_channels_list_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_channels_list_layout.setSpacing(10)
        channels_box.addWidget(self.settings_channels_list)
        ch_layout.addWidget(channels_card)
        ch_layout.addStretch(1)

        sch_layout = self._settings_pages["schedule"]["layout"]
        sch_layout.addWidget(
            self._settings_intro(
                "定时任务",
                "这里直接读写上游 sche_tasks 任务，支持新建、编辑、启用，并联动调度器状态。",
            )
        )
        schedule_card = self._panel_card()
        schedule_box = QVBoxLayout(schedule_card)
        schedule_box.setContentsMargins(20, 18, 20, 18)
        schedule_box.setSpacing(10)
        schedule_title = QLabel("上游任务识别")
        schedule_title.setObjectName("cardTitle")
        schedule_box.addWidget(schedule_title)
        schedule_desc = QLabel("AI 在上游新建任务后，点一次刷新就会出现在这里；每张卡片都可以直接编辑。")
        schedule_desc.setWordWrap(True)
        schedule_desc.setObjectName("cardDesc")
        schedule_box.addWidget(schedule_desc)
        schedule_toolbar = QHBoxLayout()
        schedule_toolbar.setSpacing(8)
        schedule_add_btn = QPushButton("新建任务")
        schedule_add_btn.setStyleSheet(self._action_button_style())
        schedule_add_btn.clicked.connect(self._schedule_add_task_card)
        schedule_toolbar.addWidget(schedule_add_btn, 0)
        schedule_refresh_btn = QPushButton("刷新任务")
        schedule_refresh_btn.setStyleSheet(self._action_button_style(primary=True))
        schedule_refresh_btn.clicked.connect(self._reload_schedule_panel)
        schedule_toolbar.addWidget(schedule_refresh_btn, 0)
        schedule_toolbar.addStretch(1)
        schedule_box.addLayout(schedule_toolbar)
        self.settings_schedule_notice = QLabel("")
        self.settings_schedule_notice.setWordWrap(True)
        self.settings_schedule_notice.setObjectName("mutedText")
        schedule_box.addWidget(self.settings_schedule_notice)
        self.settings_schedule_list = QWidget()
        self.settings_schedule_list_layout = QVBoxLayout(self.settings_schedule_list)
        self.settings_schedule_list_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_schedule_list_layout.setSpacing(10)
        schedule_box.addWidget(self.settings_schedule_list)
        sch_layout.addWidget(schedule_card)
        sch_layout.addStretch(1)

        personal_layout = self._settings_pages["personal"]["layout"]
        personal_layout.addWidget(
            self._settings_intro(
                "个性设置",
                "这里用于控制会话保留策略。你可以按主聊天区和各通讯渠道分别设置活跃会话上限，超过后会自动删除最旧未收藏会话。",
            )
        )
        personal_card = self._panel_card()
        personal_box = QVBoxLayout(personal_card)
        personal_box.setContentsMargins(20, 18, 20, 18)
        personal_box.setSpacing(10)
        personal_title = QLabel("自动清理")
        personal_title.setObjectName("cardTitle")
        personal_box.addWidget(personal_title)
        personal_desc = QLabel("数值表示该渠道保留在侧边栏中的活跃会话上限。填 0 表示关闭该渠道的自动清理。默认值是 10。")
        personal_desc.setWordWrap(True)
        personal_desc.setObjectName("cardDesc")
        personal_box.addWidget(personal_desc)
        personal_toolbar = QHBoxLayout()
        personal_toolbar.setSpacing(8)
        personal_save_btn = QPushButton("保存并立即执行")
        personal_save_btn.setStyleSheet(self._action_button_style(primary=True))
        personal_save_btn.clicked.connect(self._save_archive_settings)
        personal_toolbar.addWidget(personal_save_btn, 0)
        personal_refresh_btn = QPushButton("刷新统计")
        personal_refresh_btn.setStyleSheet(self._action_button_style())
        personal_refresh_btn.clicked.connect(self._reload_personal_panel)
        personal_toolbar.addWidget(personal_refresh_btn, 0)
        personal_toolbar.addStretch(1)
        personal_box.addLayout(personal_toolbar)
        self.settings_personal_notice = QLabel("")
        self.settings_personal_notice.setWordWrap(True)
        self.settings_personal_notice.setObjectName("mutedText")
        personal_box.addWidget(self.settings_personal_notice)
        self.settings_personal_list = QWidget()
        self.settings_personal_list_layout = QVBoxLayout(self.settings_personal_list)
        self.settings_personal_list_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_personal_list_layout.setSpacing(10)
        personal_box.addWidget(self.settings_personal_list)
        personal_layout.addWidget(personal_card)

        notify_card = self._panel_card()
        notify_box = QVBoxLayout(notify_card)
        notify_box.setContentsMargins(20, 18, 20, 18)
        notify_box.setSpacing(10)
        notify_title = QLabel("回复提醒")
        notify_title.setObjectName("cardTitle")
        notify_box.addWidget(notify_title)
        notify_desc = QLabel("分别控制 AI 回复完成后的提示音和系统托盘提示消息。勾选后表示关闭该提醒。")
        notify_desc.setWordWrap(True)
        notify_desc.setObjectName("cardDesc")
        notify_box.addWidget(notify_desc)
        self.settings_disable_reply_sound = QCheckBox("关闭提示音")
        notify_box.addWidget(self.settings_disable_reply_sound)
        self.settings_disable_reply_message = QCheckBox("关闭提示消息")
        notify_box.addWidget(self.settings_disable_reply_message)
        notify_toolbar = QHBoxLayout()
        notify_toolbar.setSpacing(8)
        notify_save_btn = QPushButton("保存提醒设置")
        notify_save_btn.setStyleSheet(self._action_button_style())
        notify_save_btn.clicked.connect(self._save_personal_preferences)
        notify_toolbar.addWidget(notify_save_btn, 0)
        notify_toolbar.addStretch(1)
        notify_box.addLayout(notify_toolbar)
        personal_layout.addWidget(notify_card)
        personal_layout.addStretch(1)

        theme_layout = self._settings_pages["theme"]["layout"]
        theme_layout.addWidget(
            self._settings_intro(
                "主题设置",
                "这里可以单独设置界面字体、字重和背景样式。背景支持自定义图片，并可设置居中、拉伸或平铺。",
            )
        )
        theme_card = self._panel_card()
        theme_box = QVBoxLayout(theme_card)
        theme_box.setContentsMargins(20, 18, 20, 18)
        theme_box.setSpacing(10)
        theme_title = QLabel("视觉偏好")
        theme_title.setObjectName("cardTitle")
        theme_box.addWidget(theme_title)
        theme_desc = QLabel("保存后会立即应用；同时写入 launcher_config.json，重启后保持不变。")
        theme_desc.setWordWrap(True)
        theme_desc.setObjectName("cardDesc")
        theme_box.addWidget(theme_desc)

        self.settings_theme_notice = QLabel("")
        self.settings_theme_notice.setWordWrap(True)
        self.settings_theme_notice.setObjectName("mutedText")
        theme_box.addWidget(self.settings_theme_notice)

        font_row = QHBoxLayout()
        font_row.setSpacing(8)
        font_label = QLabel("字体")
        font_label.setMinimumWidth(92)
        font_label.setObjectName("bodyText")
        font_row.addWidget(font_label, 0)
        self.settings_theme_font_combo = QComboBox()
        self.settings_theme_font_combo.setMinimumWidth(320)
        self._apply_theme_combo_style(self.settings_theme_font_combo)
        font_row.addWidget(self.settings_theme_font_combo, 1)
        theme_box.addLayout(font_row)

        weight_row = QHBoxLayout()
        weight_row.setSpacing(8)
        weight_label = QLabel("字重")
        weight_label.setMinimumWidth(92)
        weight_label.setObjectName("bodyText")
        weight_row.addWidget(weight_label, 0)
        self.settings_theme_weight_combo = QComboBox()
        self.settings_theme_weight_combo.addItem("常规 (400)", "400")
        self.settings_theme_weight_combo.addItem("中等 (500)", "500")
        self.settings_theme_weight_combo.addItem("半粗 (600)", "600")
        self.settings_theme_weight_combo.addItem("粗体 (700)", "700")
        self._apply_theme_combo_style(self.settings_theme_weight_combo)
        weight_row.addWidget(self.settings_theme_weight_combo, 1)
        theme_box.addLayout(weight_row)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_label = QLabel("字号")
        size_label.setMinimumWidth(92)
        size_label.setObjectName("bodyText")
        size_row.addWidget(size_label, 0)
        self.settings_theme_size_combo = QComboBox()
        self.settings_theme_size_combo.addItem("11 (紧凑)", "11")
        self.settings_theme_size_combo.addItem("12 (偏小)", "12")
        self.settings_theme_size_combo.addItem("13", "13")
        self.settings_theme_size_combo.addItem("14 (默认)", "14")
        self.settings_theme_size_combo.addItem("15", "15")
        self.settings_theme_size_combo.addItem("16", "16")
        self.settings_theme_size_combo.addItem("18", "18")
        self.settings_theme_size_combo.addItem("20", "20")
        self._apply_theme_combo_style(self.settings_theme_size_combo)
        size_row.addWidget(self.settings_theme_size_combo, 1)
        theme_box.addLayout(size_row)

        bg_row = QHBoxLayout()
        bg_row.setSpacing(8)
        bg_label = QLabel("背景预设")
        bg_label.setMinimumWidth(92)
        bg_label.setObjectName("bodyText")
        bg_row.addWidget(bg_label, 0)
        self.settings_theme_bg_combo = QComboBox()
        self.settings_theme_bg_combo.addItem("跟随主题默认", "default")
        self.settings_theme_bg_combo.addItem("雾蓝", "mist")
        self.settings_theme_bg_combo.addItem("暖米", "warm")
        self.settings_theme_bg_combo.addItem("石墨", "graphite")
        self.settings_theme_bg_combo.addItem("图片背景", "image")
        self._apply_theme_combo_style(self.settings_theme_bg_combo)
        bg_row.addWidget(self.settings_theme_bg_combo, 1)
        theme_box.addLayout(bg_row)

        bg_img_row = QHBoxLayout()
        bg_img_row.setSpacing(8)
        bg_img_label = QLabel("背景图片")
        bg_img_label.setMinimumWidth(92)
        bg_img_label.setObjectName("bodyText")
        bg_img_row.addWidget(bg_img_label, 0)
        self.settings_theme_bg_image_path = QLineEdit()
        self.settings_theme_bg_image_path.setReadOnly(True)
        self.settings_theme_bg_image_path.setPlaceholderText("未选择图片")
        self._fluent_input(self.settings_theme_bg_image_path)
        bg_img_row.addWidget(self.settings_theme_bg_image_path, 1)
        bg_img_choose_btn = QPushButton("选择图片")
        bg_img_choose_btn.setStyleSheet(self._action_button_style())
        bg_img_choose_btn.clicked.connect(self._choose_theme_background_image)
        bg_img_row.addWidget(bg_img_choose_btn, 0)
        bg_img_clear_btn = QPushButton("清除")
        bg_img_clear_btn.setStyleSheet(self._action_button_style(kind="subtle"))
        bg_img_clear_btn.clicked.connect(self._clear_theme_background_image)
        bg_img_row.addWidget(bg_img_clear_btn, 0)
        theme_box.addLayout(bg_img_row)

        bg_mode_row = QHBoxLayout()
        bg_mode_row.setSpacing(8)
        bg_mode_label = QLabel("图片显示")
        bg_mode_label.setMinimumWidth(92)
        bg_mode_label.setObjectName("bodyText")
        bg_mode_row.addWidget(bg_mode_label, 0)
        self.settings_theme_bg_mode_combo = QComboBox()
        self.settings_theme_bg_mode_combo.addItem("居中", "center")
        self.settings_theme_bg_mode_combo.addItem("拉伸", "stretch")
        self.settings_theme_bg_mode_combo.addItem("平铺", "tile")
        self._apply_theme_combo_style(self.settings_theme_bg_mode_combo)
        bg_mode_row.addWidget(self.settings_theme_bg_mode_combo, 1)
        theme_box.addLayout(bg_mode_row)

        fade_row = QHBoxLayout()
        fade_row.setSpacing(8)
        fade_label = QLabel("淡化值")
        fade_label.setMinimumWidth(92)
        fade_label.setObjectName("bodyText")
        fade_row.addWidget(fade_label, 0)
        self.settings_theme_fade_slider = QSlider(Qt.Horizontal)
        self.settings_theme_fade_slider.setRange(0, 100)
        self.settings_theme_fade_slider.setValue(0)
        self.settings_theme_fade_slider.valueChanged.connect(self._on_theme_fade_changed)
        fade_row.addWidget(self.settings_theme_fade_slider, 1)
        self.settings_theme_fade_value = QLabel("0")
        self.settings_theme_fade_value.setObjectName("softTextSmall")
        self.settings_theme_fade_value.setFixedWidth(42)
        fade_row.addWidget(self.settings_theme_fade_value, 0)
        theme_box.addLayout(fade_row)

        theme_toolbar = QHBoxLayout()
        theme_toolbar.setSpacing(8)
        theme_save_btn = QPushButton("保存主题设置")
        theme_save_btn.setStyleSheet(self._action_button_style(primary=True))
        theme_save_btn.clicked.connect(self._save_theme_preferences)
        theme_toolbar.addWidget(theme_save_btn, 0)
        theme_toolbar.addStretch(1)
        theme_box.addLayout(theme_toolbar)

        floating_sep = QFrame()
        floating_sep.setObjectName("divider")
        floating_sep.setFixedHeight(1)
        theme_box.addWidget(floating_sep)

        floating_title = QLabel("悬浮窗背景")
        floating_title.setObjectName("cardTitle")
        theme_box.addWidget(floating_title)
        floating_desc = QLabel("可为悬浮窗单独配置背景图，不影响主界面背景。默认“跟随主背景”。")
        floating_desc.setWordWrap(True)
        floating_desc.setObjectName("cardDesc")
        theme_box.addWidget(floating_desc)

        floating_bg_row = QHBoxLayout()
        floating_bg_row.setSpacing(8)
        floating_bg_label = QLabel("悬浮窗预设")
        floating_bg_label.setMinimumWidth(92)
        floating_bg_label.setObjectName("bodyText")
        floating_bg_row.addWidget(floating_bg_label, 0)
        self.settings_theme_floating_bg_combo = QComboBox()
        self.settings_theme_floating_bg_combo.addItem("跟随主背景", "follow")
        self.settings_theme_floating_bg_combo.addItem("图片背景", "image")
        self._apply_theme_combo_style(self.settings_theme_floating_bg_combo)
        floating_bg_row.addWidget(self.settings_theme_floating_bg_combo, 1)
        theme_box.addLayout(floating_bg_row)

        floating_img_row = QHBoxLayout()
        floating_img_row.setSpacing(8)
        floating_img_label = QLabel("悬浮窗图片")
        floating_img_label.setMinimumWidth(92)
        floating_img_label.setObjectName("bodyText")
        floating_img_row.addWidget(floating_img_label, 0)
        self.settings_theme_floating_bg_image_path = QLineEdit()
        self.settings_theme_floating_bg_image_path.setReadOnly(True)
        self.settings_theme_floating_bg_image_path.setPlaceholderText("未选择图片")
        self._fluent_input(self.settings_theme_floating_bg_image_path)
        floating_img_row.addWidget(self.settings_theme_floating_bg_image_path, 1)
        floating_img_choose_btn = QPushButton("选择图片")
        floating_img_choose_btn.setStyleSheet(self._action_button_style())
        floating_img_choose_btn.clicked.connect(self._choose_theme_floating_background_image)
        floating_img_row.addWidget(floating_img_choose_btn, 0)
        floating_img_clear_btn = QPushButton("清除")
        floating_img_clear_btn.setStyleSheet(self._action_button_style(kind="subtle"))
        floating_img_clear_btn.clicked.connect(self._clear_theme_floating_background_image)
        floating_img_row.addWidget(floating_img_clear_btn, 0)
        theme_box.addLayout(floating_img_row)

        floating_mode_row = QHBoxLayout()
        floating_mode_row.setSpacing(8)
        floating_mode_label = QLabel("悬浮窗显示")
        floating_mode_label.setMinimumWidth(92)
        floating_mode_label.setObjectName("bodyText")
        floating_mode_row.addWidget(floating_mode_label, 0)
        self.settings_theme_floating_bg_mode_combo = QComboBox()
        self.settings_theme_floating_bg_mode_combo.addItem("居中", "center")
        self.settings_theme_floating_bg_mode_combo.addItem("拉伸", "stretch")
        self.settings_theme_floating_bg_mode_combo.addItem("平铺", "tile")
        self._apply_theme_combo_style(self.settings_theme_floating_bg_mode_combo)
        floating_mode_row.addWidget(self.settings_theme_floating_bg_mode_combo, 1)
        theme_box.addLayout(floating_mode_row)

        floating_fade_row = QHBoxLayout()
        floating_fade_row.setSpacing(8)
        floating_fade_label = QLabel("悬浮窗淡化")
        floating_fade_label.setMinimumWidth(92)
        floating_fade_label.setObjectName("bodyText")
        floating_fade_row.addWidget(floating_fade_label, 0)
        self.settings_theme_floating_fade_slider = QSlider(Qt.Horizontal)
        self.settings_theme_floating_fade_slider.setRange(0, 100)
        self.settings_theme_floating_fade_slider.setValue(0)
        self.settings_theme_floating_fade_slider.valueChanged.connect(self._on_theme_floating_fade_changed)
        floating_fade_row.addWidget(self.settings_theme_floating_fade_slider, 1)
        self.settings_theme_floating_fade_value = QLabel("0")
        self.settings_theme_floating_fade_value.setObjectName("softTextSmall")
        self.settings_theme_floating_fade_value.setFixedWidth(42)
        floating_fade_row.addWidget(self.settings_theme_floating_fade_value, 0)
        theme_box.addLayout(floating_fade_row)

        theme_layout.addWidget(theme_card)
        theme_layout.addStretch(1)

        usage_layout = self._settings_pages["usage"]["layout"]
        usage_layout.addWidget(
            self._settings_intro(
                "使用日志",
                "这里汇总本地会话里的 token / 模型 / 渠道 / 会话活动日志，并补充 Langfuse 追踪配置状态。标注说明：真实 = 直接读取模型接口返回的 usage；估算 = 按字符数 / 2.5 回推；混合 = 同一统计范围里两者都有。",
            )
        )
        usage_card = self._panel_card()
        usage_box = QVBoxLayout(usage_card)
        usage_box.setContentsMargins(20, 18, 20, 18)
        usage_box.setSpacing(10)
        usage_title = QLabel("日志总览")
        usage_title.setObjectName("cardTitle")
        usage_box.addWidget(usage_title)
        usage_desc = QLabel("这里显示的是启动器可见的本地日志；如果上游启用了 Langfuse，也会额外展示追踪配置和接线状态。")
        usage_desc.setWordWrap(True)
        usage_desc.setObjectName("cardDesc")
        usage_box.addWidget(usage_desc)
        self.settings_usage_notice = QLabel("")
        self.settings_usage_notice.setWordWrap(True)
        self.settings_usage_notice.setObjectName("mutedText")
        usage_box.addWidget(self.settings_usage_notice)
        self.settings_usage_list = QWidget()
        self.settings_usage_list_layout = QVBoxLayout(self.settings_usage_list)
        self.settings_usage_list_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_usage_list_layout.setSpacing(10)
        usage_box.addWidget(self.settings_usage_list)
        usage_layout.addWidget(usage_card)
        usage_layout.addStretch(1)

        about_layout = self._settings_pages["about"]["layout"]
        about_layout.addWidget(
            self._settings_intro(
                "关于启动器",
                "这是一个面向 GenericAgent 的桌面启动器，目标是把下载、配置、启动和日常聊天入口收拢到一个更直接的界面里。",
            )
        )
        about_card = self._panel_card()
        about_box = QVBoxLayout(about_card)
        about_box.setContentsMargins(20, 18, 20, 18)
        about_box.setSpacing(10)
        about_title = QLabel("基础信息")
        about_title.setObjectName("cardTitle")
        about_box.addWidget(about_title)
        self.settings_about_list = QWidget()
        self.settings_about_list_layout = QVBoxLayout(self.settings_about_list)
        self.settings_about_list_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_about_list_layout.setSpacing(10)
        about_box.addWidget(self.settings_about_list)
        about_layout.addWidget(about_card)
        about_layout.addStretch(1)

        self._show_settings_category("api")
        return page

    def _show_settings_category(self, key: str):
        if not hasattr(self, "settings_stack"):
            return
        page_info = (getattr(self, "_settings_pages", None) or {}).get(key)
        if not page_info:
            return
        self._current_settings_category = key
        self.settings_stack.setCurrentWidget(page_info["widget"])
        for nav_key, btn in (getattr(self, "_settings_nav_buttons", None) or {}).items():
            if nav_key == key:
                btn.setStyleSheet(self._sidebar_button_style(selected=True))
            else:
                btn.setStyleSheet(self._sidebar_button_style(subtle=True))

    def _load_mykey_source(self):
        agent_dir = self.agent_dir
        py_path = os.path.join(agent_dir, "mykey.py")
        tpl_path = os.path.join(agent_dir, "mykey_template.py")
        if os.path.isdir(agent_dir) and not os.path.isfile(py_path):
            if os.path.isfile(tpl_path):
                try:
                    with open(tpl_path, "r", encoding="utf-8") as src, open(py_path, "w", encoding="utf-8") as dst:
                        dst.write(src.read())
                except Exception:
                    pass
            else:
                try:
                    with open(py_path, "w", encoding="utf-8") as dst:
                        dst.write("# mykey.py\n")
                except Exception:
                    pass
        parsed = lz.parse_mykey_py(py_path)
        return py_path, parsed

    def _settings_reload(self):
        if not hasattr(self, "settings_status_label"):
            return
        valid = lz.is_valid_agent_dir(self.agent_dir)
        self.settings_status_label.setText(
            "当前目录有效，下面的 API 与渠道配置都会写回这个 GenericAgent 目录。"
            if valid else
            "还没有可用的 GenericAgent 目录，先在上面选择目录。"
        )
        self._reload_api_editor_state()
        self._reload_channels_editor_state()
        self._reload_schedule_panel()
        self._reload_personal_panel()
        self._reload_theme_panel()
        self._reload_usage_panel()
        self._reload_about_panel()

    def _select_combo_data(self, combo, value, default_index: int = 0):
        if combo is None:
            return
        target = str(value or "")
        for idx in range(combo.count()):
            if str(combo.itemData(idx) or "") == target:
                combo.setCurrentIndex(idx)
                return
        combo.setCurrentIndex(max(0, int(default_index or 0)))

    def _on_theme_fade_changed(self, value):
        label = getattr(self, "settings_theme_fade_value", None)
        if label is not None:
            label.setText(str(max(0, min(100, int(value or 0)))))

    def _on_theme_floating_fade_changed(self, value):
        label = getattr(self, "settings_theme_floating_fade_value", None)
        if label is not None:
            label.setText(str(max(0, min(100, int(value or 0)))))

    def _theme_target_size(self) -> QSize:
        width = max(960, int(getattr(self, "width", lambda: 1440)() or 1440))
        height = max(640, int(getattr(self, "height", lambda: 920)() or 920))
        return QSize(width, height)

    def _theme_floating_target_size(self) -> QSize:
        floating = getattr(self, "_floating_chat_window", None)
        if floating is not None:
            expanded = getattr(floating, "_expanded_size", None)
            if isinstance(expanded, QSize):
                try:
                    ew = int(expanded.width() or 0)
                    eh = int(expanded.height() or 0)
                except Exception:
                    ew, eh = 0, 0
                if ew > 0 and eh > 0:
                    return QSize(max(360, ew), max(460, eh))
            try:
                fw = int(floating.width() or 0)
                fh = int(floating.height() or 0)
            except Exception:
                fw, fh = 0, 0
            if fw > 0 and fh > 0:
                return QSize(max(360, fw), max(460, fh))
        return QSize(480, 760)

    def _refresh_theme_background_assets_for_mode(self) -> bool:
        cfg = self.cfg if isinstance(getattr(self, "cfg", None), dict) else None
        if cfg is None:
            return False

        mode_normalizer = getattr(self, "_normalize_appearance_mode", None)
        if callable(mode_normalizer):
            mode = mode_normalizer(cfg.get("appearance_mode", "light"))
        else:
            mode = "light" if str(cfg.get("appearance_mode", "light") or "").strip().lower() == "light" else "dark"
        changed = False

        render_schema = "v2"

        def _signature(source_rel: str, crop_data, fade_value: int, target_size: QSize) -> str:
            crop = self._normalize_theme_crop_data(crop_data) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
            return (
                f"{render_schema}|{mode}|{source_rel}|{int(fade_value)}|"
                f"{int(target_size.width())}x{int(target_size.height())}|"
                f"{crop['x']:.6f},{crop['y']:.6f},{crop['w']:.6f},{crop['h']:.6f}"
            )

        def _regen(
            *,
            preset_key: str,
            source_key: str,
            image_key: str,
            crop_key: str,
            fade_key: str,
            blur_key: str,
            sig_key: str,
            asset_tag: str,
            target_size: QSize,
            enforce_launcher_min: bool = True,
        ):
            nonlocal changed
            preset = str(cfg.get(preset_key) or "").strip().lower()
            if preset != "image":
                return
            source_rel = str(cfg.get(source_key) or cfg.get(image_key) or "").strip()
            source_abs = lz._resolve_config_path(source_rel) if source_rel else ""
            if not source_abs or not os.path.isfile(source_abs):
                return
            fade_value = max(0, min(100, int(cfg.get(fade_key, cfg.get(blur_key, 18)) or 18)))
            crop_data = cfg.get(crop_key)
            sig = _signature(source_rel, crop_data, fade_value, target_size)
            current_sig = str(cfg.get(sig_key) or "")
            current_rel = str(cfg.get(image_key) or "").strip()
            current_abs = lz._resolve_config_path(current_rel) if current_rel else ""
            if current_sig == sig and current_abs and os.path.isfile(current_abs):
                return
            generated_abs = self._render_theme_background_asset(
                source_abs,
                crop_data,
                target_size,
                fade_value,
                asset_tag=asset_tag,
                enforce_launcher_min=enforce_launcher_min,
            )
            cfg[image_key] = lz._make_config_relative_path(generated_abs)
            cfg[sig_key] = sig
            changed = True

        try:
            _regen(
                preset_key="theme_bg_preset",
                source_key="theme_bg_source",
                image_key="theme_bg_image",
                crop_key="theme_bg_crop",
                fade_key="theme_bg_fade",
                blur_key="theme_bg_blur",
                sig_key="theme_bg_render_sig",
                asset_tag="launcher_bg",
                target_size=self._theme_target_size(),
                enforce_launcher_min=True,
            )
            _regen(
                preset_key="theme_floating_bg_preset",
                source_key="theme_floating_bg_source",
                image_key="theme_floating_bg_image",
                crop_key="theme_floating_bg_crop",
                fade_key="theme_floating_bg_fade",
                blur_key="theme_floating_bg_blur",
                sig_key="theme_floating_bg_render_sig",
                asset_tag="floating_bg",
                target_size=self._theme_floating_target_size(),
                enforce_launcher_min=False,
            )
        except Exception:
            return False
        if changed:
            lz.save_config(cfg)
        return changed

    def _normalize_theme_crop_data(self, data):
        if not isinstance(data, dict):
            return None
        try:
            x = float(data.get("x", 0.0))
            y = float(data.get("y", 0.0))
            w = float(data.get("w", 1.0))
            h = float(data.get("h", 1.0))
        except Exception:
            return None
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        w = max(0.001, min(1.0, w))
        h = max(0.001, min(1.0, h))
        if x + w > 1.0:
            w = 1.0 - x
        if y + h > 1.0:
            h = 1.0 - y
        if w <= 0.0 or h <= 0.0:
            return None
        return {"x": x, "y": y, "w": w, "h": h}

    def _theme_apply_fade(self, image: QImage, fade_value: int) -> QImage:
        amount = max(0, min(100, int(fade_value or 0)))
        if amount <= 0 or image.isNull():
            return image
        mode = str(self.cfg.get("appearance_mode", "light") or "").strip().lower()
        base_color = QColor("#1c1e22" if mode == "dark" else "#f3f5f9")
        overlay_alpha = int(220 * (float(amount) / 100.0))
        src = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        out = QImage(src.size(), QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(0, 0, src)
        overlay = QColor(base_color)
        overlay.setAlpha(max(0, min(255, overlay_alpha)))
        painter.fillRect(out.rect(), overlay)
        painter.end()
        return out.convertToFormat(QImage.Format_RGB32)

    def _render_theme_background_asset(
        self,
        source_path: str,
        crop_data,
        target_size: QSize,
        fade_value: int,
        *,
        asset_tag: str = "launcher_bg",
        enforce_launcher_min: bool = True,
    ) -> str:
        src = str(source_path or "").strip()
        if not src or not os.path.isfile(src):
            raise ValueError("背景源图不存在，请重新选择。")
        image = QImage(src)
        if image.isNull():
            raise ValueError("无法读取背景图片，请更换文件格式后重试。")
        crop = self._normalize_theme_crop_data(crop_data) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        sw = int(image.width())
        sh = int(image.height())
        x = int(round(crop["x"] * sw))
        y = int(round(crop["y"] * sh))
        w = int(round(crop["w"] * sw))
        h = int(round(crop["h"] * sh))
        x = max(0, min(sw - 1, x))
        y = max(0, min(sh - 1, y))
        w = max(1, min(sw - x, w))
        h = max(1, min(sh - y, h))
        cropped = image.copy(x, y, w, h)
        if enforce_launcher_min:
            out_size = QSize(max(960, int(target_size.width())), max(640, int(target_size.height())))
        else:
            out_size = QSize(max(1, int(target_size.width())), max(1, int(target_size.height())))
        rendered = cropped.scaled(out_size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        if rendered.width() != out_size.width() or rendered.height() != out_size.height():
            ox = max(0, int((rendered.width() - out_size.width()) / 2))
            oy = max(0, int((rendered.height() - out_size.height()) / 2))
            rendered = rendered.copy(ox, oy, int(out_size.width()), int(out_size.height()))
        rendered = self._theme_apply_fade(rendered, fade_value)
        out_dir = os.path.join(lz.APP_DIR, "temp", "theme_background")
        os.makedirs(out_dir, exist_ok=True)
        safe_tag = "".join(ch for ch in str(asset_tag or "launcher_bg") if ch.isalnum() or ch in ("_", "-")).strip("_-")
        if not safe_tag:
            safe_tag = "launcher_bg"
        out_path = os.path.join(out_dir, f"{safe_tag}_{int(time.time() * 1000)}.png")
        if not rendered.save(out_path, "PNG"):
            raise ValueError("背景图片写入失败，请检查目录权限。")
        return out_path

    def _ensure_theme_font_options(self):
        combo = getattr(self, "settings_theme_font_combo", None)
        if combo is None:
            return
        if bool(getattr(self, "_theme_font_options_loaded", False)) and combo.count() > 0:
            return
        families = []
        try:
            db = QFontDatabase()
            families = sorted({str(name).strip() for name in db.families() if str(name).strip()}, key=lambda x: x.lower())
        except Exception:
            families = []
        preferred = [
            "Segoe UI Variable Text",
            "Segoe UI",
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "PingFang SC",
            "Noto Sans CJK SC",
            "Source Han Sans SC",
        ]
        merged = []
        seen = set()
        for name in preferred + families:
            key = str(name or "").strip()
            if not key:
                continue
            low = key.lower()
            if low in seen:
                continue
            seen.add(low)
            merged.append(key)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("跟随默认", "")
        for name in merged:
            combo.addItem(name, name)
        combo.blockSignals(False)
        self._theme_font_options_loaded = True

    def _reload_theme_panel(self):
        if not hasattr(self, "settings_theme_notice"):
            return
        self._ensure_theme_font_options()
        font_combo = getattr(self, "settings_theme_font_combo", None)
        weight_combo = getattr(self, "settings_theme_weight_combo", None)
        size_combo = getattr(self, "settings_theme_size_combo", None)
        bg_combo = getattr(self, "settings_theme_bg_combo", None)
        mode_combo = getattr(self, "settings_theme_bg_mode_combo", None)
        fade_slider = getattr(self, "settings_theme_fade_slider", None)
        floating_bg_combo = getattr(self, "settings_theme_floating_bg_combo", None)
        floating_mode_combo = getattr(self, "settings_theme_floating_bg_mode_combo", None)
        floating_fade_slider = getattr(self, "settings_theme_floating_fade_slider", None)
        for combo in (font_combo, weight_combo, size_combo, bg_combo, mode_combo, floating_bg_combo, floating_mode_combo):
            self._apply_theme_combo_style(combo)
        path_edit = getattr(self, "settings_theme_bg_image_path", None)
        floating_path_edit = getattr(self, "settings_theme_floating_bg_image_path", None)
        current_font = str(self.cfg.get("theme_font_family") or "").strip()
        self._select_combo_data(font_combo, current_font, default_index=0)
        self._select_combo_data(weight_combo, str(self.cfg.get("theme_font_weight") or "400"), default_index=0)
        self._select_combo_data(size_combo, str(self.cfg.get("theme_font_size") or "14"), default_index=3)
        self._select_combo_data(bg_combo, str(self.cfg.get("theme_bg_preset") or "default"), default_index=0)
        self._select_combo_data(mode_combo, str(self.cfg.get("theme_bg_image_mode") or "center"), default_index=0)
        self._select_combo_data(floating_bg_combo, str(self.cfg.get("theme_floating_bg_preset") or "follow"), default_index=0)
        self._select_combo_data(
            floating_mode_combo,
            str(self.cfg.get("theme_floating_bg_image_mode") or "center"),
            default_index=0,
        )
        fade_value = max(0, min(100, int(self.cfg.get("theme_bg_fade", self.cfg.get("theme_bg_blur", 18)) or 18)))
        if fade_slider is not None:
            fade_slider.blockSignals(True)
            fade_slider.setValue(fade_value)
            fade_slider.blockSignals(False)
        self._on_theme_fade_changed(fade_value)
        floating_fade_value = max(
            0,
            min(
                100,
                int(
                    self.cfg.get(
                        "theme_floating_bg_fade",
                        self.cfg.get("theme_floating_bg_blur", self.cfg.get("theme_bg_fade", self.cfg.get("theme_bg_blur", 18))),
                    )
                    or 18
                ),
            ),
        )
        if floating_fade_slider is not None:
            floating_fade_slider.blockSignals(True)
            floating_fade_slider.setValue(floating_fade_value)
            floating_fade_slider.blockSignals(False)
        self._on_theme_floating_fade_changed(floating_fade_value)
        source_rel = str(self.cfg.get("theme_bg_source") or self.cfg.get("theme_bg_image") or "").strip()
        source_abs = lz._resolve_config_path(source_rel) if source_rel else ""
        crop_data = self._normalize_theme_crop_data(self.cfg.get("theme_bg_crop")) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self._theme_bg_source_selected_path = source_abs if os.path.isfile(source_abs) else ""
        self._theme_bg_crop_selected = crop_data
        if path_edit is not None:
            path_edit.setText(self._theme_bg_source_selected_path)
        floating_source_rel = str(self.cfg.get("theme_floating_bg_source") or self.cfg.get("theme_floating_bg_image") or "").strip()
        floating_source_abs = lz._resolve_config_path(floating_source_rel) if floating_source_rel else ""
        floating_crop_data = self._normalize_theme_crop_data(self.cfg.get("theme_floating_bg_crop")) or {
            "x": 0.0,
            "y": 0.0,
            "w": 1.0,
            "h": 1.0,
        }
        self._theme_floating_bg_source_selected_path = floating_source_abs if os.path.isfile(floating_source_abs) else ""
        self._theme_floating_bg_crop_selected = floating_crop_data
        if floating_path_edit is not None:
            floating_path_edit.setText(self._theme_floating_bg_source_selected_path)
        if str(self.cfg.get("theme_bg_preset") or "default").strip().lower() == "image" and not os.path.isfile(source_abs):
            self.settings_theme_notice.setText("当前预设是图片背景，但还没有有效图片文件；界面会回退到默认背景。")
        elif str(self.cfg.get("theme_bg_preset") or "default").strip().lower() == "image":
            self.settings_theme_notice.setText("当前图片背景已配置裁切。可重新选图进行拖动/缩放后裁切。")
        elif str(self.cfg.get("theme_floating_bg_preset") or "follow").strip().lower() == "image" and not os.path.isfile(floating_source_abs):
            self.settings_theme_notice.setText("悬浮窗预设是图片背景，但还没有有效图片文件；当前会跟随主背景。")
        elif str(self.cfg.get("theme_floating_bg_preset") or "follow").strip().lower() == "image":
            self.settings_theme_notice.setText("悬浮窗图片背景已配置裁切。点击“保存主题设置”后生效。")
        else:
            self.settings_theme_notice.setText("修改后点击“保存主题设置”即可立即应用。")

    def _choose_theme_background_image(self):
        current = str(getattr(self, "_theme_bg_source_selected_path", "") or "").strip()
        start_dir = os.path.dirname(current) if current else os.path.expanduser("~")
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择背景图片",
            start_dir,
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;全部文件 (*.*)",
        )
        if not selected:
            return
        resolved = os.path.abspath(selected)
        try:
            dialog = _ThemeCropDialog(resolved, self._theme_target_size(), self)
        except Exception as e:
            QMessageBox.warning(self, "无法加载图片", str(e))
            return
        if dialog.exec() != QDialog.Accepted:
            return
        crop_data = self._normalize_theme_crop_data(dialog.crop_norm()) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self._theme_bg_source_selected_path = resolved
        self._theme_bg_crop_selected = crop_data
        edit = getattr(self, "settings_theme_bg_image_path", None)
        if edit is not None:
            edit.setText(resolved)
        bg_combo = getattr(self, "settings_theme_bg_combo", None)
        if bg_combo is not None:
            idx = bg_combo.findData("image")
            if idx >= 0:
                bg_combo.setCurrentIndex(idx)
        notice = getattr(self, "settings_theme_notice", None)
        if notice is not None:
            notice.setText("已完成裁切，并自动切换到“图片背景”预设；点击“保存主题设置”后生效。")

    def _clear_theme_background_image(self):
        self._theme_bg_source_selected_path = ""
        self._theme_bg_crop_selected = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        edit = getattr(self, "settings_theme_bg_image_path", None)
        if edit is not None:
            edit.clear()
        notice = getattr(self, "settings_theme_notice", None)
        if notice is not None:
            notice.setText("背景图片已清空，点击“保存主题设置”后生效。")

    def _choose_theme_floating_background_image(self):
        current = str(getattr(self, "_theme_floating_bg_source_selected_path", "") or "").strip()
        start_dir = os.path.dirname(current) if current else os.path.expanduser("~")
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择悬浮窗背景图片",
            start_dir,
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.gif);;全部文件 (*.*)",
        )
        if not selected:
            return
        resolved = os.path.abspath(selected)
        try:
            dialog = _ThemeCropDialog(resolved, self._theme_floating_target_size(), self)
        except Exception as e:
            QMessageBox.warning(self, "无法加载图片", str(e))
            return
        if dialog.exec() != QDialog.Accepted:
            return
        crop_data = self._normalize_theme_crop_data(dialog.crop_norm()) or {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self._theme_floating_bg_source_selected_path = resolved
        self._theme_floating_bg_crop_selected = crop_data
        edit = getattr(self, "settings_theme_floating_bg_image_path", None)
        if edit is not None:
            edit.setText(resolved)
        combo = getattr(self, "settings_theme_floating_bg_combo", None)
        if combo is not None:
            idx = combo.findData("image")
            if idx >= 0:
                combo.setCurrentIndex(idx)
        notice = getattr(self, "settings_theme_notice", None)
        if notice is not None:
            notice.setText("已完成悬浮窗背景裁切，并自动切到“图片背景”；点击“保存主题设置”后生效。")

    def _clear_theme_floating_background_image(self):
        self._theme_floating_bg_source_selected_path = ""
        self._theme_floating_bg_crop_selected = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        edit = getattr(self, "settings_theme_floating_bg_image_path", None)
        if edit is not None:
            edit.clear()
        notice = getattr(self, "settings_theme_notice", None)
        if notice is not None:
            notice.setText("悬浮窗背景图片已清空，点击“保存主题设置”后生效。")

    def _save_theme_preferences(self):
        font_combo = getattr(self, "settings_theme_font_combo", None)
        weight_combo = getattr(self, "settings_theme_weight_combo", None)
        size_combo = getattr(self, "settings_theme_size_combo", None)
        bg_combo = getattr(self, "settings_theme_bg_combo", None)
        mode_combo = getattr(self, "settings_theme_bg_mode_combo", None)
        fade_slider = getattr(self, "settings_theme_fade_slider", None)
        floating_bg_combo = getattr(self, "settings_theme_floating_bg_combo", None)
        floating_mode_combo = getattr(self, "settings_theme_floating_bg_mode_combo", None)
        floating_fade_slider = getattr(self, "settings_theme_floating_fade_slider", None)
        font_family = str(font_combo.itemData(font_combo.currentIndex()) or "").strip() if font_combo is not None else ""
        font_weight = str(weight_combo.itemData(weight_combo.currentIndex()) or "400").strip() if weight_combo is not None else "400"
        font_size = str(size_combo.itemData(size_combo.currentIndex()) or "14").strip() if size_combo is not None else "14"
        bg_preset = str(bg_combo.itemData(bg_combo.currentIndex()) or "default").strip() if bg_combo is not None else "default"
        bg_mode = str(mode_combo.itemData(mode_combo.currentIndex()) or "center").strip() if mode_combo is not None else "center"
        floating_bg_preset = str(floating_bg_combo.itemData(floating_bg_combo.currentIndex()) or "follow").strip() if floating_bg_combo is not None else "follow"
        floating_bg_mode = str(floating_mode_combo.itemData(floating_mode_combo.currentIndex()) or "center").strip() if floating_mode_combo is not None else "center"
        fade_value = max(0, min(100, int(fade_slider.value() if fade_slider is not None else self.cfg.get("theme_bg_fade", self.cfg.get("theme_bg_blur", 18)) or 18)))
        floating_fade_value = max(
            0,
            min(
                100,
                int(
                    floating_fade_slider.value()
                    if floating_fade_slider is not None
                    else self.cfg.get(
                        "theme_floating_bg_fade",
                        self.cfg.get("theme_floating_bg_blur", self.cfg.get("theme_bg_fade", self.cfg.get("theme_bg_blur", 18))),
                    )
                    or 18
                ),
            ),
        )
        source_path = str(getattr(self, "_theme_bg_source_selected_path", "") or "").strip()
        if not source_path:
            source_rel_cfg = str(self.cfg.get("theme_bg_source") or self.cfg.get("theme_bg_image") or "").strip()
            source_path = lz._resolve_config_path(source_rel_cfg) if source_rel_cfg else ""
        crop_data = self._normalize_theme_crop_data(getattr(self, "_theme_bg_crop_selected", None))
        if crop_data is None:
            crop_data = self._normalize_theme_crop_data(self.cfg.get("theme_bg_crop"))
        if crop_data is None:
            crop_data = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        floating_source_path = str(getattr(self, "_theme_floating_bg_source_selected_path", "") or "").strip()
        if not floating_source_path:
            floating_source_rel_cfg = str(self.cfg.get("theme_floating_bg_source") or self.cfg.get("theme_floating_bg_image") or "").strip()
            floating_source_path = lz._resolve_config_path(floating_source_rel_cfg) if floating_source_rel_cfg else ""
        floating_crop_data = self._normalize_theme_crop_data(getattr(self, "_theme_floating_bg_crop_selected", None))
        if floating_crop_data is None:
            floating_crop_data = self._normalize_theme_crop_data(self.cfg.get("theme_floating_bg_crop"))
        if floating_crop_data is None:
            floating_crop_data = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}

        generated_abs = ""
        if bg_preset == "image":
            if not source_path or not os.path.isfile(source_path):
                QMessageBox.warning(self, "图片无效", "当前未选择可用的背景图片，请先点“选择图片”完成裁切。")
                return
            try:
                generated_abs = self._render_theme_background_asset(
                    source_path,
                    crop_data,
                    self._theme_target_size(),
                    fade_value,
                    asset_tag="launcher_bg",
                    enforce_launcher_min=True,
                )
            except Exception as e:
                QMessageBox.warning(self, "生成背景失败", str(e))
                return

        floating_generated_abs = ""
        if floating_bg_preset == "image":
            if not floating_source_path or not os.path.isfile(floating_source_path):
                QMessageBox.warning(self, "图片无效", "当前未选择可用的悬浮窗背景图片，请先点“选择图片”完成裁切。")
                return
            try:
                floating_generated_abs = self._render_theme_background_asset(
                    floating_source_path,
                    floating_crop_data,
                    self._theme_floating_target_size(),
                    floating_fade_value,
                    asset_tag="floating_bg",
                    enforce_launcher_min=False,
                )
            except Exception as e:
                QMessageBox.warning(self, "生成悬浮窗背景失败", str(e))
                return

        generated_rel = lz._make_config_relative_path(generated_abs) if generated_abs else str(self.cfg.get("theme_bg_image") or "").strip()
        source_rel = lz._make_config_relative_path(source_path) if source_path else ""
        floating_generated_rel = (
            lz._make_config_relative_path(floating_generated_abs)
            if floating_generated_abs
            else str(self.cfg.get("theme_floating_bg_image") or "").strip()
        )
        floating_source_rel = lz._make_config_relative_path(floating_source_path) if floating_source_path else ""
        self.cfg["theme_font_family"] = font_family
        self.cfg["theme_font_weight"] = font_weight
        self.cfg["theme_font_size"] = font_size
        self.cfg["theme_bg_preset"] = bg_preset
        self.cfg["theme_bg_image"] = generated_rel
        self.cfg["theme_bg_source"] = source_rel
        self.cfg["theme_bg_crop"] = crop_data
        self.cfg["theme_bg_fade"] = fade_value
        self.cfg["theme_bg_blur"] = fade_value
        self.cfg["theme_bg_render_sig"] = ""
        self.cfg["theme_bg_image_mode"] = bg_mode
        self.cfg["theme_floating_bg_preset"] = floating_bg_preset
        self.cfg["theme_floating_bg_image"] = floating_generated_rel
        self.cfg["theme_floating_bg_source"] = floating_source_rel
        self.cfg["theme_floating_bg_crop"] = floating_crop_data
        self.cfg["theme_floating_bg_fade"] = floating_fade_value
        self.cfg["theme_floating_bg_blur"] = floating_fade_value
        self.cfg["theme_floating_bg_render_sig"] = ""
        self.cfg["theme_floating_bg_image_mode"] = floating_bg_mode
        lz.save_config(self.cfg)
        mode = self._normalize_appearance_mode(self.cfg.get("appearance_mode", "light"))
        self._apply_theme(mode)
        self._reload_theme_panel()
        if bg_preset == "image" and not generated_abs:
            self.settings_theme_notice.setText("主题已保存。你选择了图片背景，但还没有图片文件，当前会使用默认背景。")
        elif floating_bg_preset == "image" and not floating_generated_abs:
            self.settings_theme_notice.setText("主题已保存。悬浮窗选择了图片背景，但图片无效，当前会跟随主背景。")
        elif bg_preset == "image":
            self.settings_theme_notice.setText("主题设置已保存并应用。当前显示的是裁切后背景图。")
        elif floating_bg_preset == "image":
            self.settings_theme_notice.setText("主题设置已保存并应用。悬浮窗将使用单独背景图。")
        else:
            self.settings_theme_notice.setText("主题设置已保存并应用。")
        self._set_status("主题设置已保存。")
