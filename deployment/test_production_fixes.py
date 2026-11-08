"""Fixes merged from the production deployment after v1.2.4 (fictional providers, no network)."""
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import image_pipeline
import web_summary

PRIMARY = {"base_url": "https://primary.invalid/v1", "model": "primary", "api_key": "k1"}
CONFIG = {**PRIMARY, "fallbacks": [{"base_url": "https://backup.invalid/v1", "model": "backup", "api_key": "k2"}]}
PAGE = {"url": "https://example.org/a", "title": "t", "status": "success", "text_complete": True, "text": "正文"}


def http_error(code):
    return urllib.error.HTTPError("https://provider.invalid", code, "x", {}, None)


class WebSummaryFallbackTests(unittest.TestCase):
    def run_once(self, cache, behaviour, now):
        def summarize(page, provider):
            outcome = behaviour(provider)
            if isinstance(outcome, BaseException):
                raise outcome
            return {"title": "标题", "points": ["要点"], "uncertainties": [], "model": provider["model"]}
        with patch.object(web_summary, "summarize", side_effect=summarize), patch("time.time", return_value=now):
            output, _calls = web_summary.process({"m": {"pages": [PAGE]}}, CONFIG, cache)
        return output["m"]["pages"][0]

    def test_unavailable_primary_falls_back_through_the_global_chain(self):
        with tempfile.TemporaryDirectory() as temporary:
            page = self.run_once(Path(temporary), lambda p: http_error(503) if p["model"] == "primary" else "ok", 1000)
        self.assertEqual((page["status"], page["fallback_used"], page["configured_model"]), ("success", True, "backup"))

    def test_bad_request_does_not_switch_provider(self):
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            page = self.run_once(Path(temporary), lambda p: calls.append(p["model"]) or http_error(400), 1000)
        self.assertEqual(calls, ["primary"])
        self.assertEqual(page["status"], "pending")

    def test_failures_back_off_exponentially_capped_at_900_and_never_give_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            now, delays = 10_000, []
            for _ in range(9):
                page = self.run_once(cache, lambda p: http_error(503), now)
                self.assertEqual(page["status"], "pending")
                delays.append(page["retry_at"] - now)
                now = page["retry_at"]
            page = self.run_once(cache, lambda p: "ok", now)
        self.assertEqual(page["status"], "success")
        self.assertEqual(max(delays), 900)
        self.assertEqual(delays, sorted(delays))


class ImageStageTests(unittest.TestCase):
    def test_refused_and_terminal_missing_images_do_not_block(self):
        manifest = [{"message_uid": "ok"}, {"message_uid": "refused"}, {"message_uid": "missing", "terminal": True},
                    {"message_uid": "waiting", "source_quality": "pending"}]
        understood = {"ok": {"status": "success"},
                      "refused": {"status": "failed", "attempts": [{"category": "refusal", "retryable": False}]},
                      "missing": {"status": "unavailable"}, "waiting": {"status": "unavailable"}}
        pending, refused = image_pipeline._split_understood(manifest, understood)
        self.assertEqual([item["message_uid"] for item in pending], ["waiting"])
        self.assertEqual(refused, {"refused"})

    def test_caption_exit_one_with_result_line_is_finished_but_a_crash_is_not(self):
        line = json.dumps({"images": 3, "failed": 1})
        self.assertTrue(image_pipeline._stage_finished("caption-images.py", 1, "log\n" + line))
        self.assertFalse(image_pipeline._stage_finished("caption-images.py", 1, "Traceback"))
        self.assertFalse(image_pipeline._stage_finished("web_summary.py", 1, line))

    def test_stage_failures_are_persisted_privately(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            job = work / "image-jobs" / "daily-1"
            job.mkdir(parents=True)
            with patch.object(image_pipeline, "STAGE_FAILURE_LOG", work / "stage-failures.jsonl"), \
                 patch.object(image_pipeline.settings, "load_project", return_value={"worker_uid": 1, "worker_gid": 1}):
                image_pipeline._record_stage_failure(job, "daily", "ocr_images.py", 1, "out", "err")
            entry = json.loads((work / "stage-failures.jsonl").read_text().splitlines()[-1])
            self.assertEqual((entry["script"], entry["returncode"], entry["stderr_tail"]), ("ocr_images.py", 1, "err"))
            self.assertEqual((work / "stage-failures.jsonl").stat().st_mode & 0o777, 0o600)
        error = image_pipeline.StageError("ocr_images.py", "exit 1")
        self.assertEqual(error.label, "ocr_images.py: exit 1")


class OcrTerminalStateTests(unittest.TestCase):
    def test_missing_originals_and_absent_ocr_layer_never_block_the_pipeline(self):
        import builtins
        import io
        import hashlib
        import ocr_images
        import ocr_summary
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = io.BytesIO()
            Image.new("RGB", (4, 4), "white").save(data, format="PNG")
            image = root / "a.png"
            image.write_bytes(data.getvalue())
            manifest = [{"message_uid": "ready", "available": True, "source_quality": "original", "image_path": str(image),
                         "sha256": hashlib.sha256(data.getvalue()).hexdigest()},
                        {"message_uid": "missing", "available": False, "source_quality": "missing", "terminal": True},
                        {"message_uid": "waiting", "available": False, "source_quality": "pending"}]
            real_import = builtins.__import__

            def no_ocr(name, *args, **kwargs):
                if name.startswith("rapidocr"):
                    raise ImportError(name)
                return real_import(name, *args, **kwargs)
            with patch("builtins.__import__", side_effect=no_ocr):
                ocr_images.process_manifest(manifest, root / "ocr.json", root / "cache")
            rows = json.loads((root / "ocr.json").read_text())
            self.assertEqual({uid: row["status"] for uid, row in rows.items()},
                             {"ready": "unavailable", "missing": "unavailable", "waiting": "pending"})
            summary, _calls = ocr_summary.process(rows, CONFIG, root / "summary-cache")
            auxiliary = image_pipeline._build_auxiliary_context(summary, {})
            blocking = {uid for uid, item in auxiliary.items()
                        if any(entry.get("status") in ("pending", "failed") for entry in item.values())}
            self.assertEqual(blocking, {"waiting"})


if __name__ == "__main__":
    unittest.main()
