#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""境外节点采集真实播放量，回传给云端看板服务渲染。

优先用 yt-dlp（自动处理 YouTube 反爬与多客户端），失败再回退到
youtubei/v1/browse + player 多客户端。抓不到的数据跳过，绝不填估算值。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
code = os.environ.get("SHARE_CODE", "").strip()
KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
status = {
    "run_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
    "ok": False, "secret_set": bool(code), "channels": {},
}
opener = urllib.request.build_opener()
TZ = timezone(timedelta(hours=8))


def http(url, data=None, extra=None, timeout=20) -> bytes:
    h = dict(HEADERS)
    if extra:
        h.update(extra)
    req = urllib.request.Request(url, data=data, headers=h,
                                 method="POST" if data is not None else "GET")
    with opener.open(req, timeout=timeout) as r:
        return r.read()


def iso_from_unix(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), TZ).isoformat(timespec="seconds")
    except Exception:
        return ""


def iso_from_date(s: str) -> str:
    try:
        return datetime.strptime(s, "%Y%m%d").replace(tzinfo=TZ).isoformat(
            timespec="seconds")
    except Exception:
        return ""


def ensure_ytdlp():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "--quiet", "yt-dlp"], timeout=180)
        import yt_dlp
        return yt_dlp


CLIENT_SETS = (("android_vr",), ("tv_embedded",), ("web_safari",),
               ("android", "web"), ("web",))


def via_ytdlp(cid, ydl_mod):
    last = ""
    for clients in CLIENT_SETS:
        opts = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "playlistend": 15, "ignoreerrors": True, "noplaylist": False,
            "extract_flat": False,
            "extractor_args": {"youtube": {"player_client": list(clients)}},
        }
        try:
            with ydl_mod.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/channel/{cid}/videos",
                    download=False)
            entries = [e for e in (info or {}).get("entries", []) if e][:15]
            good = sum(1 for e in entries if isinstance(e.get("view_count"), int))
            status["channels"].setdefault(cid, {})["ytdlp:" + "+".join(clients)] = \
                f"{len(entries)} entries / {good} views"
            if entries and good:
                return entries, info or {}
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:100]}"
            status["channels"].setdefault(cid, {})["ytdlp:" + "+".join(clients)] = last
    raise RuntimeError("yt-dlp 未能取得播放量" + (f"：{last}" if last else ""))


def via_browse(cid):
    """回退：browse 拿列表，player 多客户端 / watch / embed 拿播放量。"""
    body = json.dumps({
        "context": {"client": {"clientName": "WEB",
                               "clientVersion": "2.20241101.00.00",
                               "hl": "zh-CN", "gl": "TW"}},
        "browseId": cid,
    }).encode()
    raw = http("https://www.youtube.com/youtubei/v1/browse?key=" + KEY,
               data=body, extra={"Content-Type": "application/json",
                                 "Origin": "https://www.youtube.com"},
               timeout=25).decode("utf-8", "replace")
    ids, seen = [], set()
    for m in re.finditer(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', raw):
        v = m.group(1)
        if v not in seen:
            seen.add(v)
            ids.append(v)
    ids = ids[:15]
    if not ids:
        raise RuntimeError("browse 未返回 videoId")
    import scraper
    from concurrent.futures import ThreadPoolExecutor, wait
    out = []
    pool = ThreadPoolExecutor(max_workers=8)
    try:
        fs = {pool.submit(scraper.views_for, opener, v, KEY): v for v in ids}
        done, pending = wait(fs, timeout=150)
        for f in pending:
            f.cancel()
        for f in done:
            try:
                vid, n = f.result()
            except Exception:
                continue
            if isinstance(n, int) and n >= 0:
                out.append({"id": vid, "title": "(无标题)", "view_count": n})
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    status["channels"].setdefault(cid, {})["browse"] = \
        f"{len(ids)} ids / {len(out)} views"
    return out, {}


try:
    if not code:
        raise RuntimeError("缺少 SHARE_CODE 环境变量，请在仓库 Secrets 中配置")
    try:
        ydl_mod = ensure_ytdlp()
    except Exception as e:
        ydl_mod = None
        status["ytdlp_install"] = f"{type(e).__name__}: {str(e)[:100]}"

    channels, videos, views, subs = {}, {}, {}, {}
    for e in [x for x in cfg["channels"] if x.get("channel_id")]:
        cid = e["channel_id"].strip()
        if ydl_mod:
            try:
                entries, info = via_ytdlp(cid, ydl_mod)
                source = "ytdlp"
            except Exception as ex:
                status["channels"].setdefault(cid, {})["ytdlp"] = \
                    f"FAIL {type(ex).__name__}: {str(ex)[:100]}"
                entries, info = via_browse(cid)
                source = "browse"
        else:
            entries, info = via_browse(cid)
            source = "browse"
        print(f"  {e['name']}：{len(entries)} 条，来源 {source}", flush=True)
        channels[cid] = {
            "name": e["name"],
            "handle": (e.get("handle") or "").lstrip("@"),
            "subscribers": int(info.get("channel_follower_count") or 0),
            "channelViews": "",
        }
        subs[cid] = channels[cid]["subscribers"]
        for x in entries:
            vid = x.get("id")
            n = x.get("view_count")
            if not vid or not isinstance(n, int) or n < 0:
                continue
            ts = (iso_from_unix(x.get("timestamp"))
                  or iso_from_date(x.get("upload_date") or ""))
            videos[vid] = {
                "id": vid, "title": x.get("title") or "(无标题)",
                "publishedAt": ts,
                "url": "https://www.youtube.com/watch?v=" + vid,
                "channelId": cid, "duration": "",
            }
            views[vid] = n

    if not views:
        raise RuntimeError("所有视频播放量都抓取失败，本次不回传")
    print(f"  共取得 {len(views)} 条精确播放量", flush=True)

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
