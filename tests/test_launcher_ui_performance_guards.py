from __future__ import annotations

import os
import types
import unittest
from unittest import mock

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from launcher_app import window as launcher_window
import qt_chat_parts.chat_view as chat_view_mod
import qt_chat_parts.common as chat_common
from qt_chat_parts.channel_runtime import ChannelRuntimeMixin
from qt_chat_parts.personal_usage import PersonalUsageMixin
from qt_chat_parts.settings_panel import SettingsPanelMixin
from qt_chat_parts.sidebar_sessions import SidebarSessionsMixin


class LauncherUiPerformanceGuardTests(unittest.TestCase):
    def test_stream_updates_are_throttled_and_floating_refresh_moves_to_flush(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("self._stream_flush_timer.start(90)", src)
        self.assertIn("def _flush_stream_render", src)
        self.assertIn('refresher = getattr(self, "_refresh_floating_chat_window", None)', src)

    def test_chat_message_containers_use_fixed_bottom_spacer_instead_of_stretch(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_app", "window.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("self.msg_layout.setAlignment(Qt.AlignTop)", src)
        self.assertIn("layout.setAlignment(Qt.AlignBottom if rows else Qt.AlignTop)", src)
        chat_view_path = os.path.join(root, "qt_chat_parts", "chat_view.py")
        with open(chat_view_path, "r", encoding="utf-8") as f:
            chat_src = f.read()
        self.assertIn("def _sync_message_layout_alignment(self):", chat_src)
        self.assertIn("layout.setAlignment(Qt.AlignBottom if rows else Qt.AlignTop)", chat_src)
        self.assertIn(
            "self.msg_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Fixed))",
            src,
        )
        self.assertNotIn("self.msg_layout.addStretch(1)", src)

    def test_message_row_action_row_stays_collapsed_when_not_hovered_or_live(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "common.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)", src)
        self.assertIn("self._action_row.hide()", src)
        self.assertIn("self._action_row_hovered = False", src)
        self.assertIn("self._action_row_live = False", src)
        self.assertIn(
            "if bool(getattr(self, \"_action_row_hovered\", False)) or bool(getattr(self, \"_action_row_live\", False)):",
            src,
        )
        self.assertIn("self._sync_action_row_visibility()", src)

    def test_assistant_browsers_strip_default_document_margin_and_frame(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "common.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("browser.setFrameShape(QFrame.NoFrame)", src)
        self.assertIn("browser.document().setDocumentMargin(0)", src)
        self.assertIn("self._body.setFrameShape(QFrame.NoFrame)", src)
        self.assertIn("self._body.document().setDocumentMargin(0)", src)

    def test_single_line_assistant_message_keeps_action_row_close_to_text(self):
        app = QApplication.instance() or QApplication([])
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        row = chat_common.MessageRow("短回复", "assistant", host, on_resend=lambda _row: None)
        layout.addWidget(row)
        host.resize(480, 220)
        host.show()

        row._action_row_hovered = True
        row._copy_btn.show()
        if row._regen_btn is not None:
            row._regen_btn.show()
        row._sync_action_row_visibility()
        row._refit_finished_assistant_browsers()
        app.processEvents()
        app.processEvents()

        browser = next(row._iter_active_assistant_browsers())
        browser_origin = browser.mapTo(row, QPoint(0, 0))
        action_origin = row._action_row.mapTo(row, QPoint(0, 0))
        gap = int(action_origin.y() - (browser_origin.y() + browser.height()))

        self.assertLessEqual(browser.height(), 24)
        self.assertLessEqual(gap, 12)

        host.close()
        host.deleteLater()
        app.processEvents()

    def test_finished_message_row_refits_browser_and_clears_streaming_hold(self):
        class DummyDoc:
            def __init__(self, height):
                self.height = height
                self.width = None

            def setTextWidth(self, width):
                self.width = width

            def size(self):
                return types.SimpleNamespace(height=lambda: self.height)

        class DummyBar:
            def isVisible(self):
                return False

        class DummyBrowser:
            def __init__(self):
                self.doc = DummyDoc(28)
                self.current_height = 120
                self.props = {
                    "streamingHold": True,
                    "_fitForce": False,
                    "_fitHeight": 120,
                    "_fitWidth": 560,
                }
                self.fixed_heights = []

            def document(self):
                return self.doc

            def viewport(self):
                return types.SimpleNamespace(width=lambda: 560)

            def property(self, key):
                return self.props.get(key)

            def setProperty(self, key, value):
                self.props[key] = value

            def height(self):
                return self.current_height

            def horizontalScrollBar(self):
                return DummyBar()

            def frameShape(self):
                return 0

            def frameWidth(self):
                return 0

            def setFixedHeight(self, value):
                self.fixed_heights.append(int(value))
                self.current_height = int(value)

        browser = DummyBrowser()
        chat_common._refit_browser_for_state(browser, streaming=False)

        self.assertFalse(browser.property("streamingHold"))
        self.assertEqual(browser.fixed_heights, [30])
        self.assertEqual(browser.property("_fitHeight"), 30)
        self.assertFalse(browser.property("_fitForce"))

    def test_finished_message_row_schedules_refit_after_rebuild(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "common.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _schedule_finished_assistant_refit(self):", src)
        self.assertIn("QTimer.singleShot(0, self._refit_finished_assistant_browsers)", src)
        self.assertIn("self._refit_finished_assistant_browsers()", src)

    def test_empty_live_assistant_row_first_chunk_does_not_lock_host_height(self):
        app = QApplication.instance() or QApplication([])
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        row = chat_common.MessageRow("", "assistant", host, on_resend=lambda _row: None)
        layout.addWidget(row)
        host.resize(520, 480)
        host.show()
        app.processEvents()

        row.set_finished(False)
        app.processEvents()
        row.update_content("hello world", finished=False)
        app.processEvents()
        app.processEvents()

        browser = row._stream_live_browser
        self.assertIsNotNone(browser)
        self.assertTrue(browser.property("streamingHold"))
        self.assertLessEqual(browser.height(), 40)
        self.assertLessEqual(int(browser.property("_fitHeight") or 0), 40)
        self.assertNotEqual(browser.height(), host.height())

        row.update_content("hello world", finished=True)
        app.processEvents()
        app.processEvents()

        browser = next(row._iter_active_assistant_browsers())
        self.assertFalse(browser.property("streamingHold"))
        self.assertLessEqual(browser.height(), 40)

        host.close()
        host.deleteLater()
        app.processEvents()

    def test_hidden_floating_window_skips_full_sync_when_not_visible(self):
        class DummyFloating:
            def __init__(self):
                self.refreshed = 0
                self.sync_payload = None

            def refresh_action_texts(self):
                self.refreshed += 1

            def isVisible(self):
                return False

            def sync_view(self, **kwargs):
                self.sync_payload = dict(kwargs)

        class DummyHost:
            _refresh_floating_chat_window = launcher_window.QtChatWindow._refresh_floating_chat_window
            _floating_default_status_text = launcher_window.QtChatWindow._floating_default_status_text

            def __init__(self):
                self._floating_chat_window = DummyFloating()
                self._tray_mode_active = False
                self.status_label = types.SimpleNamespace(text=lambda: "")
                self.current_session = {}
                self._busy = False
                self._abort_requested = False
                self.calls = []

            def isVisible(self):
                return True

            def _is_channel_process_session(self, session=None):
                return False

            def _floating_chat_title(self):
                return "title"

            def _floating_chat_subtitle(self):
                return "subtitle"

            def _floating_chat_meta(self):
                return "meta"

            def _sync_floating_llm_combo(self):
                self.calls.append("sync_llm")

            def _sync_floating_session_list(self):
                self.calls.append("sync_sessions")

            def _sync_draft_to_floating(self, *, force=False):
                self.calls.append(("sync_draft", bool(force)))

            def _refresh_launcher_tray_menu(self):
                self.calls.append("refresh_tray")

        dummy = DummyHost()
        launcher_window.QtChatWindow._refresh_floating_chat_window(dummy)

        self.assertEqual(dummy._floating_chat_window.refreshed, 1)
        self.assertIsNone(dummy._floating_chat_window.sync_payload)
        self.assertEqual(dummy.calls, ["refresh_tray"])

    def test_busy_floating_stream_refresh_skips_side_sync_for_orb_window(self):
        class DummyFloating:
            def __init__(self):
                self.refreshed = 0
                self.sync_payload = None

            def refresh_action_texts(self):
                self.refreshed += 1

            def isVisible(self):
                return True

            def sync_view(self, **kwargs):
                self.sync_payload = dict(kwargs)

        class DummyHost:
            _refresh_floating_chat_window = launcher_window.QtChatWindow._refresh_floating_chat_window
            _floating_default_status_text = launcher_window.QtChatWindow._floating_default_status_text

            def __init__(self):
                self._floating_chat_window = DummyFloating()
                self._tray_mode_active = False
                self.status_label = types.SimpleNamespace(text=lambda: "streaming")
                self.current_session = {"bubbles": [{"role": "assistant", "text": "done"}]}
                self._pending_stream_text = "partial"
                self._current_stream_text = ""
                self._busy = True
                self._abort_requested = False
                self.calls = []

            def isVisible(self):
                return False

            def _is_channel_process_session(self, session=None):
                return False

            def _floating_chat_title(self):
                return "title"

            def _floating_chat_subtitle(self):
                return "subtitle"

            def _floating_chat_meta(self):
                return "meta"

            def _sync_floating_llm_combo(self):
                self.calls.append("sync_llm")

            def _sync_floating_session_list(self):
                self.calls.append("sync_sessions")

            def _sync_draft_to_floating(self, *, force=False):
                self.calls.append(("sync_draft", bool(force)))

            def _refresh_launcher_tray_menu(self):
                self.calls.append("refresh_tray")

        dummy = DummyHost()
        with mock.patch.object(launcher_window, "FloatingOrbWindow", DummyFloating):
            launcher_window.QtChatWindow._refresh_floating_chat_window(dummy)

        self.assertEqual(dummy._floating_chat_window.refreshed, 1)
        self.assertEqual(dummy._floating_chat_window.sync_payload["stream_text"], "partial")
        self.assertEqual(dummy.calls, ["sync_llm", "refresh_tray"])

    def test_scroll_row_to_top_can_preserve_stream_auto_follow_state(self):
        class DummyBar:
            def __init__(self):
                self.value_set = None

            def maximum(self):
                return 400

            def setValue(self, value):
                self.value_set = int(value)

        class DummyScroll:
            def __init__(self, bar):
                self._bar = bar

            def verticalScrollBar(self):
                return self._bar

        class DummyRow:
            def y(self):
                return 180

        class DummyView:
            _scroll_row_to_top = chat_view_mod.ChatViewMixin._scroll_row_to_top

            def __init__(self):
                self.scroll = DummyScroll(DummyBar())
                self._user_scrolled_up = True
                self.jump_refreshes = 0

            def _refresh_jump_latest_button(self):
                self.jump_refreshes += 1

        dummy = DummyView()
        with mock.patch.object(chat_view_mod.QTimer, "singleShot", side_effect=lambda _ms, fn: fn()):
            dummy._scroll_row_to_top(DummyRow(), preserve_scroll_state=True)

        self.assertFalse(dummy._user_scrolled_up)
        self.assertEqual(dummy.scroll.verticalScrollBar().value_set, 162)
        self.assertEqual(dummy.jump_refreshes, 1)

    def test_scroll_row_to_top_retries_when_nonfirst_row_geometry_is_not_ready(self):
        class DummyBar:
            def __init__(self):
                self.value_set = None

            def maximum(self):
                return 400

            def setValue(self, value):
                self.value_set = int(value)

        class DummyScroll:
            def __init__(self, bar):
                self._bar = bar

            def verticalScrollBar(self):
                return self._bar

        class DummyLayout:
            def activate(self):
                return True

        class DummyRoot:
            def updateGeometry(self):
                return None

        class DummyRow:
            def __init__(self):
                self.calls = 0

            def y(self):
                self.calls += 1
                return 0 if self.calls == 1 else 240

        class DummyView:
            _scroll_row_to_top = chat_view_mod.ChatViewMixin._scroll_row_to_top

            def __init__(self, row):
                self.scroll = DummyScroll(DummyBar())
                self.msg_layout = DummyLayout()
                self.msg_root = DummyRoot()
                self._user_scrolled_up = False
                self._rendered_message_rows = [object(), row]
                self.jump_refreshes = 0

            def _refresh_jump_latest_button(self):
                self.jump_refreshes += 1

        row = DummyRow()
        dummy = DummyView(row)
        timer_calls = []

        def run_timer(_ms, fn):
            timer_calls.append(True)
            fn()

        with mock.patch.object(chat_view_mod.QTimer, "singleShot", side_effect=run_timer):
            dummy._scroll_row_to_top(row, preserve_scroll_state=True)

        self.assertEqual(timer_calls, [True, True])
        self.assertEqual(dummy.scroll.verticalScrollBar().value_set, 222)
        self.assertEqual(dummy.jump_refreshes, 2)

    def test_sync_current_turn_view_prefers_tracked_current_turn_user_row(self):
        class DummyRow:
            def __init__(self, label):
                self.label = str(label)

        class DummyView:
            _sync_current_turn_view = chat_view_mod.ChatViewMixin._sync_current_turn_view
            _tracked_current_turn_user_row = chat_view_mod.ChatViewMixin._tracked_current_turn_user_row
            _latest_user_row = chat_view_mod.ChatViewMixin._latest_user_row

            def __init__(self):
                self._follow_latest_user_message = True
                self._current_turn_user_row = DummyRow("current")
                self._rendered_message_rows = [DummyRow("history"), self._current_turn_user_row]
                self.scroll_calls = []
                self.bottom_calls = 0

            def _scroll_row_to_top(self, row, preserve_scroll_state=False):
                self.scroll_calls.append((row.label, bool(preserve_scroll_state)))

            def _set_follow_latest_user(self, enabled):
                self._follow_latest_user_message = bool(enabled)

            def _scroll_to_bottom(self, force=False):
                self.bottom_calls += 1

        dummy = DummyView()
        dummy._sync_current_turn_view()

        self.assertEqual(dummy.scroll_calls, [("current", True)])
        self.assertEqual(dummy.bottom_calls, 0)
        self.assertFalse(dummy._follow_latest_user_message)

    def test_chat_view_tail_spacer_survives_clear_and_keeps_insert_slot(self):
        class DummyWidget:
            def __init__(self, label):
                self.label = str(label)
                self.deleted = 0

            def deleteLater(self):
                self.deleted += 1

        class DummyLayoutItem:
            def __init__(self, widget=None, spacer=False):
                self._widget = widget
                self._spacer = object() if spacer else None

            def widget(self):
                return self._widget

            def spacerItem(self):
                return self._spacer

        class DummyLayout:
            def __init__(self):
                self.items = [DummyLayoutItem(DummyWidget("row")), DummyLayoutItem(spacer=True)]
                self.alignment = None

            def count(self):
                return len(self.items)

            def itemAt(self, index):
                return self.items[int(index)]

            def takeAt(self, index):
                return self.items.pop(int(index))

            def insertWidget(self, index, widget):
                self.items.insert(int(index), DummyLayoutItem(widget))

            def setAlignment(self, alignment):
                self.alignment = alignment

        class DummyView:
            _message_row_insert_index = chat_view_mod.ChatViewMixin._message_row_insert_index
            _clear_messages = chat_view_mod.ChatViewMixin._clear_messages
            _sync_message_layout_alignment = chat_view_mod.ChatViewMixin._sync_message_layout_alignment

            def __init__(self):
                self.msg_layout = DummyLayout()
                self.msg_root = object()
                self._stream_row = object()
                self._current_stream_text = "live"
                self._pending_stream_text = "pending"
                self._rendered_message_rows = [object()]
                self.jump_refreshes = 0
                self.floating_refreshes = 0

            def _refresh_jump_latest_button(self):
                self.jump_refreshes += 1

            def _refresh_floating_chat_window(self):
                self.floating_refreshes += 1

        dummy = DummyView()
        row_widget = dummy.msg_layout.itemAt(0).widget()
        dummy._clear_messages()

        self.assertEqual(row_widget.deleted, 1)
        self.assertEqual(dummy.msg_layout.count(), 1)
        self.assertIsNotNone(dummy.msg_layout.itemAt(0).spacerItem())
        self.assertEqual(dummy._message_row_insert_index(), 0)
        self.assertEqual(dummy.jump_refreshes, 1)
        self.assertEqual(dummy.floating_refreshes, 1)

    def test_floating_orb_stream_updates_reuse_live_row_when_bubbles_do_not_change(self):
        class DummyLayoutItem:
            def __init__(self, widget):
                self._widget = widget

            def widget(self):
                return self._widget

        class DummyLayout:
            def __init__(self):
                self.widgets = []
                self.alignment = None

            def count(self):
                return len(self.widgets) + 1

            def insertWidget(self, index, widget):
                idx = max(0, min(int(index), len(self.widgets)))
                self.widgets.insert(idx, widget)

            def takeAt(self, index):
                if 0 <= int(index) < len(self.widgets):
                    return DummyLayoutItem(self.widgets.pop(int(index)))
                return DummyLayoutItem(None)

            def indexOf(self, widget):
                try:
                    return self.widgets.index(widget)
                except ValueError:
                    return -1

            def setAlignment(self, alignment):
                self.alignment = alignment

        class DummyRow:
            instances = []

            def __init__(self, text, role, _parent, on_resend=None):
                self._text = str(text)
                self._role = str(role)
                self._finished = True
                self._on_resend = on_resend
                self.updates = []
                DummyRow.instances.append(self)

            def set_finished(self, value):
                self._finished = bool(value)

            def update_content(self, text, *, finished):
                self._text = str(text)
                self._finished = bool(finished)
                self.updates.append((self._text, self._finished))

            def parent(self):
                return object()

            def deleteLater(self):
                return None

        class DummyHost:
            def __init__(self):
                self._busy = True
                self._follow_latest_user_message = False

        class DummyOrb:
            _render_rows = launcher_window.FloatingOrbWindow._render_rows
            _sync_stream_row = launcher_window.FloatingOrbWindow._sync_stream_row
            _clear_stream_row = launcher_window.FloatingOrbWindow._clear_stream_row
            _sync_message_layout_alignment = launcher_window.FloatingOrbWindow._sync_message_layout_alignment

            def __init__(self):
                self._host = DummyHost()
                self._last_signature = None
                self._last_bubble_signature = None
                self._rendered_rows = []
                self._stream_row = None
                self._focus_latest_user_after_refresh = False
                self.msg_layout = DummyLayout()
                self.msg_root = object()
                self.clear_calls = 0
                self.scroll_calls = []

            def _clear_rows(self):
                self.clear_calls += 1
                self._stream_row = None
                self._last_bubble_signature = None
                self._rendered_rows = []
                self.msg_layout.widgets = []

            def _scroll_to_latest_dialogue(self):
                self.scroll_calls.append("latest")

            def _scroll_to_bottom(self):
                self.scroll_calls.append("bottom")

        dummy = DummyOrb()
        bubbles = [{"role": "user", "text": "hello"}]
        with mock.patch.object(launcher_window, "MessageRow", DummyRow), mock.patch.object(
            launcher_window.QTimer, "singleShot", side_effect=lambda _ms, fn: fn()
        ):
            dummy._render_rows(bubbles, "part 1")
            first_user_row = dummy._rendered_rows[0]
            first_stream_row = dummy._stream_row
            dummy._render_rows(bubbles, "part 2")

        self.assertEqual(dummy.clear_calls, 1)
        self.assertIs(dummy._rendered_rows[0], first_user_row)
        self.assertIs(dummy._stream_row, first_stream_row)
        self.assertEqual(len(DummyRow.instances), 2)
        self.assertEqual(first_stream_row.updates, [("part 1", False), ("part 2", False)])
        self.assertEqual(dummy.scroll_calls, ["bottom", "bottom"])

    def test_streaming_browser_height_skips_sub_line_growth_jitter(self):
        class DummyDoc:
            def __init__(self, height):
                self.height = height
                self.width = None

            def setTextWidth(self, width):
                self.width = width

            def size(self):
                return types.SimpleNamespace(height=lambda: self.height)

        class DummyBar:
            def isVisible(self):
                return False

        class DummyBrowser:
            def __init__(self, *, doc_height, height, fit_height):
                self.doc = DummyDoc(doc_height)
                self.current_height = height
                self.props = {
                    "streamingHold": True,
                    "_fitForce": True,
                    "_fitHeight": fit_height,
                    "_fitWidth": 560,
                }
                self.fixed_heights = []

            def document(self):
                return self.doc

            def viewport(self):
                return types.SimpleNamespace(width=lambda: 560)

            def property(self, key):
                return self.props.get(key)

            def setProperty(self, key, value):
                self.props[key] = value

            def height(self):
                return self.current_height

            def horizontalScrollBar(self):
                return DummyBar()

            def frameShape(self):
                return 0

            def frameWidth(self):
                return 0

            def setFixedHeight(self, value):
                self.fixed_heights.append(int(value))
                self.current_height = int(value)

        small_growth = DummyBrowser(doc_height=94, height=100, fit_height=100)
        chat_common._fit_browser_height(small_growth)

        self.assertEqual(small_growth.fixed_heights, [])
        self.assertFalse(small_growth.property("_fitForce"))
        self.assertEqual(small_growth.property("_fitHeight"), 100)

        line_growth = DummyBrowser(doc_height=110, height=100, fit_height=100)
        chat_common._fit_browser_height(line_growth)

        self.assertEqual(line_growth.fixed_heights, [112])
        self.assertEqual(line_growth.property("_fitHeight"), 112)

    def test_cached_local_settings_category_switch_skips_redundant_reload(self):
        class DummyStack:
            def __init__(self):
                self.current = None
                self.set_calls = 0

            def setCurrentWidget(self, widget):
                self.set_calls += 1
                self.current = widget

            def currentWidget(self):
                return self.current

        class DummyButton:
            def setStyleSheet(self, _style):
                return None

        class DummySettings(SettingsPanelMixin):
            _show_settings_category = SettingsPanelMixin._show_settings_category
            _settings_should_reload_on_switch = SettingsPanelMixin._settings_should_reload_on_switch
            _settings_category_should_force_live_reload = SettingsPanelMixin._settings_category_should_force_live_reload

            def __init__(self):
                self.settings_stack = DummyStack()
                self._settings_pages = {"theme": {"widget": object()}}
                self._settings_nav_buttons = {"theme": DummyButton()}
                self._settings_loaded_categories = {"theme"}
                self.calls = []

            def _refresh_settings_target_visibility(self, key):
                self.calls.append(("visibility", key))

            def _sidebar_button_style(self, *, selected=False, subtle=False):
                return "selected" if selected else "subtle" if subtle else "default"

            def _settings_reload(self, *, categories=None, force=False):
                self.calls.append(("reload", list(categories or []), bool(force)))

            def _refresh_settings_status_label(self, key=None):
                self.calls.append(("status", key))

        dummy = DummySettings()
        with mock.patch("qt_chat_parts.settings_panel.QTimer.singleShot", side_effect=lambda *_args: _args[-1]()):
            dummy._show_settings_category("theme", reload=True)

        self.assertEqual(dummy.settings_stack.current, dummy._settings_pages["theme"]["widget"])
        self.assertIn(("visibility", "theme"), dummy.calls)
        self.assertIn(("status", "theme"), dummy.calls)
        self.assertNotIn(("reload", ["theme"], False), dummy.calls)
        self.assertEqual(dummy.settings_stack.set_calls, 1)

    def test_settings_category_activation_waits_briefly_and_skips_redundant_stack_switch(self):
        class DummyStack:
            def __init__(self, widget):
                self.current = widget
                self.set_calls = 0

            def setCurrentWidget(self, widget):
                self.set_calls += 1
                self.current = widget

            def currentWidget(self):
                return self.current

        class DummyButton:
            def setStyleSheet(self, _style):
                return None

        class DummySettings(SettingsPanelMixin):
            _show_settings_category = SettingsPanelMixin._show_settings_category
            _settings_should_reload_on_switch = SettingsPanelMixin._settings_should_reload_on_switch
            _settings_category_should_force_live_reload = SettingsPanelMixin._settings_category_should_force_live_reload

            def __init__(self):
                widget = object()
                self.settings_stack = DummyStack(widget)
                self._settings_pages = {"theme": {"widget": widget}}
                self._settings_nav_buttons = {"theme": DummyButton()}
                self._settings_loaded_categories = {"theme"}
                self.calls = []

            def _refresh_settings_target_visibility(self, key):
                self.calls.append(("visibility", key))

            def _sidebar_button_style(self, *, selected=False, subtle=False):
                return "selected" if selected else "subtle" if subtle else "default"

            def _settings_reload(self, *, categories=None, force=False):
                self.calls.append(("reload", list(categories or []), bool(force)))

            def _refresh_settings_status_label(self, key=None):
                self.calls.append(("status", key))

        dummy = DummySettings()
        delays = []

        def immediate_single_shot(delay, *args):
            delays.append(int(delay))
            args[-1]()

        with mock.patch("qt_chat_parts.settings_panel.QTimer.singleShot", side_effect=immediate_single_shot):
            dummy._show_settings_category("theme", reload=True)

        self.assertEqual(dummy.settings_stack.set_calls, 0)
        self.assertEqual(delays, [dummy._SETTINGS_SWITCH_RELOAD_DELAY_MS])
        self.assertIn(("status", "theme"), dummy.calls)

    def test_local_only_settings_reload_skips_target_combo_refresh(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, value):
                self.text = str(value or "")

        class DummySettings(SettingsPanelMixin):
            _settings_reload = SettingsPanelMixin._settings_reload
            _refresh_settings_status_label = SettingsPanelMixin._refresh_settings_status_label
            _settings_category_refreshes_target_combo = SettingsPanelMixin._settings_category_refreshes_target_combo
            _settings_category_allows_live_reload = SettingsPanelMixin._settings_category_allows_live_reload
            _settings_category_should_force_live_reload = SettingsPanelMixin._settings_category_should_force_live_reload
            _settings_category_scope_mode = SettingsPanelMixin._settings_category_scope_mode
            _settings_category_uses_target_switch = SettingsPanelMixin._settings_category_uses_target_switch

            def __init__(self):
                self.cfg = {}
                self.agent_dir = ""
                self._current_settings_category = "theme"
                self._settings_loaded_categories = set()
                self.settings_status_label = DummyLabel()
                self.calls = []

            def _normalize_settings_target(self, raw):
                data = dict(raw or {})
                scope = str(data.get("scope") or "local").strip().lower()
                if scope != "remote":
                    scope = "local"
                device_id = str(data.get("device_id") or "local").strip() or "local"
                if scope == "local":
                    device_id = "local"
                return {"scope": scope, "device_id": device_id}

            def _settings_target_context(self):
                return {"is_remote": False, "label": "本机目录"}

            def _refresh_settings_target_combo(self, *, force=False):
                self.calls.append(("refresh_target_combo", bool(force)))

            def _reload_api_editor_state(self):
                self.calls.append("reload_api")

            def _reload_channels_editor_state(self):
                self.calls.append("reload_channels")

            def _reload_vps_panel(self):
                self.calls.append("reload_vps")

            def _reload_schedule_panel(self):
                self.calls.append("reload_schedule")

            def _reload_personal_panel(self):
                self.calls.append("reload_personal")

            def _reload_theme_panel(self):
                self.calls.append("reload_theme")

            def _reload_usage_panel(self):
                self.calls.append("reload_usage")

            def _reload_about_panel(self):
                self.calls.append("reload_about")

        dummy = DummySettings()
        dummy._settings_reload(categories=["theme"], force=False)

        self.assertNotIn(("refresh_target_combo", False), dummy.calls)
        self.assertIn("reload_theme", dummy.calls)
        self.assertEqual(dummy.settings_status_label.text, "当前页为启动器本机设置，不需要切换目标设备。")

    def test_local_usage_page_does_not_force_live_reload_on_switch(self):
        class DummySettings(SettingsPanelMixin):
            _settings_category_allows_live_reload = SettingsPanelMixin._settings_category_allows_live_reload
            _settings_category_should_force_live_reload = SettingsPanelMixin._settings_category_should_force_live_reload

            def __init__(self):
                self._settings_loaded_categories = {"usage"}
                self._settings_live_reload_stamps = {"usage": 0.0}

            def _settings_data_target_context(self):
                return {"is_remote": False, "scope": "local", "device_id": "local", "label": "本机"}

        dummy = DummySettings()
        self.assertFalse(dummy._settings_category_should_force_live_reload("usage"))

    def test_cached_channels_switch_still_refreshes_visible_runtime_status(self):
        class DummyStack:
            def __init__(self):
                self.current = None

            def setCurrentWidget(self, widget):
                self.current = widget

        class DummyButton:
            def setStyleSheet(self, _style):
                return None

        class DummySettings(SettingsPanelMixin):
            _show_settings_category = SettingsPanelMixin._show_settings_category
            _settings_should_reload_on_switch = SettingsPanelMixin._settings_should_reload_on_switch
            _settings_category_should_force_live_reload = SettingsPanelMixin._settings_category_should_force_live_reload

            def __init__(self):
                self.settings_stack = DummyStack()
                widget = object()
                self._settings_pages = {"channels": {"widget": widget}}
                self._settings_nav_buttons = {"channels": DummyButton()}
                self._settings_loaded_categories = {"channels"}
                self.calls = []

            def _refresh_settings_target_visibility(self, key):
                self.calls.append(("visibility", key))

            def _sidebar_button_style(self, *, selected=False, subtle=False):
                return "selected" if selected else "subtle" if subtle else "default"

            def _settings_reload(self, *, categories=None, force=False):
                self.calls.append(("reload", list(categories or []), bool(force)))

            def _refresh_settings_status_label(self, key=None):
                self.calls.append(("status", key))

            def _refresh_channels_runtime_status_labels(self, *, force=False):
                self.calls.append(("channels_runtime", bool(force)))

        dummy = DummySettings()
        with mock.patch("qt_chat_parts.settings_panel.QTimer.singleShot", side_effect=lambda *_args: _args[-1]()):
            dummy._show_settings_category("channels", reload=True)

        self.assertEqual(dummy.settings_stack.current, dummy._settings_pages["channels"]["widget"])
        self.assertIn(("status", "channels"), dummy.calls)
        self.assertIn(("channels_runtime", True), dummy.calls)
        self.assertNotIn(("reload", ["channels"], False), dummy.calls)

    def test_local_usage_reload_is_deferred_to_background_worker(self):
        class DummyLabel:
            def __init__(self, text=""):
                self.text = str(text or "")
                self.word_wrap = False
                self.object_name = ""

            def setText(self, value):
                self.text = str(value or "")

            def setWordWrap(self, enabled):
                self.word_wrap = bool(enabled)

            def setObjectName(self, name):
                self.object_name = str(name or "")

        class DummyLayout:
            def __init__(self):
                self.items = []

            def addWidget(self, widget):
                self.items.append(widget)

        class DummyUsage(PersonalUsageMixin):
            _reload_usage_panel = PersonalUsageMixin._reload_usage_panel

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.settings_usage_notice = DummyLabel()
                self.settings_usage_list_layout = DummyLayout()
                self.calls = []

            def _clear_layout(self, layout):
                self.calls.append("clear")
                layout.items.clear()

            def _settings_data_target_context(self):
                return {"label": "本机", "scope": "local", "device_id": "local", "is_remote": False}

            def _collect_usage_stats(self, **_kwargs):
                self.calls.append("collect")
                return {"today": {}, "recent": {}, "all": {}, "activity": {}}

            def _load_langfuse_status(self):
                self.calls.append("langfuse")
                return {}

            def _render_usage_panel_content(self, stats, target, langfuse):
                self.calls.append(("render", stats, target, langfuse))

        created = []
        started = []

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=None):
                created.append((target, name, daemon))
                self._target = target

            def start(self):
                started.append(self._target)

        dummy = DummyUsage()
        with mock.patch("qt_chat_parts.personal_usage.QLabel", DummyLabel), \
             mock.patch("qt_chat_parts.personal_usage.capture_runtime_context", return_value={"token": 1}), \
             mock.patch("qt_chat_parts.personal_usage.runtime_context_matches", return_value=True), \
             mock.patch("qt_chat_parts.personal_usage.lz.is_valid_agent_dir", return_value=True), \
             mock.patch("qt_chat_parts.personal_usage.threading.Thread", FakeThread):
            dummy._reload_usage_panel()

        self.assertIn("正在整理 本机 的使用日志", dummy.settings_usage_notice.text)
        self.assertEqual(dummy.calls, ["clear"])
        self.assertEqual(len(dummy.settings_usage_list_layout.items), 1)
        self.assertEqual(len(created), 1)
        self.assertEqual(len(started), 1)

    def test_usage_panel_render_failure_shows_fallback_message(self):
        class DummyLabel:
            def __init__(self, text=""):
                self.text = str(text or "")
                self.word_wrap = False
                self.object_name = ""

            def setText(self, value):
                self.text = str(value or "")

            def setWordWrap(self, enabled):
                self.word_wrap = bool(enabled)

            def setObjectName(self, name):
                self.object_name = str(name or "")

        class DummyLayout:
            def __init__(self):
                self.items = []

            def addWidget(self, widget):
                self.items.append(widget)

        class DummyUsage(PersonalUsageMixin):
            _reload_usage_panel = PersonalUsageMixin._reload_usage_panel

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.settings_usage_notice = DummyLabel()
                self.settings_usage_list_layout = DummyLayout()
                self.calls = []
                self.statuses = []
                self._api_on_ui_thread = lambda fn: fn()

            def _clear_layout(self, layout):
                self.calls.append("clear")
                layout.items.clear()

            def _settings_data_target_context(self):
                return {"label": "本机", "scope": "local", "device_id": "local", "is_remote": False}

            def _collect_usage_stats(self, **_kwargs):
                self.calls.append("collect")
                return {"today": {}, "recent": {}, "all": {}, "activity": {}}

            def _load_langfuse_status(self):
                self.calls.append("langfuse")
                return {}

            def _render_usage_panel_content(self, stats, target, langfuse):
                self.calls.append(("render", stats, target, langfuse))
                raise RuntimeError("boom")

            def _set_status(self, text):
                self.statuses.append(str(text))

        class FakeThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        dummy = DummyUsage()
        with mock.patch("qt_chat_parts.personal_usage.QLabel", DummyLabel), \
             mock.patch("qt_chat_parts.personal_usage.capture_runtime_context", return_value={"token": 1}), \
             mock.patch("qt_chat_parts.personal_usage.runtime_context_matches", return_value=True), \
             mock.patch("qt_chat_parts.personal_usage.lz.is_valid_agent_dir", return_value=True), \
             mock.patch("qt_chat_parts.personal_usage.threading.Thread", FakeThread):
            dummy._reload_usage_panel()

        self.assertIn("使用日志渲染失败", dummy.settings_usage_notice.text)
        self.assertEqual(len(dummy.settings_usage_list_layout.items), 1)
        self.assertEqual(dummy.settings_usage_list_layout.items[0].text, "当前使用日志面板渲染失败，请稍后重试。若问题持续出现，请反馈这个错误。")
        self.assertIn("使用日志渲染失败：boom", dummy.statuses[-1])

    def test_hidden_channels_status_refresh_skips_background_widget_updates(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""
                self.style = ""
                self.visible = None

            def setText(self, text):
                self.text = str(text)

            def setStyleSheet(self, style):
                self.style = str(style)

            def setVisible(self, visible):
                self.visible = bool(visible)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyChannel(ChannelRuntimeMixin):
            _refresh_channels_runtime_status_labels = ChannelRuntimeMixin._refresh_channels_runtime_status_labels

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_extras = {}
                self._qt_channel_states = {
                    "wechat": {
                        "status_label": DummyLabel(),
                        "status_hint_label": DummyLabel(),
                        "start_btn": DummyButton(),
                        "stop_btn": DummyButton(),
                    }
                }
                self.calls = []

            def _settings_category_is_visible(self, key):
                self.calls.append(("visible_check", key))
                return False

            def _settings_target_context(self):
                self.calls.append("target_ctx")
                return {"is_remote": False}

            def _refresh_channel_source_actions(self):
                self.calls.append("refresh_actions")

            def _refresh_local_channel_external_running(self, *, persist=False):
                self.calls.append("refresh_local")
                return {}

            def _channel_status(self, channel_id, values, *, target_ctx=None):
                self.calls.append(("channel_status", channel_id))
                return ("运行中", "#22c55e")

            def _channel_proc_alive(self, _channel_id):
                return False

            def _channel_external_running(self, _channel_id):
                return False

            def _channel_conflict_message(self, _channel_id):
                return ""

            def _channel_missing_required(self, _channel_id, _values):
                return []

        dummy = DummyChannel()
        with mock.patch("qt_chat_parts.channel_runtime.lz.COMM_CHANNEL_SPECS", [{"id": "wechat", "fields": []}]):
            dummy._refresh_channels_runtime_status_labels()

        self.assertEqual(dummy.calls, [("visible_check", "channels")])
        self.assertEqual(dummy._qt_channel_states["wechat"]["status_label"].text, "")

    def test_channel_runtime_uses_async_local_probe_path_for_channels_status_refresh(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "channel_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _request_local_channel_external_running_refresh", src)
        self.assertIn('action_name="本地渠道状态刷新"', src)

    def test_queued_session_refresh_coalesces_background_rebuilds(self):
        class DummySidebar(SidebarSessionsMixin):
            _queue_session_refresh = SidebarSessionsMixin._queue_session_refresh

            def __init__(self):
                self._session_refresh_queued = False
                self._closing_in_progress = False
                self._last_session_list_signature = "sig"
                self.calls = []

            def _refresh_sessions(self):
                self.calls.append("refresh")

        dummy = DummySidebar()
        scheduled = []

        def fake_single_shot(delay, *args):
            callback = args[-1]
            scheduled.append((int(delay), callback))

        with mock.patch("qt_chat_parts.sidebar_sessions.QTimer.singleShot", side_effect=fake_single_shot):
            dummy._queue_session_refresh(delay_ms=45)
            dummy._queue_session_refresh(delay_ms=45)

        self.assertEqual(len(scheduled), 1)
        self.assertIsNone(dummy._last_session_list_signature)
        self.assertTrue(dummy._session_refresh_queued)

        scheduled[0][1]()

        self.assertEqual(dummy.calls, ["refresh"])
        self.assertFalse(dummy._session_refresh_queued)

    def test_orb_release_only_persists_after_real_drag(self):
        class DummyOrb:
            _complete_orb_drag_release = launcher_window.FloatingOrbWindow._complete_orb_drag_release

            def __init__(self, moved):
                self.calls = []
                self._drag_moved = bool(moved)
                self._orb_pressed = True
                self._host = types.SimpleNamespace(
                    _save_floating_orb_position=lambda pos: self.calls.append(("save", pos.x(), pos.y()))
                )

            def _snap_to_edge(self):
                self.calls.append("snap")

            def pos(self):
                return launcher_window.QPoint(40, 60)

            def update(self):
                self.calls.append("update")

            def toggle_panel(self):
                self.calls.append("toggle")

        click_dummy = DummyOrb(moved=False)
        click_result = click_dummy._complete_orb_drag_release(toggle_on_click=True, refresh_orb=True)
        self.assertTrue(click_result)
        self.assertEqual(click_dummy.calls, ["update", "toggle"])

        drag_dummy = DummyOrb(moved=True)
        drag_result = drag_dummy._complete_orb_drag_release(toggle_on_click=True, refresh_orb=True)
        self.assertFalse(drag_result)
        self.assertEqual(drag_dummy.calls, ["snap", ("save", 40, 60), "update"])


if __name__ == "__main__":
    unittest.main()
