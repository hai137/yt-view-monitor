#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""境外节点采集真实播放量，回传给云端看板服务渲染。

RSS 在数据中心 IP 上会被 YouTube 返回 404，因此依次尝试多个转发通道；
抓不到的数据跳过，绝不填估算值。
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path

import scraper

ROOT = Path(__file__).resolve().parent
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
code = os.environ.get("SHARE_CODE", "").strip()
KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
opener = urllib.request.build_opener()

status = {
    "run_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
    "ok": False,
    "secret_set": bool(code),
    "sources": {},
}


def http(url: str, data=None, extra=None, timeout=18) -> bytes:
    h = dict(HEADERS)
    if extra:
        h.update(extra)
    req = urllib.request.Request(url, data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    with opener.open(req, timeout=timeout) as r:
        return r.read()


def to_local(utc_str: str) -> str:
    try:
        d = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return d.astimezone(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    except Exception:
        return ""


def parse_rss(raw: bytes) -> list:
    root = ET.fromstring(raw.decode("utf-8", "replace"))
    out = []
    for e in root.findall(ATOM + "entry"):
        vid = e.findtext(YT + "videoId", "")
        if not vid:
            continue
        out.append({
            "id": vid,
            "title": e.findtext(ATOM + "title", "") or "(无标题)",
            "publishedAt": to_local(e.findtext(ATOM + "published", "")),
            "url": "https://www.youtube.com/watch?v=" + vid,
        })
    return out


def walk_renderers(node, out):
    if isinstance(node, dict):
        vr = node.get("videoRenderer") or node.get("gridVideoRenderer")
        if vr and vr.get("videoId"):
            title = "".join(r.get("text", "") for r in
                            vr.get("title", {}).get("runs", []))
            out.append({
                "id": vr["videoId"],
                "title": title or vr.get("title", {}).get("simpleText", "") or "(无标题)",
                "publishedAt": "",
                "url": "https://www.youtube.com/watch?v=" + vr["videoId"],
            })
        for v in node.values():
            walk_renderers(v, out)
    elif isinstance(node, list):
        for v in node:
            walk_renderers(v, out)


def from_channel_page(cid: str) -> list:
    raw = http(f"https://www.youtube.com/channel/{cid}/videos?hl=zh-CN",
               timeout=25).decode("utf-8", "replace")
    m = (re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", raw, re.S)
         or re.search(r'"ytInitialData"\s*:\s*(\{.*?\})\s*,\s*"ytInitialPlayerResponse"', raw, re.S))
    if not m:
        return []
    out, seen = [], set()
    walk_renderers(json.loads(m.group(1)), out)
    uniq = []
    for v in out:
        if v["id"] not in seen:
            seen.add(v["id"])
            uniq.append(v)
    return uniq[:15]


def rss_sources(cid: str):
    u = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
    q = urllib.parse.quote(u, safe="")
    return [
        ("direct", u),
        ("allorigins", "https://api.allorigins.win/raw?url=" + q),
        ("codetabs", "https://api.codetabs.com/v1/proxy?quest=" + q),
        ("corsproxy", "https://corsproxy.io/?url=" + q),
        ("jina", "https://r.jina.ai/" + u),
    ]


def videos_for(cid: str) -> tuple:
    for name, url in rss_sources(cid):
        try:
            raw = http(url, timeout=18)
            text = raw.decode("utf-8", "replace")
            status["sources"][cid + ":" + name] = f"200 {len(raw)}B"
            if "<feed" in text[:6000] or "<entry" in text[:6000]:
                vids = parse_rss(raw)
                if vids:
                    return vids[:15], name
        except Exception as e:
            status["sources"][cid + ":" + name] = \
                f"{type(e).__name__}: {str(e)[:60]}"
    try:
        vids = from_channel_page(cid)
        status["sources"][cid + ":channel_page"] = f"{len(vids)} videos"
        if vids:
            return vids, "channel_page"
    except Exception as e:
        status["sources"][cid + ":channel_page"] = \
            f"{type(e).__name__}: {str(e)[:60]}"
    return [], "none"


try:
    if not code:
        raise RuntimeError("缺少 SHARE_CODE 环境变量，请在仓库 Secrets 中配置")
    channels, videos, views, subs = {}, {}, {}, {}
    jobs = []
    for e in [x for x in cfg["channels"] if x.get("channel_id")]:
        cid = e["channel_id"].strip()
        vids, source = videos_for(cid)
        print(f"  {e['name']}：{len(vids)} 条视频，来源 {source}", flush=True)
        if not vids:
            raise RuntimeError(f"频道 {e['name']} 的视频列表抓取失败")
        try:
            page = scraper.channel_page(opener, cid)
            subs_n = page.get("subscribers") or 0
        except Exception:
            subs_n = 0
        channels[cid] = {
            "name": e["name"],
            "handle": (e.get("handle") or "").lstrip("@"),
            "subscribers": subs_n,
            "channelViews": "",
        }
        subs[cid] = subs_n
        for v in vids:
            videos[v["id"]] = {**v, "channelId": cid, "duration": ""}
            jobs.append(v["id"])

    pool = ThreadPoolExecutor(max_workers=8)
    try:
        futures = {pool.submit(scraper.views_for, opener, v, KEY): v for v in jobs}
        done, pending = wait(futures, timeout=150)
        for f in pending:
            f.cancel()
            videos.pop(futures[f], None)
        for f in done:
            try:
                vid, n = f.result()
            except Exception:
                vid, n = futures[f], None
            if n is None:
                videos.pop(vid, None)
            else:
                views[vid] = n
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if not views:
        raise RuntimeError("所有视频播放量都抓取失败，本次不回传")
    print(f"  共取得 {len(views)}/{len(jobs)} 条精确播放量", flush=True)

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
