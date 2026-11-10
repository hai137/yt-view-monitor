#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""境外节点采集真实播放量，回传给云端看板服务渲染。

视频列表来自 youtubei/v1/browse，播放量依次尝试多个官方 player 客户端
（含 ANDROID_VR）与 watch/embed 页面；抓不到就跳过，绝不填估算值。
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
code = os.environ.get("SHARE_CODE", "").strip()
KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
TZ = timezone(timedelta(hours=8))
opener = urllib.request.build_opener()
status = {
    "run_at": datetime.now(TZ).isoformat(timespec="seconds"),
    "ok": False, "secret_set": bool(code), "client_hits": {}, "channels": {},
}

CLIENTS = (
    ("ANDROID_VR", "1.60.19",
     {"androidSdkVersion": 32, "hl": "zh-CN", "gl": "US"}),
    ("TVHTML5_SIMPLY_EMBEDDED_PLAYER", "2.0",
     {"clientScreen": "EMBED", "hl": "zh-CN", "gl": "US"}),
    ("WEB_EMBEDDED_PLAYER", "1.20241009.01.00",
     {"clientScreen": "EMBED", "hl": "zh-CN", "gl": "TW"}),
    ("ANDROID", "20.10.38",
     {"androidSdkVersion": 30, "hl": "zh-CN", "gl": "US"}),
    ("IOS", "20.10.4",
     {"deviceMake": "Apple", "deviceModel": "iPhone16,2",
      "osName": "iPhone", "osVersion": "18.3.1.22D72",
      "hl": "zh-CN", "gl": "US"}),
    ("TVHTML5", "7.20241101.18.00", {"hl": "zh-CN", "gl": "US"}),
    ("WEB", "2.20241101.00.00", {"hl": "zh-CN", "gl": "TW"}),
)


def http(url, data=None, extra=None, timeout=15) -> bytes:
    h = dict(HEADERS)
    if extra:
        h.update(extra)
    req = urllib.request.Request(url, data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    with opener.open(req, timeout=timeout) as r:
        return r.read()


def parse_browse(raw: bytes) -> list:
    data = json.loads(raw.decode("utf-8", "replace"))
    out, seen = [], set()

    def walk(node):
        if isinstance(node, dict):
            vr = node.get("videoRenderer") or node.get("gridVideoRenderer")
            if vr and vr.get("videoId"):
                vid = vr["videoId"]
                title = ("".join(r.get("text", "") for r in
                                 vr.get("title", {}).get("runs", []))
                         or vr.get("title", {}).get("simpleText", ""))
                pub = (vr.get("publishedTimeText", {}).get("simpleText", "")
                       or "")
                if vid not in seen:
                    seen.add(vid)
                    out.append({"id": vid, "title": title or "(无标题)",
                                "pub": pub})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out[:15]


def approx_time(txt: str) -> str:
    if not txt:
        return ""
    m = re.search(r"(\d+)\s*(天|日|小時|小时|週|周|個月|个月|年|分|分鐘|分钟)", txt)
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2)
    if unit in ("天", "日"):
        delta = timedelta(days=n)
    elif unit in ("週", "周"):
        delta = timedelta(weeks=n)
    elif unit in ("個月", "个月"):
        delta = timedelta(days=30 * n)
    elif unit == "年":
        delta = timedelta(days=365 * n)
    elif unit in ("小時", "小时"):
        delta = timedelta(hours=n)
    else:
        delta = timedelta(minutes=n)
    return (datetime.now(TZ) - delta).isoformat(timespec="seconds")


def browse_entries(cid: str) -> list:
    body = json.dumps({
        "context": {"client": {"clientName": "WEB",
                               "clientVersion": "2.20241101.00.00",
                               "hl": "zh-CN", "gl": "TW"}},
        "browseId": cid,
    }).encode()
    raw = http("https://www.youtube.com/youtubei/v1/browse?key=" + KEY,
               data=body, extra={"Content-Type": "application/json",
                                 "Origin": "https://www.youtube.com"},
               timeout=25)
    return parse_browse(raw)


