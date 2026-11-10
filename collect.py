#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""境外节点只读诊断二：从真实响应里抠出播放量字段原文。

不回传数据、不修改看板快照。
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VID = "PeSNnT69zd8"
KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
BASE = {"User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
TZ = timezone(timedelta(hours=8))
res = {"run_at": datetime.now(TZ).isoformat(timespec="seconds"),
       "diag": 2, "ok": False, "tests": {}}

KEYS = ("viewCount", "viewCountText", "shortViewCountText",
        "videoViewCountRenderer", "interactionCount",
        "playabilityStatus", "videoDetails")


def fetch(url, data=None, headers=None, timeout=18):
    h = dict(BASE)
    if headers:
        h.update(headers)
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode("utf-8")
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(
        url, data=data, headers=h,
        method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def inspect(name, fn):
    try:
        status, body = fn()
        item = {"ok": True, "status": status, "len": len(body)}
        for kw in KEYS:
            n = body.count(kw)
            if not n:
                continue
            ctx = []
            for m in list(re.finditer(re.escape(kw), body))[:2]:
                i = m.start()
                ctx.append(body[max(0, i - 110):i + 230].replace("\n", " "))
            item[kw] = {"count": n, "ctx": ctx}
        digits = re.findall(r'"viewCount"\s*:\s*"(\d+)"', body)
        if digits:
            item["EXACT_VIEWCOUNT"] = digits[:3]
        res["tests"][name] = item
    except Exception as e:
        res["tests"][name] = {"ok": False,
                              "error": type(e).__name__ + ": " + str(e)[:170]}


web = {"context": {"client": {
    "clientName": "WEB", "clientVersion": "2.20241101.00.00",
    "hl": "zh-CN", "gl": "TW"}}}
android = {"context": {"client": {
    "clientName": "ANDROID", "clientVersion": "20.10.38",
    "androidSdkVersion": 30, "hl": "zh-CN", "gl": "US"}}}

inspect("next_web", lambda: fetch(
    "https://www.youtube.com/youtubei/v1/next?key=" + KEY,
    data=dict(web, videoId=VID, contentCheckOk=True, racyCheckOk=True),
    headers={"Origin": "https://www.youtube.com"}))
inspect("next_android", lambda: fetch(
    "https://www.youtube.com/youtubei/v1/next?key=" + KEY,
    data=dict(android, videoId=VID, contentCheckOk=True,
              racyCheckOk=True)))
inspect("m_watch", lambda: fetch(
    "https://m.youtube.com/watch?v=" + VID + "&hl=zh-CN"))
inspect("watch_verified", lambda: fetch(
    "https://www.youtube.com/watch?v=" + VID
    + "&bpctr=9999999999&has_verified=1&hl=zh-CN"))
inspect("nocookie_embed", lambda: fetch(
    "https://www.youtube-nocookie.com/embed/" + VID + "?hl=zh-CN"))

(ROOT / "status.json").write_text(
    json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print("diag2 done", len(json.dumps(res, ensure_ascii=False)))
