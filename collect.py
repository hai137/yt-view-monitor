#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""境外节点采集真实精确播放量，回传云端看板服务渲染。

视频列表来自 youtubei/v1/browse；播放量来自 youtubei/v1/next 的
videoViewCountRenderer.viewCount.simpleText（精确到个位）。
抓不到就跳过，绝不填 0、绝不估算。
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
CODE = os.environ.get("SHARE_CODE", "").strip()
KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
HEADERS = {"User-Agent": UA,
           "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
           "Origin": "https://www.youtube.com"}
TZ = timezone(timedelta(hours=8))
WEB = {"context": {"client": {
    "clientName": "WEB", "clientVersion": "2.20241101.00.00",
    "hl": "zh-CN", "gl": "TW"}}}
BROWSE = ("https://www.youtube.com/youtubei/v1/browse?key=" + KEY
          + "&prettyPrint=false")
NEXT = ("https://www.youtube.com/youtubei/v1/next?key=" + KEY
        + "&prettyPrint=false")
LAST_SUBS = {
    "UC_AglC5rzoBquyI3HYWHGyQ": 7890,
    "UCmaMFmdJPY47IdGvCo3jSjw": 7830,
    "UCam5YvQCGc_18jERc34XGxQ": 3500,
}

status = {
    "run_at": datetime.now(TZ).isoformat(timespec="seconds"),
    "ok": False, "secret_set": bool(CODE), "channels": {},
    "views_ok": 0, "views_fail": 0, "push": None,
}


def http(url, data=None, timeout=25):
    h = dict(HEADERS)
    if data is not None:
        data = json.dumps(data).encode("utf-8")
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=data, headers=h,
        method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def text_of(x) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        if isinstance(x.get("simpleText"), str):
            return x["simpleText"]
        if isinstance(x.get("runs"), list):
            return "".join(r.get("text", "") for r in x["runs"]
                           if isinstance(r, dict))
    return ""


def walk(o):
    if isinstance(o, dict):
        vid = o.get("videoId")
        if isinstance(vid, str) and re.fullmatch(r"[\w-]{11}", vid) \
                and "title" in o:
            yield vid, text_of(o["title"])
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def browse_channel(cid):
    body = dict(WEB)
    body["browseId"] = cid
    body["params"] = "EgZ2aWRlb3PyBgQKAjoA"
    raw = http(BROWSE, body)
    found, seen = [], set()
    for vid, title in walk(json.loads(raw)):
        if vid not in seen and title:
            seen.add(vid)
            found.append((vid, title))
        if len(found) >= 15:
            break
    subs = None
    m = re.search(r'"subscriberCountText"\s*:\s*\{\s*"simpleText"\s*'
                  r':\s*"([\d,]+)\s*(?:位訂閱者|位订阅者|subscribers|訂閱者)"',
                  raw)
    if m:
        try:
            n = int(m.group(1).replace(",", ""))
            if n >= 100:
                subs = n
        except ValueError:
            pass
    return found, subs


VIEW_RE = re.compile(
    r'"videoViewCountRenderer"\s*:\s*\{\s*"viewCount"\s*:\s*\{\s*'
    r'"simpleText"\s*:\s*"([\d,]+)')
VIEW_RE2 = re.compile(
    r'"viewCount"\s*:\s*\{\s*"simpleText"\s*:\s*"([\d,]+)\s*'
    r'(?:次觀看|次观看|views)"')
DATE_RE = re.compile(r'"publishDate"\s*:\s*"([^"]+)"')


def fetch_one(vid):
    try:
        raw = http(NEXT, dict(WEB, videoId=vid, contentCheckOk=True,
                              racyCheckOk=True))
        m = VIEW_RE.search(raw) or VIEW_RE2.search(raw)
        if not m:
            return vid, None, ""
        n = int(m.group(1).replace(",", ""))
        d = DATE_RE.search(raw)
        return vid, n, d.group(1) if d else ""
    except Exception:
        return vid, None, ""


def main():
    channels, videos, subs, jobs = {}, {}, {}, []
    for c in cfg["channels"]:
        cid = c["channel_id"]
        try:
            found, sub = browse_channel(cid)
        except Exception as e:
            status["channels"][cid] = {"error": type(e).__name__
                                       + ": " + str(e)[:110]}
            continue
        channels[cid] = {
            "name": c["name"], "handle": c["handle"],
            "subscribers": sub if sub is not None
            else LAST_SUBS.get(cid, 0),
            "channelViews": "",
        }
        subs[cid] = channels[cid]["subscribers"]
        status["channels"][cid] = {"videos": len(found),
                                   "subscribers": subs[cid],
                                   "exact_subs": sub is not None}
        for vid, title in found:
            videos[vid] = {
                "id": vid, "title": title, "publishedAt": "",
                "url": "https://www.youtube.com/watch?v=" + vid,
                "channelId": cid, "duration": "",
            }
            jobs.append(vid)

    views = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for vid, n, pub in pool.map(fetch_one, jobs):
            if n is None or n < 0:
                status["views_fail"] += 1
                continue
            views[vid] = n
            status["views_ok"] += 1
            if pub:
                videos[vid]["publishedAt"] = pub
    if not views:
        status["error"] = "没有取得任何真实播放量，不回传"
        return

    payload = {"channels": channels, "videos": videos,
               "views": views, "subs": subs, "code": CODE}
    url = cfg["push_url"] + "?sharecode=" + urllib.parse.quote(CODE)
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "yt-monitor/1.0",
                 "X-Share-Code": CODE}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            status["push"] = {"status": r.status,
                              "body": r.read(220).decode("utf-8", "replace")}
            status["ok"] = r.status == 200
    except Exception as e:
        status["push"] = {"error": type(e).__name__ + ": " + str(e)[:170]}


try:
    main()
except Exception as e:
    status["error"] = type(e).__name__ + ": " + str(e)[:200]

(ROOT / "status.json").write_text(
    json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(status, ensure_ascii=False)[:1200])
