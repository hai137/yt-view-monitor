#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""免 API Key 的真实采集：频道 RSS 取最近 15 条，player 接口取精确播放量。

抓不到的数据跳过，绝不填估算值。返回 (channels, videos, views, subs)。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path

# YouTube 官方 WEB 客户端的公开内建 key（非用户密钥，无需申请）
FALLBACK_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
CLIENT_VERSION = "2.20241101.00.00"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
ATOM = {"a": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015"}

_DEFAULT_KEY = FALLBACK_KEY
_log = print


def make_opener(proxy: str):
    opener = urllib.request.build_opener()
    if proxy:
        opener.add_handler(urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy}))
    return opener


COMMON_PROXY_PORTS = [
    "7890", "6598", "10809", "1080", "8080", "7891", "7897",
    "20171", "33210", "8889", "10801", "2080",
]


def _probe(proxy: str, cid: str, timeout=8) -> bool:
    try:
        raw = get(make_opener(proxy),
                  "https://www.youtube.com/feeds/videos.xml?channel_id=" + cid,
                  timeout=timeout)
        return b"<feed" in raw[:4000]
    except Exception:
        return False


def resolve_proxy(cfg: dict, cid: str, logger=print) -> str:
    """VPN 端口变了自动找可用线路，并写回 config.json。"""
    cur = cfg.get("proxy", "").strip()
    if _probe(cur, cid):
        return cur
    env = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
           or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "").strip()
    candidates = [""] + [f"http://127.0.0.1:{p}" for p in COMMON_PROXY_PORTS]
    if env:
        candidates.insert(0, env)
    for p in candidates:
        if p == cur or not _probe(p, cid):
            continue
        logger(f"  代理自动切换：{cur or '直连'} -> {p or '直连'}")
        cfg["proxy"] = p
        try:
            path = Path(__file__).resolve().parent / "config.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["proxy"] = p
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except Exception as e:
            logger(f"  代理写回失败（本次仍生效）：{e}")
        return p
    logger("  警告：未探测到可用代理，沿用原配置")
    return cur


