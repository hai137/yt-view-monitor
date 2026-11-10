#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""境外节点只读诊断三：精确字段 + 公共镜像。

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
       "diag": 3, "ok": False, "fields": {}, "mirrors": {}}


def fetch(url, data=None, headers=None, timeout=10):
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


web = {"context": {"client": {
    "clientName": "WEB", "clientVersion": "2.20241101.00.00",
    "hl": "zh-CN", "gl": "TW"}}}

try:
    _, body = fetch(
        "https://www.youtube.com/youtubei/v1/next?key=" + KEY,
        data=dict(web, videoId=VID, contentCheckOk=True,
                  racyCheckOk=True),
        headers={"Origin": "https://www.youtube.com"}, timeout=20)
    res["fields"]["len"] = len(body)
    for kw in ("videoViewCountRenderer", "viewCountFactoidRenderer",
               '"viewCount"', "shortViewCountText"):
        hits = list(re.finditer(re.escape(kw), body))
        res["fields"][kw] = {
            "count": len(hits),
            "ctx": [body[max(0, m.start() - 130):m.start() + 260]
                    .replace("\n", " ") for m in hits[:5]],
        }
    exact = re.findall(
        r'"viewCount"\s*:\s*"?([\d,]{4,})', body)
    res["fields"]["numeric_candidates"] = exact[:8]
    res["fields"]["chinese_counts"] = re.findall(
        r'([\d,]{4,})\s*(?:次觀看|次观看|views)', body)[:8]
except Exception as e:
    res["fields"]["error"] = type(e).__name__ + ": " + str(e)[:170]

MIRRORS = (
    ("inv_nadeko", "https://inv.nadeko.net/api/v1/videos/" + VID),
    ("yewtu", "https://yewtu.be/api/v1/videos/" + VID),
    ("inv_private_coffee",
     "https://invidious.private.coffee/api/v1/videos/" + VID),
    ("inv_projectsegfau",
     "https://inv.us.projectsegfau.lt/api/v1/videos/" + VID),
    ("inv_nerdvpn",
     "https://invidious.nerdvpn.de/api/v1/videos/" + VID),
    ("piped_adminforge",
     "https://pipedapi.adminforge.de/streams/" + VID),
    ("piped_kavin",
     "https://pipedapi.kavin.rocks/streams/" + VID),
    ("piped_reallyawesome",
     "https://pipedapi.reallyaweso.me/streams/" + VID),
    ("piped_darkness",
     "https://pipedapi-libre.kavin.rocks/streams/" + VID),
)

for name, url in MIRRORS:
    try:
        status, text = fetch(url, timeout=9)
        vc = re.findall(r'"viewCount"\s*:\s*"?(\d+)', text)
        res["mirrors"][name] = {
            "ok": True, "status": status, "len": len(text),
            "viewCount": vc[:2], "head": text[:130].replace("\n", " "),
        }
    except Exception as e:
        res["mirrors"][name] = {
            "ok": False, "error": type(e).__name__ + ": " + str(e)[:110],
        }

(ROOT / "status.json").write_text(
    json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print("diag3 done", len(json.dumps(res, ensure_ascii=False)))