def one_view(vid: str):
    for name, ver, extra in CLIENTS:
        client = {"clientName": name, "clientVersion": ver}
        client.update(extra)
        body = json.dumps({
            "context": {"client": client},
            "videoId": vid, "contentCheckOk": True, "racyCheckOk": True,
        }).encode()
        try:
            raw = http("https://www.youtube.com/youtubei/v1/player?key=" + KEY,
                       data=body, extra={"Content-Type": "application/json",
                                         "Origin": "https://www.youtube.com"},
                       timeout=12)
            d = json.loads(raw.decode("utf-8", "replace"))
            v = d.get("videoDetails", {}).get("viewCount")
            if v and str(v).isdigit():
                return vid, int(v), name
        except Exception:
            continue
    for kind, url in (("watch", f"https://www.youtube.com/watch?v={vid}&hl=zh-CN"),
                      ("embed", f"https://www.youtube.com/embed/{vid}?hl=zh-CN")):
        try:
            txt = http(url, timeout=14).decode("utf-8", "replace")
            m = re.search(r'"viewCount"\s*:\s*"(\d+)"', txt)
            if m:
                return vid, int(m.group(1)), kind
        except Exception:
            continue
    return vid, None, "none"


try:
    if not code:
        raise RuntimeError("缺少 SHARE_CODE 环境变量，请在仓库 Secrets 中配置")
    channels, videos, subs = {}, {}, {}
    jobs = []
    for e in [x for x in cfg["channels"] if x.get("channel_id")]:
        cid = e["channel_id"].strip()
        entries = browse_entries(cid)
        status["channels"][cid] = f"browse {len(entries)}"
        print(f"  {e['name']}：browse {len(entries)} 条", flush=True)
        if not entries:
            raise RuntimeError(f"频道 {e['name']} 的视频列表为空")
        channels[cid] = {
            "name": e["name"],
            "handle": (e.get("handle") or "").lstrip("@"),
            "subscribers": 0,
            "channelViews": "",
        }
        subs[cid] = 0
        for x in entries:
            videos[x["id"]] = {
                "id": x["id"], "title": x["title"],
                "publishedAt": approx_time(x["pub"]),
                "url": "https://www.youtube.com/watch?v=" + x["id"],
                "channelId": cid, "duration": "",
            }
            jobs.append(x["id"])

    views = {}
    pool = ThreadPoolExecutor(max_workers=8)
    try:
        fs = {pool.submit(one_view, v): v for v in jobs}
        done, pending = wait(fs, timeout=210)
        for f in pending:
            f.cancel()
        for f in done:
            try:
                vid, n, src = f.result()
            except Exception:
                continue
            status["client_hits"][src] = status["client_hits"].get(src, 0) + 1
            if isinstance(n, int) and n >= 0:
                views[vid] = n
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    print(f"  播放量 {len(views)}/{len(jobs)}", flush=True)
    if not views:
        raise RuntimeError("所有视频播放量都抓取失败，本次不回传")

    payload = {"channels": channels, "videos": videos,
               "views": views, "subs": subs, "code": code}
    push_url = cfg["push_url"] + "?sharecode=" + urllib.parse.quote(code, safe="")
    req = urllib.request.Request(
        push_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "yt-monitor/1.0",
                 "X-Share-Code": code},
        method="POST")
    with urllib.request.urlopen(req, timeout=45) as r:
        body = r.read(2000).decode("utf-8", "replace")
        if r.status != 200:
            raise RuntimeError(f"回传失败 HTTP {r.status}: {body[:200]}")
    status.update({"ok": True, "videos": len(videos),
                   "views": len(views), "response": body[:200]})
    print(f"OK：{len(views)} 条真实播放量已回传")
except Exception as e:
    url = getattr(e, "url", "") or ""
    where = urllib.parse.urlparse(url).path if url else ""
    status["error"] = (f"{type(e).__name__}: {str(e)[:160]}"
                       + (f" @ {where}" if where else ""))
    print("FAILED:", status["error"], flush=True)

(ROOT / "status.json").write_text(
    json.dumps(status, ensure_ascii=False), encoding="utf-8")
sys.exit(0 if status["ok"] else 1)
