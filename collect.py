#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""境外节点只读诊断：寻找无需住宅代理即可取得精确播放量的通道。

本版本不回传任何数据、不修改看板快照。
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VID = "PeSNnT69zd8"
CID = "UC_AglC5rzoBquyI3HYWHGyQ"
KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
BASE = {"User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
TZ = timezone(timedelta(hours=8))

res = {
    "run_at": datetime.now(TZ).isoformat(timespec="seconds"),
    "diag": True,
    "ok": False,
    "tests": {},
}


def fetch(url, data=None, headers=None, timeout=14):
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


def test(name, fn):
    try:
        status, body = fn()
        item = {"ok": True, "status": status,
                "len": len(body),
                "head": body[:220].replace("\n", " ")}
        counts = re.findall(r'"viewCount"\s*:\s*"(\d+)"', body)
        if counts:
            item["viewCount"] = counts[:3]
        texts = re.findall(
            r'"viewCountText"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"', body)
        if texts:
            item["viewCountText"] = texts[:3]
        res["tests"][name] = item
    except Exception as e:
        res["tests"][name] = {
            "ok": False,
            "error": type(e).__name__ + ": " + str(e)[:170],
        }


v3 = ("https://www.googleapis.com/youtube/v3/videos"
      "?part=statistics&id=" + VID + "&key=" + KEY)
v3_multi = ("https://www.googleapis.com/youtube/v3/videos"
            "?part=statistics&id=" + VID + ",PeSNnT69zd8&key=" + KEY)

test("egress_ip", lambda: fetch("https://api.ipify.org?format=json"))
test("v3_plain", lambda: fetch(v3))
test("v3_referer", lambda: fetch(
    v3, headers={"Referer": "https://www.youtube.com/",
                 "Origin": "https://www.youtube.com"}))
test("v3_xorigin", lambda: fetch(
    v3, headers={"X-Origin": "https://www.youtube.com",
                 "Referer": "https://www.youtube.com/"}))
test("v3_alt_host", lambda: fetch(
    "https://youtube.googleapis.com/youtube/v3/videos"
    "?part=statistics&id=" + VID + "&key=" + KEY,
    headers={"Referer": "https://www.youtube.com/"}))
test("v3_snippet", lambda: fetch(
    "https://www.googleapis.com/youtube/v3/videos?part=snippet&id="
    + VID + "&key=" + KEY))

ctx_web = {"context": {"client": {
    "clientName": "WEB", "clientVersion": "2.20241101.00.00",
    "hl": "zh-CN", "gl": "TW"}}}
ctx_android = {"context": {"client": {
    "clientName": "ANDROID", "clientVersion": "20.10.38",
    "androidSdkVersion": 30, "hl": "zh-CN", "gl": "US"}}}

test("next_web", lambda: fetch(
    "https://www.youtube.com/youtubei/v1/next?key=" + KEY,
    data=dict(ctx_web, videoId=VID, contentCheckOk=True,
              racyCheckOk=True),
    headers={"Origin": "https://www.youtube.com"}))
test("next_android", lambda: fetch(
    "https://www.youtube.com/youtubei/v1/next?key=" + KEY,
    data=dict(ctx_android, videoId=VID, contentCheckOk=True,
              racyCheckOk=True)))

test("m_watch", lambda: fetch(
    "https://m.youtube.com/watch?v=" + VID + "&hl=zh-CN"))
test("watch_verified", lambda: fetch(
    "https://www.youtube.com/watch?v=" + VID
    + "&bpctr=9999999999&has_verified=1&hl=zh-CN"))
test("nocookie_embed", lambda: fetch(
    "https://www.youtube-nocookie.com/embed/" + VID + "?hl=zh-CN"))

browse_body = dict(ctx_web)
browse_body["browseId"] = CID
browse_body["params"] = "EgZ2aWRlb3PyBgQKAjoA"
test("browse_videos", lambda: fetch(
    "https://www.youtube.com/youtubei/v1/browse?key=" + KEY,
    data=browse_body,
    headers={"Origin": "https://www.youtube.com"}))

(ROOT / "status.json").write_text(
    json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False)[:2500])