def get(opener, url: str, timeout=25, data=None, extra=None) -> bytes:
    h = dict(HEADERS)
    if extra:
        h.update(extra)
    req = urllib.request.Request(url, data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    with opener.open(req, timeout=timeout) as r:
        return r.read()


def to_local(utc_str: str, offset_hours: float) -> str:
    """RSS 的 UTC 时间 -> 配置时区的 ISO 字符串。"""
    try:
        d = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return d.astimezone(timezone(timedelta(hours=offset_hours))).isoformat(
            timespec="seconds")
    except Exception:
        return ""


def fetch_rss(opener, cid: str, offset_hours: float) -> list:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
    raw = get(opener, url, timeout=25)
    root = ET.fromstring(raw.decode("utf-8", "replace"))
    out = []
    for e in root.findall("a:entry", ATOM):
        vid = e.findtext("yt:videoId", "", ATOM)
        if not vid:
            continue
        out.append({
            "id": vid,
            "title": e.findtext("a:title", "", ATOM) or "(无标题)",
            "publishedAt": to_local(e.findtext("a:published", "", ATOM),
                                    offset_hours),
            "url": "https://www.youtube.com/watch?v=" + vid,
        })
    return out


def channel_page(opener, cid: str) -> dict:
    """频道 about 页：拿订阅数、频道全量观看数、以及最新的内建 key。"""
    global _DEFAULT_KEY
    out = {"subscribers": None, "channelViews": "", "key": ""}
    try:
        raw = get(opener, f"https://www.youtube.com/channel/{cid}/about?hl=zh-CN",
                  timeout=25).decode("utf-8", "replace")
        m = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"', raw)
        if m:
            out["key"] = m.group(1)
            _DEFAULT_KEY = m.group(1)
        m = re.search(r'"subscriberCountText"\s*:\s*"([^"]+)"', raw)
        if m:
            digits = re.sub(r"[^\d.]", "", m.group(1).split("位")[0])
            if digits:
                out["subscribers"] = int(float(digits))
        m = re.search(r'"viewCountText"\s*:\s*"([^"]+)"', raw)
        if m:
            out["channelViews"] = m.group(1)
    except Exception as e:
        _log(f"  频道页读取失败 {cid}: {type(e).__name__}: {e}")
    return out


CLIENTS = (
    ("WEB", CLIENT_VERSION, {"hl": "zh-CN", "gl": "TW"}),
    ("ANDROID", "20.10.38", {"hl": "zh-CN", "gl": "US", "androidSdkVersion": 30}),
    ("TVHTML5", "7.20241101.18.00", {"hl": "zh-CN", "gl": "US"}),
)


def player_views(opener, vid: str, key: str) -> int | None:
    url = f"https://www.youtube.com/youtubei/v1/player?key={key}"
    for name, version, extra_client in CLIENTS:
        client = {"clientName": name, "clientVersion": version}
        client.update(extra_client)
        body = json.dumps({
            "context": {"client": client},
            "videoId": vid, "contentCheckOk": True, "racyCheckOk": True,
        }).encode()
        try:
            d = json.loads(get(opener, url, timeout=12, data=body, extra={
                "Content-Type": "application/json",
                "Origin": "https://www.youtube.com",
            }).decode("utf-8", "replace"))
            v = d.get("videoDetails", {}).get("viewCount")
            if v and str(v).isdigit():
                return int(v)
        except Exception:
            continue
    return None


def watch_views(opener, vid: str) -> int | None:
    """player 接口失效时的回退：watch 页里的 videoDetails.viewCount。"""
    try:
        raw = get(opener, f"https://www.youtube.com/watch?v={vid}&hl=zh-CN",
                  timeout=15).decode("utf-8", "replace")
        m = re.search(r'"viewCount"\s*:\s*"(\d+)"', raw)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def embed_views(opener, vid: str) -> int | None:
    """最后一道回退：嵌入页里的 playerResponse 同样带精确播放量。"""
    try:
        raw = get(opener, f"https://www.youtube.com/embed/{vid}?hl=zh-CN",
                  timeout=15).decode("utf-8", "replace")
        m = re.search(r'"viewCount"\s*:\s*"(\d+)"', raw)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def views_for(opener, vid: str, key: str) -> tuple:
    n = player_views(opener, vid, key)
    if n is None:
        n = watch_views(opener, vid)
    if n is None:
        n = embed_views(opener, vid)
    return vid, n


def collect(cfg: dict, logger=print) -> tuple:
    global _log
    _log = logger
    offset = float(cfg.get("timezone_offset_hours", 8))
    entries = [e for e in cfg.get("channels", []) if e.get("channel_id")]
    if not entries:
        raise RuntimeError("config.json 里没有填 channel_id，无法采集")
    env_proxy = os.environ.get("YT_PROXY", "").strip()
    if env_proxy:
        proxy = env_proxy
    elif cfg.get("cloud_mode"):
        proxy = ""
    else:
        proxy = resolve_proxy(cfg, entries[0]["channel_id"].strip(), logger)
    opener = make_opener(proxy)

    channels, videos, views, subs = {}, {}, {}, {}
    jobs = []

    for e in entries:
        cid = e["channel_id"].strip()
        rss = fetch_rss(opener, cid, offset)
        if not rss:
            raise RuntimeError(f"频道 {e.get('name')} 的 RSS 为空，代理可能不可用")
        page = channel_page(opener, cid)
        channels[cid] = {
            "name": e.get("name") or cid,
            "handle": (e.get("handle") or "").lstrip("@"),
            "subscribers": page["subscribers"] or 0,
            "channelViews": page["channelViews"],
        }
        subs[cid] = page["subscribers"] or 0
        for v in rss:
            videos[v["id"]] = {**v, "channelId": cid, "duration": ""}
            jobs.append(v["id"])
        logger(f"  {e.get('name')}：RSS {len(rss)} 条 · 订阅 {page['subscribers']}")

    key = _DEFAULT_KEY or FALLBACK_KEY
    pool = ThreadPoolExecutor(max_workers=8)
    try:
        futures = {pool.submit(views_for, make_opener(proxy), v, key): v for v in jobs}
        done, pending = wait(futures, timeout=150)
        for f in pending:
            f.cancel()
            logger(f"  跳过 {futures[f]}：采集超时（不填估算值）")
            videos.pop(futures[f], None)
        for f in done:
            try:
                vid, n = f.result()
            except Exception:
                vid, n = futures[f], None
            if n is None:
                logger(f"  跳过 {vid}：播放量抓取失败（不填估算值）")
                videos.pop(vid, None)
            else:
                views[vid] = n
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if not views:
        raise RuntimeError("所有视频播放量都抓取失败，本次不写入快照")
    logger(f"  共取得 {len(views)}/{len(jobs)} 条精确播放量")
    return channels, videos, views, subs
