import os
import tempfile
import time
import unittest

from launcher_core_parts import sessions as sess
from qt_chat_parts.personal_usage import PersonalUsageMixin


class TokenBillingCoreTests(unittest.TestCase):
    def test_v1_usage_normalizes_to_v2_with_legacy_unpriced_counters(self):
        session = {
            "id": "s1",
            "channel_id": "launcher",
            "updated_at": 1000,
            "token_usage": {
                "version": 1,
                "events": [
                    {"ts": 1000, "input_tokens": 10, "output_tokens": 20, "usage_source": "provider"},
                    {"ts": 1001, "input_tokens": 5, "output_tokens": 0, "usage_source": "estimate"},
                ],
            },
        }

        sess._normalize_token_usage_inplace(session)
        usage = session["token_usage"]

        self.assertEqual(usage["version"], 2)
        self.assertEqual(usage["input_tokens"], 15)
        self.assertEqual(usage["output_tokens"], 20)
        self.assertEqual(usage["total_tokens"], 35)
        self.assertEqual(usage["priced_event_count"], 0)
        self.assertEqual(usage["legacy_unpriced_event_count"], 2)
        self.assertEqual(usage["cost_total"], 0)
        self.assertEqual(usage["events"][0]["billing_mode"], "legacy_unpriced")

    def test_price_snapshot_freezes_cost_after_rule_change(self):
        cfg = {}
        first_rule = sess.set_usage_price_rule(
            cfg,
            "local",
            "local",
            "native_oai_config",
            {"input_per_1m": 2, "output_per_1m": 10, "api_card_label": "Primary"},
        )
        event = {
            "input_tokens": 1_000_000,
            "output_tokens": 500_000,
            "api_card_var": "native_oai_config",
            "usage_source": "provider",
        }
        sess.apply_usage_price_snapshot(event, sess.usage_price_snapshot(first_rule, "USD"))
        self.assertEqual(event["cost_total"], 7)

        sess.set_usage_price_rule(
            cfg,
            "local",
            "local",
            "native_oai_config",
            {"input_per_1m": 20, "output_per_1m": 100, "api_card_label": "Primary"},
        )

        session = {"id": "s", "token_usage": {"events": [event]}}
        sess._normalize_token_usage_inplace(session)
        self.assertEqual(session["token_usage"]["events"][0]["cost_total"], 7)
        self.assertEqual(session["token_usage"]["cost_total"], 7)

    def test_local_and_remote_pricing_are_separate(self):
        cfg = {}
        sess.set_usage_price_rule(cfg, "local", "local", "native_oai_config", {"input_per_1m": 1})
        sess.set_usage_price_rule(cfg, "remote", "box-1", "native_oai_config", {"input_per_1m": 9})

        local_rule = sess.usage_price_rule(cfg, "local", "local", "native_oai_config")
        remote_rule = sess.usage_price_rule(cfg, "remote", "box-1", "native_oai_config")

        self.assertEqual(local_rule["input_per_1m"], 1)
        self.assertEqual(remote_rule["input_per_1m"], 9)
        self.assertEqual(sess.usage_pricing_target_key("remote", "box-1"), "remote:box-1")

    def test_estimate_only_event_can_be_priced_with_snapshot(self):
        rule = {
            "api_card_var": "native_oai_config",
            "api_card_label": "Primary",
            "input_per_1m": 4,
            "output_per_1m": 8,
        }
        event = {
            "input_tokens": 250_000,
            "output_tokens": 125_000,
            "api_card_var": "native_oai_config",
            "usage_source": "estimate",
        }

        sess.apply_usage_price_snapshot(event, sess.usage_price_snapshot(rule, "USD"))

        self.assertEqual(event["billing_mode"], "priced")
        self.assertEqual(event["usage_source"], "estimate")
        self.assertEqual(event["cost_total"], 2)

    def test_cache_buckets_do_not_double_count_input_tokens(self):
        rule = {
            "api_card_var": "native_oai_config",
            "api_card_label": "Primary",
            "input_per_1m": 10,
            "cache_read_per_1m": 1,
            "cache_creation_per_1m": 5,
        }
        event = {
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_read_input_tokens": 300_000,
            "cache_creation_input_tokens": 200_000,
            "api_card_var": "native_oai_config",
            "usage_source": "provider",
        }

        sess.apply_usage_price_snapshot(event, sess.usage_price_snapshot(rule, "USD"))

        self.assertEqual(event["billable_input_tokens"], 500_000)
        self.assertEqual(event["cost_input"], 5)
        self.assertEqual(event["cost_cache_read"], 0.3)
        self.assertEqual(event["cost_cache_creation"], 1)
        self.assertEqual(event["cost_total"], 6.3)

    def test_bucket_costs_supply_event_total_when_cost_total_missing_or_empty(self):
        for missing_value in (None, ""):
            event = {
                "input_tokens": 0,
                "output_tokens": 0,
                "usage_source": "provider",
                "api_card_var": "native_oai_config",
                "price_snapshot": {
                    "api_card_var": "native_oai_config",
                    "api_card_label": "Primary",
                    "currency": "USD",
                    "unit": "per_1m_tokens",
                    "input_per_1m": 1,
                },
                "cost_input": 0.12,
                "cost_output": 0.34,
                "cost_cache_read": 0.05,
                "cost_cache_creation": 0.07,
                "cost_total": missing_value,
            }
            session = {"id": "s", "token_usage": {"events": [event]}}

            sess._normalize_token_usage_inplace(session)

            self.assertEqual(session["token_usage"]["events"][0]["cost_total"], 0.58)
            self.assertEqual(session["token_usage"]["cost_total"], 0.58)


