import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _demo_module():
    spec = importlib.util.spec_from_file_location("generate_demo", ROOT / "scripts" / "generate-demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SyntheticDemoTests(unittest.TestCase):
    def test_qq_demo_is_fictional_renderer_only_output(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("generate_demo", ROOT / "scripts" / "generate-demo.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            module.generate_qq_demo(output)
            brief = json.loads((output / "qq-brief.json").read_text(encoding="utf-8"))
            review = json.loads((output / "qq-review.json").read_text(encoding="utf-8"))
            from agent_console.report_metrics import body_token_count
            self.assertEqual(brief["chat_username"], "qq:1000000001")
            self.assertEqual(brief["title_prefix"], "QQ群日报")
            self.assertEqual(review["title_prefix"], "QQ群总结")
            self.assertTrue(all("tokens" in person and "count" not in person for person in review["participants"]))
            self.assertEqual(review["stats"]["messages"], 3)
            self.assertEqual(review["leaders"]["talk"]["tokens"], review["active_rank"][0]["tokens"])
            caption = next(person for person in review["participants"] if person["username"] == "1000000102")
            self.assertEqual(caption["tokens"], body_token_count("[图片]"))
            self.assertIn("虚构", (output / "index.html").read_text(encoding="utf-8"))
            self.assertEqual(len(list((output / "assets").glob("[a-f0-9]*.png"))), 1)

    def test_bundled_export_matches_its_generator(self):
        module = _demo_module()
        with tempfile.TemporaryDirectory() as temporary:
            fresh = Path(temporary) / "export"
            module.write_fixture(fresh)
            bundled = ROOT / "examples" / "synthetic-export"
            names = sorted(p.relative_to(fresh).as_posix() for p in fresh.rglob("*") if p.is_file())
            self.assertEqual(names, sorted(p.relative_to(bundled).as_posix() for p in bundled.rglob("*") if p.is_file()))
            for name in names:
                self.assertEqual((fresh / name).read_bytes(), (bundled / name).read_bytes(), name)

    def test_offline_demo_runs_the_real_digest_and_contract_sender(self):
        module = _demo_module()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "demo"
            summary = module.generate(output, ROOT / "examples" / "synthetic-export")
            self.assertEqual(summary["messages"], 7)
            self.assertEqual(summary["delivery"]["status"], "sent")
            brief = json.loads(Path(summary["editions"]["brief"]["json"]).read_text(encoding="utf-8"))
            review = json.loads(Path(summary["editions"]["review"]["json"]).read_text(encoding="utf-8"))
            self.assertEqual((brief["edition"], review["edition"]), ("brief", "review"))
            self.assertEqual([quote["text"] for quote in brief["quotes"]], ["标签用防水纸打印，字写大一点，小朋友也能看清。"])
            self.assertTrue(all(person["tokens"] > 0 for person in brief["participants"]))
            self.assertTrue(all(brief["avatars"][person["username"]]["available"] for person in brief["participants"]))
            self.assertEqual(brief["media_understanding"]["images"]["understood"], 1)
            self.assertTrue(Path(summary["editions"]["review"]["png"]).read_bytes().startswith(b"\x89PNG"))
            self.assertEqual(len(list((output / "outbox").glob("*.png"))), 1)


if __name__ == "__main__":
    unittest.main()
