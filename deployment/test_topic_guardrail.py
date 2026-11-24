"""Unit tests for two-layer selective sensitive-topic guardrail (fictional data only)."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

DEPLOYMENT = Path(__file__).resolve().parent


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, DEPLOYMENT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


topic_guardrail = _load_module("topic_guardrail", "topic_guardrail.py")
digest = _load_module("digest", "digest.py")


def _msg(index: int, text: str, *, author: str = "Member", image_desc: str = "", visible_text: str = "") -> dict:
    row = {
        "message_uid": f"uid-{index}",
        "reference": f"M{index:04d}",
        "timestamp": 1_793_145_600 + index * 60,
        "author": author,
        "author_id": f"u-{index}",
        "type": "image" if image_desc or visible_text else "text",
        "text": text,
    }
    if image_desc or visible_text:
        row["image_understanding"] = {
            "description": image_desc,
            "visible_text": visible_text,
            "facts": [],
            "uncertainties": [],
        }
    return row


def _valid_cn_id() -> str:
    prefix = "11010120000101123"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    check = "10X98765432"[sum(int(prefix[i]) * weights[i] for i in range(17)) % 11]
    return prefix + check


class TopicGuardrailTests(unittest.TestCase):
    def test_layer1_prompts_require_silent_selective_ignoring_without_contradiction(self):
        for prompt in (digest._SUMMARY_BRIEF_PROMPT, digest._SUMMARY_REVIEW_PROMPT):
            self.assertIn("选择性忽视与合规边界", prompt)
            self.assertIn("绝不输出“已略过敏感话题”", prompt)
            self.assertIn("马原/毛概", prompt)
        self.assertIn("需静默忽略的敏感话题", digest._SUMMARY_REVIEW_PROMPT)
        self.assertIn("涉政或敏感话题", digest._SUMMARY_QUOTE_RULES)

    def test_deterministic_redlines_catch_nodes_ids_tokens_and_keep_exemptions(self):
        valid_id = _valid_cn_id()
        invalid_id = valid_id[:-1] + ("0" if valid_id[-1] != "0" else "1")
        self.assertTrue(topic_guardrail.is_valid_cn_id_card(valid_id))
        self.assertFalse(topic_guardrail.is_valid_cn_id_card(invalid_id))

        self.assertEqual(topic_guardrail.check_hard_redlines("vless://11111111-2222-3333-4444-555555555555@example.invalid:443"), "circumvention_uri")
        self.assertEqual(topic_guardrail.check_hard_redlines("机场推荐和免费节点订阅链接"), "circumvention_promo")
        self.assertEqual(topic_guardrail.check_hard_redlines(f"身份证 {valid_id}"), "cn_id_card")
        self.assertEqual(topic_guardrail.check_hard_redlines("sk-abcdefghijklmnopqrstuvwxyz1234"), "api_token")
        self.assertEqual(topic_guardrail.check_hard_redlines("社工库查询与人肉开盒教程"), "contraband_or_doxxing")

        # Harmless academic, technical, and everyday terms must never trigger Stage 2A.
        benign_samples = [
            f"随机测试编号 {invalid_id}",
            "新相机开盒评测视频",
            "马原和毛概作业本周五截止，思政课点名了",
            "OpenClash 规则排障以及 Nginx 反向代理 502 修复",
            "Tailscale 子网路由和 API 网关限流排查",
            "游戏剧情里主角暴打 Boss，早八人快累死了",
        ]
        for sample in benign_samples:
            with self.subTest(sample=sample):
                self.assertIsNone(topic_guardrail.check_hard_redlines(sample))
                self.assertFalse(topic_guardrail.has_meta_filter_notice(sample))

        self.assertTrue(topic_guardrail.has_meta_filter_notice("敏感话题已略过"))
        self.assertTrue(topic_guardrail.has_meta_filter_notice("部分政治内容不予展示"))
        self.assertTrue(topic_guardrail.has_meta_filter_notice("不说这个了，大家聊别的"))

    def test_stage_a_strips_tainted_message_ids_and_drops_tainted_topics_and_quotes(self):
        valid_id = _valid_cn_id()
        messages = [
            _msg(0, "实验室机箱风扇噪音有点大，准备换静音扇。"),
            _msg(1, "vless://abcdef123456@proxy.invalid:443?security=tls"),
            _msg(2, f"实名信息：{valid_id}"),
            _msg(3, "大家记得去领本周测试器材。"),
        ]
        candidates = [
            {"message_id": "M0001", "author": "A", "timestamp": 1, "text": messages[1]["text"]},
            {"message_id": "M0003", "author": "B", "timestamp": 2, "text": "记得带校园卡领本周测试器材"},
        ]
        raw = {
            "one_line": "已略过敏感话题，今天聊了风扇和领器材。",
            "topics": [
                {
                    "title": "静音风扇更换计划",
                    "summary": "讨论替换机箱静音风扇。",
                    "details": ["先测量风扇尺寸"],
                    "message_ids": ["M0000", "M0001"],
                },
                {
                    "title": "隐私号码核对",
                    "summary": "群内有人发了一串号码。",
                    "details": ["包含证件号码"],
                    "message_ids": ["M0002"],
                },
            ],
            "quotes": [0, 1],
        }

        def fake_review(_cfg, _schema, _prompt, payload, _diag, _name):
            # Stage 2A already stripped M0001 and dropped topic 1 and quote 0 before Stage 2B.
            self.assertEqual(
                [[m["reference"] for m in t["cited_messages"]] for t in payload["topics"]],
                [["M0000"]],
            )
            self.assertEqual([q["quote_index"] for q in payload["quotes"]], [1])
            self.assertEqual(payload["one_line"], "")
            return {
                "drop_topic_indices": [],
                "strip_message_ids": [],
                "drop_quote_indices": [],
                "one_line_unsafe": False,
                "replacement_one_line": "今日群内讨论了实验室静音风扇更换计划。",
            }

        with tempfile.TemporaryDirectory() as root:
            sanitized = topic_guardrail.apply_guardrail(
                raw,
                candidates=candidates,
                messages=messages,
                config={"guardrail": {"enabled": True, "semantic_review": True}},
                runtime=root,
                diagnostics_dir=None,
                request_fn=fake_review,
            )
        self.assertEqual(sanitized["one_line"], "今日群内讨论了实验室静音风扇更换计划。")
        self.assertEqual(sanitized["topics"][0]["message_ids"], ["M0000"])
        self.assertEqual(len(sanitized["topics"]), 1)
        self.assertEqual(sanitized["quotes"], [1])

    def test_stage_b_drops_political_topics_and_falls_back_to_neutral_when_all_dropped(self):
        messages = [
            _msg(0, "虚构某地官员被查处分消息，纯虚构测试文本。"),
            _msg(1, "这条虚构涉政感叹句不该作为金句保留。"),
        ]
        candidates = [{"message_id": "M0001", "author": "A", "timestamp": 1, "text": messages[1]["text"]}]
        raw = {
            "one_line": "群友围绕虚构地方官员被查通报展开热议。",
            "topics": [
                {
                    "title": "虚构地方官员被查通报",
                    "summary": "虚构讨论官员落马人事变动。",
                    "details": ["纯虚构样本细节"],
                    "message_ids": ["M0000"],
                }
            ],
            "quotes": [0],
        }
        verdict = {
            "drop_topic_indices": [0],
            "strip_message_ids": [],
            "drop_quote_indices": [0],
            "one_line_unsafe": True,
            "replacement_one_line": "敏感话题已略过，没有其他内容。",  # contains meta-notice -> must be rejected!
        }
        with tempfile.TemporaryDirectory() as root:
            sanitized = topic_guardrail.apply_guardrail(
                raw,
                candidates=candidates,
                messages=messages,
                config={},
                runtime=root,
                diagnostics_dir=None,
                request_fn=lambda *_: verdict,
            )
        self.assertEqual(sanitized["topics"], [])
        self.assertEqual(sanitized["quotes"], [])
        self.assertEqual(sanitized["one_line"], topic_guardrail.NEUTRAL_EMPTY_ONE_LINE)

    def test_stage_b_strips_adjacent_sensitive_ref_and_rewrites_one_line_deterministically(self):
        messages = [
            _msg(0, "Nginx 反向代理 502 错误解决了，是上游端口写错。"),
            _msg(1, "顺带发了一张虚构涉政新闻截图", image_desc="虚构涉政评论截图"),
            _msg(2, "毛概论文下周一交，大家记得查重。"),
        ]
        raw = {
            "one_line": "群友排查了 Nginx 502 并顺便看了虚构涉政新闻。",
            "topics": [
                {
                    "title": "Nginx 反向代理 502 排障",
                    "summary": "定位到上游端口配置不一致导致 502。",
                    "details": ["修改 upstream 端口后恢复正常"],
                    "message_ids": ["M0000", "M0001"],
                },
                {
                    "title": "毛概论文提交提醒",
                    "summary": "提醒下周一前完成查重并提交。",
                    "details": [],
                    "message_ids": ["M0002"],
                },
            ],
            "quotes": [],
        }
        verdict = {
            "drop_topic_indices": [],
            "strip_message_ids": ["M0001"],
            "drop_quote_indices": [],
            "one_line_unsafe": True,
            "replacement_one_line": "",  # empty -> deterministic fallback from safe topic titles
        }
        with tempfile.TemporaryDirectory() as root:
            sanitized = topic_guardrail.apply_guardrail(
                raw,
                candidates=[],
                messages=messages,
                config={},
                runtime=root,
                diagnostics_dir=None,
                request_fn=lambda *_: verdict,
            )
        self.assertEqual(sanitized["topics"][0]["message_ids"], ["M0000"])
        self.assertEqual([t["title"] for t in sanitized["topics"]], ["Nginx 反向代理 502 排障", "毛概论文提交提醒"])
        self.assertEqual(sanitized["one_line"], "Nginx 反向代理 502 排障；毛概论文提交提醒。")

    def test_content_filter_bisection_isolates_only_declined_topic(self):
        messages = [
            _msg(0, "OpenClash 本地 DNS 分流规则验证通过。"),
            _msg(1, "虚构触发上游内容审查的涉政条目。"),
            _msg(2, "3D 打印机喷嘴清理教程分享。"),
        ]
        raw = {
            "one_line": "汇总了 DNS 分流、虚构涉政条目和 3D 打印清理。",
            "topics": [
                {"title": "OpenClash DNS 分流", "summary": "分流规则生效。", "details": [], "message_ids": ["M0000"]},
                {"title": "触发审查条目", "summary": "触发模型拒答的内容。", "details": [], "message_ids": ["M0001"]},
                {"title": "3D 打印喷嘴清理", "summary": "冷拉法清堵成功。", "details": [], "message_ids": ["M0002"]},
            ],
            "quotes": [],
        }

        def fake_request(_cfg, _schema, _prompt, payload, _diag, _name):
            titles = [t["title"] for t in payload["topics"]]
            if "触发审查条目" in titles:
                raise RuntimeError("AI returned invalid structured JSON: Model declined the request; no fabricated summary is permitted")
            return {
                "drop_topic_indices": [],
                "strip_message_ids": [],
                "drop_quote_indices": [],
                "one_line_unsafe": False,
                "replacement_one_line": "",
            }

        with tempfile.TemporaryDirectory() as root:
            sanitized = topic_guardrail.apply_guardrail(
                raw,
                candidates=[],
                messages=messages,
                config={},
                runtime=root,
                diagnostics_dir=None,
                request_fn=fake_request,
            )
        self.assertEqual([t["title"] for t in sanitized["topics"]], ["OpenClash DNS 分流", "3D 打印喷嘴清理"])
        self.assertEqual(sanitized["one_line"], "OpenClash DNS 分流；3D 打印喷嘴清理。")

    def test_non_refusal_provider_failure_propagates_fail_closed(self):
        messages = [_msg(0, "正常开发讨论")]
        raw = {
            "one_line": "开发讨论",
            "topics": [{"title": "接口重构", "summary": "正常内容", "details": [], "message_ids": ["M0000"]}],
            "quotes": [],
        }
        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(RuntimeError, "AI request failed"):
            topic_guardrail.apply_guardrail(
                raw,
                candidates=[],
                messages=messages,
                config={},
                runtime=root,
                diagnostics_dir=None,
                request_fn=lambda *_: (_ for _ in ()).throw(RuntimeError("AI request failed")),
            )

    def test_private_blocklist_file_loading_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = Path(root)
            blocklist = runtime / "guardrail-blocklist.txt"
            blocklist.write_text("# comment\n虚构屏蔽短语\n", encoding="utf-8")
            token_with = topic_guardrail.guardrail_cache_token({}, runtime)
            self.assertEqual(
                topic_guardrail.check_hard_redlines(
                    "包含 虚 构 屏 蔽 短 语 的消息",
                    extra_terms=topic_guardrail.load_guardrail_settings({}, runtime)["extra_terms"],
                ),
                "custom_block_term",
            )
            blocklist.write_text("# comment\n虚构屏蔽短语\n第二个词\n", encoding="utf-8")
            token_updated = topic_guardrail.guardrail_cache_token({}, runtime)
            self.assertNotEqual(token_with, token_updated)

            link = runtime / "link.txt"
            link.symlink_to(blocklist)
            with patch.dict("os.environ", {"CHAT_DIGEST_GUARDRAIL_BLOCKLIST": str(link)}):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    topic_guardrail.load_guardrail_settings({}, runtime)


if __name__ == "__main__":
    unittest.main()
