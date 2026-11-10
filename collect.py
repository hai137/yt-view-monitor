#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""境外节点网络诊断：确认哪些 YouTube 端点能拿到真实数据。"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
code = os.environ.get("SHARE_CODE", "").strip()
CID = "UC_AglC5rzoBquyI3HYWHGyQ"
VID = "PeSNnT69zd8"
KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
COOKIE = "CONSENT=YES+cb.20210328-17-p0.en+FX+111; PREF=hl=zh-CN&gl=TW"
status = {
    "run_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
    "ok": False, "probes2": {},
}
opener = urllib.request.build_opener()


def probe(url, data=None, extra=None, cookie=True, timeout=15):
    h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if cookie:
        h["Cookie"] = COOKIE
    if extra:
        h.update(extra)
    req = urllib.request.Request(url, data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read(400000).decode("utf-8", "replace")
            low = raw.lower()
            flags = [k for k in ("ytinitialdata", "videoid", "viewcount",
                                 "ytinitialplayerresponse", "<feed", "<entry",
                                 "consent", "unusual traffic", "sign in",
                                 "not a bot") if k in low]
            return {"status": r.status, "len": len(raw),
                    "flags": flags, "head": raw[:260]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:90]}"}


player_body = json.dumps({
    "context": {"client": {"clientName": "WEB",
                           "clientVersion": "2.20241101.00.00",
                           "hl": "zh-CN", "gl": "TW"}},
    "videoId": VID, "contentCheckOk": True, "racyCheckOk": True,
}).encode()
browse_body = json.dumps({
    "context": {"client": {"clientName": "WEB",
                           "clientVersion": "2.20241101.00.00",
                           "hl": "zh-CN", "gl": "TW"}},
    "browseId": CID,
}).encode()
rss = "https://www.youtube.com/feeds/videos.xml?channel_id=" + CID
q = urllib.parse.quote(rss, safe="")
tests = [
    ("home", "https://www.youtube.com/", None, None),
    ("home_nocookie", "https://www.youtube.com/", None, None, False),
    ("channel", f"https://www.youtube.com/channel/{CID}/videos?hl=zh-CN", None, None),
    ("watch", f"https://www.youtube.com/watch?v={VID}&hl=zh-CN", None, None),
    ("player", "https://www.youtube.com/youtubei/v1/player?key=" + KEY,
     player_body, {"Content-Type": "application/json",
                   "Origin": "https://www.youtube.com"}),
    ("browse", "https://www.youtube.com/youtubei/v1/browse?key=" + KEY,
     browse_body, {"Content-Type": "application/json",
                   "Origin": "https://www.youtube.com"}),
    ("rss_cookie", rss, None, None),
    ("allorigins", "https://api.allorigins.win/raw?url=" + q, None, None),
    ("jina", "https://r.jina.ai/" + rss, None, None),
]
for t in tests:
    name, url, body, extra = t[0], t[1], t[2], t[3]
    ck = t[4] if len(t) > 4 else True
    res = probe(url, body, extra, ck)
    status["probes2"][name] = res
    print("PROBE", name, json.dumps(res, ensure_ascii=False)[:320], flush=True)

(ROOT / "status.json").write_text(
    json.dumps(status, ensure_ascii=False), encoding="utf-8")
print("DIAG_DONE", flush=True)
