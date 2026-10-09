"""情報収集：RSS、気象庁の天気予報と地震情報。

ネットワークに出るのはこのファイルだけ。fixtures を渡すと、ネットに出ずに
手元の保存ファイルを読む（試運転用）。
"""
from __future__ import annotations

import email.utils
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

UA = "issantei/0.1 (personal character app)"
TIMEOUT = 20


@dataclass
class Item:
    title: str
    link: str
    published: datetime | None
    source: str          # 報じた媒体（NHK、朝日新聞…）。話題の大小を数える単位
    feed: str            # どのRSSから来たか
    lang: str = "ja"     # ja / en
    extra: dict = field(default_factory=dict)


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _text(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


_SUFFIX = re.compile(r"\s+-\s+([^-]{1,30})$")


def parse_feed(raw: bytes, feed: dict) -> list[Item]:
    """RSS 2.0 / RSS 1.0(RDF) / Atom のどれでも読む。"""
    root = ET.fromstring(raw)
    for el in root.iter():           # 名前空間を剥がして扱いやすくする
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    entries = root.findall(".//item") or root.findall(".//entry")
    default_source = feed["name"].split()[0] if feed.get("lang", "ja") == "ja" else feed["name"].rsplit(" ", 1)[0]
    items = []
    for e in entries:
        title = _text(e, "title")
        if not title:
            continue
        link = _text(e, "link")
        if not link:
            le = e.find("link")
            link = le.get("href", "") if le is not None else ""
        published = _parse_date(_text(e, "pubDate", "date", "published", "updated"))
        source = default_source
        if feed.get("aggregator"):
            src = _text(e, "source")
            m = _SUFFIX.search(title)
            if m:
                title = title[: m.start()].strip()
                source = src or m.group(1).strip()
            elif src:
                source = src
        items.append(Item(title, link, published, source, feed["name"], feed.get("lang", "ja")))
    return items


def fetch_news(config: dict, fixtures: Path | None = None) -> tuple[list[Item], list[str]]:
    items, errors = [], []
    for i, feed in enumerate(config["feeds"]):
        if not feed.get("enabled", True):
            continue
        try:
            if fixtures:
                p = fixtures / f"feed{i}.xml"
                if not p.exists():
                    continue
                raw = p.read_bytes()
            else:
                raw = _get(feed["url"])
            items.extend(parse_feed(raw, feed))
        except Exception as ex:  # 一つのRSSが落ちても全体は止めない
            errors.append(f"{feed['name']}: {ex}")
    return items, errors


# ---- 気象庁 -------------------------------------------------------------

JMA_FORECAST = "https://www.jma.go.jp/bosai/forecast/data/forecast/{code}.json"
JMA_QUAKES = "https://www.jma.go.jp/bosai/quake/data/list.json"

WEATHER_CHAR = {"1": "晴", "2": "曇", "3": "雨", "4": "雪"}


def fetch_weather(cfg: dict, fixtures: Path | None = None) -> dict | None:
    """今日の天気を {"char": "晴", "text": "晴れ 時々 くもり", ...} で返す。"""
    try:
        if fixtures:
            p = fixtures / "forecast.json"
            if not p.exists():
                return None
            data = json.loads(p.read_text("utf-8"))
        else:
            data = json.loads(_get(JMA_FORECAST.format(code=cfg["office_code"])))
        ts = data[0]["timeSeries"][0]
        area = next((a for a in ts["areas"] if a["area"]["name"] == cfg["area_name"]), ts["areas"][0])
        code = area["weatherCodes"][0]
        text = re.sub(r"\s+", " ", area["weathers"][0]).strip()
        return {"char": WEATHER_CHAR.get(code[0], "晴"), "text": text,
                "place": cfg.get("place_label", ""), "code": code}
    except Exception:
        return None


_INTENSITY_ORDER = ["1", "2", "3", "4", "5-", "5+", "6-", "6+", "7"]


def intensity_rank(s: str) -> int:
    return _INTENSITY_ORDER.index(s) if s in _INTENSITY_ORDER else -1


def fetch_quakes(since: datetime, fixtures: Path | None = None) -> list[dict]:
    """since 以降、最大震度3以上の地震。"""
    try:
        if fixtures:
            p = fixtures / "quakes.json"
            if not p.exists():
                return []
            data = json.loads(p.read_text("utf-8"))
        else:
            data = json.loads(_get(JMA_QUAKES))
    except Exception:
        return []
    out, seen = [], set()
    for q in data:
        at = _parse_date(q.get("at", ""))
        maxi = q.get("maxi", "")
        if not at or at < since or intensity_rank(maxi) < intensity_rank("3"):
            continue
        key = (q.get("at"), q.get("anm"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"at": at, "area": q.get("anm", ""), "mag": q.get("mag", ""), "maxi": maxi})
    return out