class DummyUsage(PersonalUsageMixin):
    def __init__(self, agent_dir, cfg=None):
        self.agent_dir = agent_dir
        self.cfg = cfg or {}

    def _set_status(self, _text):
        pass

    def _ensure_session_usage_metadata(self, session):
        sess._normalize_token_usage_inplace(session)


class TokenBillingUsageTests(unittest.TestCase):
    def make_agent_dir(self):
        root = tempfile.TemporaryDirectory()
        for name in ("launch.pyw", "agentmain.py"):
            with open(os.path.join(root.name, name), "w", encoding="utf-8") as f:
                f.write("# test\n")
        return root

    def test_collect_usage_keeps_token_panels_and_adds_cost(self):
        root = self.make_agent_dir()
        self.addCleanup(root.cleanup)
        event = {
            "ts": time.time(),
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "usage_source": "provider",
            "api_card_var": "native_oai_config",
            "api_card_label": "Primary",
            "price_snapshot": {
                "api_card_var": "native_oai_config",
                "api_card_label": "Primary",
                "currency": "USD",
                "unit": "per_1m_tokens",
                "input_per_1m": 10,
                "output_per_1m": 20,
            },
            "cost_input": 0.001,
            "cost_output": 0.001,
            "cost_total": 0.002,
        }
        sess.save_session(
            root.name,
            {
                "id": "s1",
                "title": "Priced",
                "created_at": time.time(),
                "updated_at": time.time(),
                "channel_id": "launcher",
                "token_usage": {"events": [event]},
            },
            touch=False,
        )

        stats = DummyUsage(root.name, {"usage_pricing": {"currency": "USD"}})._collect_usage_stats()

        self.assertEqual(stats["all"]["total_tokens"], 150)
        self.assertAlmostEqual(stats["all"]["cost_total"], 0.002)
        self.assertEqual(stats["activity"]["priced_events"], 1)
        self.assertEqual(stats["activity"]["legacy_unpriced_events"], 0)
        self.assertEqual(stats["models"][0]["api_card_var"], "native_oai_config")

    def test_collect_usage_keeps_frozen_currency_totals_after_global_currency_change(self):
        root = self.make_agent_dir()
        self.addCleanup(root.cleanup)
        now = time.time()
        for sid, currency, cost in (("usd", "USD", 1.25), ("cny", "CNY", 8.5)):
            sess.save_session(
                root.name,
                {
                    "id": sid,
                    "title": sid,
                    "created_at": now,
                    "updated_at": now,
                    "channel_id": "launcher",
                    "token_usage": {
                        "events": [
                            {
                                "ts": now,
                                "input_tokens": 100,
                                "output_tokens": 0,
                                "total_tokens": 100,
                                "usage_source": "provider",
                                "api_card_var": "native_oai_config",
                                "price_snapshot": {
                                    "api_card_var": "native_oai_config",
                                    "currency": currency,
                                    "unit": "per_1m_tokens",
                                    "input_per_1m": cost * 10000,
                                },
                                "currency": currency,
                                "cost_input": cost,
                                "cost_total": cost,
                            }
                        ]
                    },
                },
                touch=False,
            )

        stats = DummyUsage(root.name, {"usage_pricing": {"currency": "EUR"}})._collect_usage_stats()
        payload = DummyUsage(root.name, {"usage_pricing": {"currency": "EUR"}})._usage_build_export_payload(
            stats,
            {"label": "本机", "scope": "local", "device_id": "local"},
            {},
        )

        self.assertTrue(stats["all"]["mixed_currency"])
        self.assertEqual(stats["all"]["currency_totals"], {"CNY": 8.5, "USD": 1.25})
        self.assertEqual(payload["billing"]["currency"], "MIXED")
        self.assertEqual(payload["billing"]["current_currency"], "EUR")
        self.assertEqual(payload["billing"]["currency_totals"], {"CNY": 8.5, "USD": 1.25})

    def test_usage_export_includes_billing_and_legacy_distinction(self):
        dummy = DummyUsage("", {"usage_pricing": {"currency": "USD"}})
        payload = dummy._usage_build_export_payload(
            {
                "today": {"total_tokens": 10, "cost_total": 0.01, "currency_totals": {"USD": 0.01}, "sources": {"provider"}},
                "recent": {"total_tokens": 10, "cost_total": 0.01, "currency_totals": {"USD": 0.01}, "sources": {"provider"}},
                "all": {"total_tokens": 10, "cost_total": 0.01, "currency_totals": {"USD": 0.01}, "sources": {"provider"}},
                "activity": {"priced_events": 1, "estimated_priced_events": 0, "legacy_unpriced_events": 2, "models": {"m"}},
                "warnings": ["legacy"],
                "channels": [],
                "models": [],
                "sources": [],
                "timeline": [],
                "sessions": [],
                "days": [],
            },
            {"label": "本机", "scope": "local", "device_id": "local"},
            {},
        )

        self.assertEqual(payload["billing"]["currency"], "USD")
        self.assertEqual(payload["billing"]["priced_events"], 1)
        self.assertEqual(payload["billing"]["legacy_unpriced_events"], 2)
        self.assertEqual(payload["summary"]["activity"]["models"], ["m"])

    def test_pricing_cards_include_saved_remote_rules_without_usage_stats(self):
        cfg = {}
        sess.set_usage_price_rule(
            cfg,
            "remote",
            "box-1",
            "remote_oai_config",
            {"api_card_label": "Remote Primary", "input_per_1m": 3},
        )
        dummy = DummyUsage("", cfg)

        cards = dummy._usage_api_cards_for_pricing(
            {"label": "Box", "scope": "remote", "device_id": "box-1", "is_remote": True},
            {"models": [], "sessions": [], "timeline": []},
        )

        self.assertIn({"var": "remote_oai_config", "label": "Remote Primary"}, cards)

    def test_pricing_cards_keep_saved_rules_missing_from_current_stats_and_mykey(self):
        root = self.make_agent_dir()
        self.addCleanup(root.cleanup)
        with open(os.path.join(root.name, "mykey.py"), "w", encoding="utf-8") as f:
            f.write(
                "native_oai_config = {\n"
                "    'name': 'Current Primary',\n"
                "    'model': 'gpt-current',\n"
                "}\n"
            )
        cfg = {}
        sess.set_usage_price_rule(
            cfg,
            "local",
            "local",
            "archived_config",
            {"api_card_label": "Archived Card", "output_per_1m": 7},
        )
        dummy = DummyUsage(root.name, cfg)

        cards = dummy._usage_api_cards_for_pricing(
            {"label": "本机", "scope": "local", "device_id": "local", "is_remote": False},
            {"models": [], "sessions": [], "timeline": []},
        )

        self.assertIn({"var": "native_oai_config", "label": "Current Primary"}, cards)
        self.assertIn({"var": "archived_config", "label": "Archived Card"}, cards)

    def test_pricing_cards_can_use_current_remote_api_state_before_usage_exists(self):
        class RemoteUsage(DummyUsage):
            def _settings_target_context(self):
                return {"label": "Box", "scope": "remote", "device_id": "box-1", "is_remote": True}

        dummy = RemoteUsage("", {})
        dummy._qt_api_state = [
            {"var": "remote_oai_config", "name": "Remote Primary", "model": "gpt-remote"}
        ]

        cards = dummy._usage_api_cards_for_pricing(
            {"label": "Box", "scope": "remote", "device_id": "box-1", "is_remote": True},
            {"models": [], "sessions": [], "timeline": []},
        )

        self.assertEqual(cards, [{"var": "remote_oai_config", "label": "Remote Primary"}])


if __name__ == "__main__":
    unittest.main()
