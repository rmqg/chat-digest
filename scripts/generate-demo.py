#!/usr/bin/env python3
"""Offline demo: build reports from a fictional chat-export/v1 directory with a loopback stand-in model.

No account, no chat client and no internet: the "model" is a tiny HTTP server on 127.0.0.1 that returns a
deterministic summary, and the "sender" is scripts/demo-chat-send.py, which only writes into a local outbox.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPORT = ROOT / "examples" / "synthetic-export"
CONVERSATION = "g_demo_garden"
START = 1793145600  # 2026-10-28T00:00:00Z
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "deployment"))
from agent_console import daily_report  # noqa: E402


def _save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def _image(path: Path) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError("Pillow is required to generate the synthetic PNG illustration") from exc
    image = Image.new("RGB", (720, 360), "#f5f8fc")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((22, 22, 698, 338), radius=24, fill="#ffffff", outline="#b7c8df", width=3)
    draw.ellipse((68, 80, 208, 220), fill="#d8e9ff", outline="#6288b8", width=4)
    draw.ellipse((100, 110, 176, 186), fill="#81addb")
    draw.polygon([(112, 190), (164, 190), (138, 236)], fill="#81addb")
    draw.rounded_rectangle((248, 82, 620, 144), radius=18, fill="#e7f2ff")
    draw.rounded_rectangle((248, 166, 560, 228), radius=18, fill="#eaf5eb")
    draw.rounded_rectangle((248, 250, 624, 296), radius=18, fill="#fff1d9")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=False)



def _avatar(path: Path, color: str) -> None:
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (96, 96), "#ffffff")
    ImageDraw.Draw(image).ellipse((8, 8, 88, 88), fill=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=False)


def _media(root: Path, source: Path, ext: str = "png") -> dict:
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    relative = f"media/{digest[:2]}/{digest}.{ext}"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return {"path": relative, "sha256": digest, "bytes": len(payload), "mime": "image/png"}


def write_fixture(root: Path) -> None:
    """Deterministic fictional export; every person, message and picture is invented."""
    if root.exists():
        shutil.rmtree(root)
    scratch = root / ".build"
    _image(scratch / "garden-plan.png")
    people = [("u_lin", "林小满", "#9cc3e6"), ("u_zhou", "周禾", "#a8d5a2"), ("u_chen", "陈星", "#f2c48d")]
    members = []
    for sender, name, color in people:
        _avatar(scratch / f"{sender}.png", color)
        members.append({"id": sender, "name": name, "avatar": _media(root, scratch / f"{sender}.png")["path"]})
    picture = _media(root, scratch / "garden-plan.png")
    rows = [
        {"id": "m001", "ts": START + 3600, "sender_id": "u_lin", "sender_name": "林小满", "type": "text",
         "text": "周末的社区花园活动改到周六上午，入口旁边集合。"},
        {"id": "m002", "ts": START + 3900, "sender_id": "u_zhou", "sender_name": "周禾", "type": "text",
         "text": "收到，我会带上工具箱，顺便把旧花盆清理出来。"},
        {"id": "m003", "ts": START + 4200, "sender_id": "u_chen", "sender_name": "陈星", "type": "quote",
         "text": "我可以负责准备植物标签。", "reply_to": {"id": "m001", "sender_name": "林小满",
                                                   "text": "周末的社区花园活动改到周六上午，入口旁边集合。"}},
        {"id": "m004", "ts": START + 7200, "sender_id": "u_lin", "sender_name": "林小满", "type": "forward",
         "forward": [{"sender_name": "社区志愿者", "ts": START + 7000, "type": "text", "text": "A区：香草；B区：花苗"}]},
        {"id": "m005", "ts": START + 10800, "sender_id": "u_zhou", "sender_name": "周禾", "type": "image",
         "media": [{"kind": "image", "status": "ready", "is_thumbnail": False, **picture}]},
        {"id": "m006", "ts": START + 11000, "sender_id": "u_chen", "sender_name": "陈星", "type": "link",
         "link": {"url": "https://example.org/garden-guide", "title": "城市小花园入门（虚构链接）", "description": "示例链接，demo 不联网抓取"}},
        {"id": "m007", "ts": START + 14400, "sender_id": "u_chen", "sender_name": "陈星", "type": "text",
         "text": "标签用防水纸打印，字写大一点，小朋友也能看清。"},
    ]
    (root / "conversations").mkdir(parents=True, exist_ok=True)
    (root / "members").mkdir(parents=True, exist_ok=True)
    (root / "conversations" / f"{CONVERSATION}.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    (root / "members" / f"{CONVERSATION}.json").write_text(json.dumps(
        {"conversation_id": CONVERSATION, "updated_at": START, "members": members}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema": "chat-export/v1", "producer": {"name": "chat-digest-demo", "version": "2.0.0"},
                "source": {"platform": "other", "account_id": "demo_account"},
                "generated_at": "2026-10-29T08:00:00+08:00", "timezone": "Asia/Shanghai",
                "conversations": [{"id": CONVERSATION, "kind": "group", "name": "合成示例群（虚构）",
                                   "file": f"conversations/{CONVERSATION}.jsonl", "members_file": f"members/{CONVERSATION}.json",
                                   "last_ts": rows[-1]["ts"], "message_count": len(rows)}]}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "README.md").write_text("# 合成 chat-export/v1 样本\n\n全部人物、消息、图片均为虚构，供 `scripts/generate-demo.py` 离线演示和测试使用。"
                                   "格式见 `docs/CONTRACT.md`。\n", encoding="utf-8")
    shutil.rmtree(scratch)


def _summary(contents: dict, edition: str) -> dict:
    refs = [message["reference"] for message in contents["messages"]]
    topics = [{"title": "社区花园周六活动安排", "summary": "活动改到周六上午在入口集合，成员认领了工具箱和植物标签。",
               "details": [] if edition == "brief" else ["周禾带工具箱并清理旧花盆。", "陈星负责防水纸打印的大字标签。"],
               "message_ids": refs[:3] + refs[-1:]},
              {"title": "分区安排与示意图", "summary": "转发了虚构的分区安排，并附上一张合成示意图和一篇参考链接。",
               "details": [] if edition == "brief" else ["A区种香草，B区种花苗。"], "message_ids": refs[3:6]}]
    quotes = [item["candidate_index"] for item in contents.get("quote_candidates", [])
              if "防水纸" in item["text"]][:1]
    return {"one_line": "合成示例：成员确定周六上午的社区花园活动，并分好了准备工作。", "topics": topics, "quotes": quotes}


class _StandInModel(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(length))
        name = request["response_format"]["json_schema"]["name"]
        contents = json.loads(request["messages"][1]["content"])
        content = json.dumps(_summary(contents, "brief" if name.endswith("brief") else "review"), ensure_ascii=False)
        chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": content}}], "model": "demo-stand-in"},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "model": "demo-stand-in"}]
        body = "".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def generate(output: Path, export: Path, *, send: bool = True) -> dict:
    import chat_export
    if output.exists():
        shutil.rmtree(output)
    work, reports = output / "work", output / "reports"
    for directory in (work, reports):
        directory.mkdir(parents=True, mode=0o700)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StandInModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        model = output / "model.json"
        _save_json(model, {"base_url": f"http://127.0.0.1:{server.server_address[1]}/v1", "model": "demo-stand-in",
                           "api_key": "demo-only-not-a-secret"})
        project = output / "project.json"
        sender = [sys.executable, str(ROOT / "scripts" / "demo-chat-send.py"), "--outbox", str(output / "outbox")]
        _save_json(project, {"export": {"root": str(export)}, "group": {"conversation_id": CONVERSATION, "name": "合成示例群（虚构）"},
                             "send": {"command": sender, "to": "合成示例群（虚构）", "timeout": 60},
                             "report_prefix": "chat-digest-demo", "timezone": "Asia/Shanghai"})
        manifest = chat_export.load_manifest(export)
        _conv, messages = chat_export.read_messages(export, CONVERSATION, START, START + 86400, manifest=manifest)
        understanding = {m["message_uid"]: {"status": "success", "model": "demo-stand-in", "source_quality": "original",
                                            "result": {"description": "合成的社区花园分区示意图，含三条彩色分区。",
                                                       "visible_text": "", "facts": ["示意图为虚构插画"], "uncertainties": []}}
                         for m in messages if m["type"] == "image"}
        understanding_path = output / "image-understanding.json"
        _save_json(understanding_path, understanding)
        env = {**os.environ, "CHAT_DIGEST_CONFIG": str(project), "PYTHONPATH": os.pathsep.join(
            [str(ROOT / "deployment"), str(ROOT / "agent_console")]), "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(key, None)
        completed = subprocess.run([sys.executable, str(ROOT / "deployment" / "digest.py"), "--export-root", str(export),
                                    "--runtime-dir", str(work), "--output-dir", str(reports), "--start", str(START),
                                    "--end", str(START + 86400), "--model-config", str(model),
                                    "--image-understanding", str(understanding_path)],
                                   env=env, capture_output=True, text=True, timeout=300)
        if completed.returncode:
            raise RuntimeError("demo digest failed: " + completed.stderr[-2000:])
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        server.shutdown()
    delivery = None
    if send:
        sys.path.insert(0, str(ROOT / "deployment"))
        import chat_send
        config = chat_send.send_config(json.loads(project.read_text(encoding="utf-8")))
        png = Path(result["editions"]["brief"]["png"])
        rid = chat_send.request_id("demo", CONVERSATION, hashlib.sha256(png.read_bytes()).hexdigest())
        delivery = chat_send.send(config, rid=rid, image=png)
        again = chat_send.send(config, rid=rid, image=png)
        if delivery["status"] != "sent" or again.get("detail") != "already sent (idempotent replay)":
            raise RuntimeError("demo sender did not follow chat-send/v1")
    links = "".join(f"<li>{edition}: <a href=\"reports/{Path(files['html']).name}\">HTML</a> · "
                    f"<a href=\"reports/{Path(files['png']).name}\">PNG</a></li>" for edition, files in result["editions"].items())
    (output / "index.html").write_text("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>chat-digest 离线演示</title>"
                                       "<body><h1>chat-digest 离线演示</h1><p>全部内容虚构；总结来自本机回环的演示模型，不是真实 AI 效果。</p>"
                                       f"<ul>{links}</ul></body></html>\n", encoding="utf-8")
    summary = {"status": "ok", "output": str(output), "editions": result["editions"], "messages": result["stats"].get("messages"),
               "delivery": delivery and {"status": delivery["status"], "outbox": str(output / "outbox")}}
    _save_json(output / "demo-result.json", summary)
    return summary


def generate_qq_demo(output: Path) -> None:
    """Write fictional QQ renderer fixtures without touching QQ or a model."""
    output.mkdir(parents=True, exist_ok=True)
    assets = output / "assets"
    assets.mkdir(mode=0o755, exist_ok=True)
    image_path = assets / "garden-plan.png"
    _image(image_path)
    image_name = hashlib.sha256(image_path.read_bytes()).hexdigest() + ".png"
    asset_path = assets / image_name
    if asset_path != image_path:
        image_path.replace(asset_path)
    start = 1793145600
    end = start + 86400
    group_id, self_id = 1000000001, "1000000002"
    messages = [
        {"message_uid": "QQ-SYN-001", "author": "林小满", "author_id": "1000000101",
         "timestamp": start + 3600, "type": "text", "text": "虚构测试记录：周六上午整理社区花园工具。"},
        {"message_uid": "QQ-SYN-002", "author": "周禾", "author_id": "1000000102",
         "timestamp": start + 3900, "type": "image", "text": "[图片]",
         "image_sources": [f"assets/{image_name}"]},
        {"message_uid": "QQ-SYN-003", "author": "陈星", "author_id": "1000000103",
         "timestamp": start + 4200, "type": "text", "text": "我来准备标签，以上均为离线虚构内容。"},
    ]
    sender_names = {message["author_id"]: message["author"] for message in messages}
    token_totals = {identity: 0 for identity in sender_names}
    for message in messages:
        if message["type"] not in {"system", "recall"}:
            token_totals[message["author_id"]] += daily_report.body_token_count(message["text"])
    active_rank = [{"username": identity, "name": sender_names[identity], "tokens": token_totals[identity]}
                   for identity in sorted(token_totals, key=lambda key: (-token_totals[key], key))]
    common = {"chat_username": f"qq:{group_id}", "chat_display": "合成演示群（虚构）",
              "day": "2026-10-28", "range_text": "2026.10.27 17:00 — 2026.10.28 17:00",
              "generated_at": "离线合成示例", "stats": {"messages": 3, "members": 3, "images": 1},
              "rhythm_buckets": daily_report.build_rhythm_buckets(
                  (message["timestamp"] for message in messages), start, end),
              "leaders": {"talk": active_rank[0] if active_rank else {"name": "--", "tokens": 0},
                          "peak": {"name": "18:00", "count": 3}}, "participants": active_rank,
              "active_rank": active_rank,
              "quotes": [{"speaker": messages[2]["author"], "speaker_id": messages[2]["author_id"],
                          "text": messages[2]["text"], "source": dict(messages[2])}]}
    brief = {**common, "title_prefix": "QQ群日报", "edition": "brief",
             "one_line": "离线合成内容：群友安排了一次社区花园准备活动。",
             "topics": [{"topic": "周末准备", "summary": "群友计划整理花园工具并准备标签。",
                         "details": ["集合时间为周六上午。"],
                         "references": [{key: value for key, value in messages[0].items()}]}],
             }
    review = {**common, "title_prefix": "QQ群总结", "edition": "review",
              "one_line": "离线合成内容：活动准备分工已初步明确。",
              "topics": [{"topic": "时间与分工", "summary": "周六上午整理工具，陈星负责标签。",
                          "details": ["计划为虚构内容，不是实测聊天。"],
                          "references": [{key: value for key, value in message.items()} for message in messages]},
                         {"topic": "图示", "summary": "示意图为本地程序自制合成图。",
                          "details": [], "references": [dict(messages[1])]}],
              }
    for edition, report in (("brief", brief), ("review", review)):
        _save_json(output / f"qq-{edition}.json", report)
        (output / f"qq-{edition}.html").write_text(daily_report.render_html(report), encoding="utf-8")
        daily_report.render_png_pillow(report, output / f"qq-{edition}.png")
    _save_json(output / "qq-sources.json", {
        "synthetic_example": True, "group_id": group_id, "group_name": "合成演示群（虚构）",
        "self_id": self_id, "window": {"start_unix": start, "end_unix_exclusive": end},
        "messages": messages})
    index = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>虚构 QQ 离线样本</title>"
             "<body><h1>虚构 QQ 离线样本</h1><p>仅演示渲染，不是 AI 实测、QQ 导出或真实群内容。</p>"
             "<p><a href='qq-brief.html'>简报 HTML</a> · <a href='qq-brief.png'>简报 PNG</a></p>"
             "<p><a href='qq-review.html'>回顾 HTML</a> · <a href='qq-review.png'>回顾 PNG</a></p></body></html>\n")
    (output / "index.html").write_text(index, encoding="utf-8")



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, help="destination directory (default: ./chat-digest-demo, or ./qq-digest-demo with --qq)")
    parser.add_argument("--export", type=Path, default=DEFAULT_EXPORT, help="chat-export/v1 directory to read (default: bundled sample)")
    parser.add_argument("--no-send", action="store_true", help="skip the local demo sender")
    parser.add_argument("--write-fixture", type=Path, help="regenerate the bundled fictional export into this directory and exit")
    parser.add_argument("--qq", action="store_true", help="generate fictional QQ renderer fixtures only; no network or model")
    args = parser.parse_args()
    if args.write_fixture:
        write_fixture(args.write_fixture.resolve())
        print(json.dumps({"status": "ok", "fixture": str(args.write_fixture.resolve())}))
        return
    if args.qq:
        output = (args.output or Path("qq-digest-demo")).resolve()
        generate_qq_demo(output)
        print(f"Generated fictional QQ demo artifacts in {output}")
        return
    summary = generate((args.output or Path("chat-digest-demo")).resolve(), args.export.resolve(), send=not args.no_send)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
