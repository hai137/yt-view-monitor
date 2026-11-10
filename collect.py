#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""境外节点采集真实播放量，回传给云端看板服务渲染。

本文件只负责抓数和传输，不生成任何估算值；抓不到就报错，绝不填假数。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
status = {
    "run_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
    "ok": False,
}

try:
    code = os.environ.get("SHARE_CODE", "").strip()
    if not code:
        raise RuntimeError("缺少 SHARE_CODE 环境变量，请在仓库 Secrets 中配置")
    import scraper
    channels, videos, views, subs = scraper.collect(cfg, logger=lambda m: print(m, flush=True))
    if not views:
        raise RuntimeError("本次没有取得任何真实播放量")
    payload = {"channels": channels, "videos": videos,
               "views": views, "subs": subs,
               "code": code}
    req = urllib.request.Request(
        cfg["push_url"],
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "yt-monitor/1.0",
                 "X-Share-Code": code},
        method="POST")
    with urllib.request.urlopen(req, timeout=45) as r:
        body = r.read(2000).decode("utf-8", "replace")
        if r.status != 200:
            raise RuntimeError(f"回传失败 HTTP {r.status}: {body[:200]}")
    status.update({"ok": True, "videos": len(views), "response": body[:200]})
    print(f"OK：{len(views)} 条真实播放量已回传")
except Exception as e:
    status["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    print("FAILED:", status["error"], flush=True)

(ROOT / "status.json").write_text(
    json.dumps(status, ensure_ascii=False), encoding="utf-8")
sys.exit(0 if status["ok"] else 1)
