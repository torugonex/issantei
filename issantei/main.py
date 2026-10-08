"""一傘亭の一刻み。15分ごとに呼ばれ、呟くかどうか決めて、呟くなら一つ書き足す。

  python -m issantei.main                 # 本番（ANTHROPIC_API_KEY が必要）
  python -m issantei.main --dry-run       # APIを呼ばない試運転
  python -m issantei.main --dry-run --fixtures tests/fixtures --now 2026-10-08T09:00
"""
from __future__ import annotations

import argparse
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import collect, season, topics
from .generate import ClaudeLLM, FakeLLM, generate

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parent.parent
TWEETS = ROOT / "docs" / "data" / "tweets.json"
STATE = ROOT / "state" / "state.json"
CONFIG = ROOT / "config" / "feeds.json"

POST_PROB = 0.85        # 起きている間、一刻み（15分）ごとに呟く確率。1時間で3〜4回
SARCASM_GAP = 4         # 皮肉のあと、これだけ呟くまでは次の皮肉を控える（5回に1回程度）
WAKE, SLEEP = 6, 23     # 6時に起き、23時に寝る
KEEP = 400              # tweets.json に残す件数

DEFAULT_STATE = {"mood": {"valence": 0.2, "arousal": -0.1}, "since_sarcasm": SARCASM_GAP,
                 "day": "", "morning_done": False, "goodnight_done": False,
                 "sleeptalk_night": "", "commented": {}, "last_quake_check": ""}


def load(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def decide_scene(now: datetime, st: dict, rng: random.Random) -> str | None:
    h, m = now.hour, now.minute
    if h >= SLEEP or h < WAKE:                                  # 就寝中
        night = (now - timedelta(hours=WAKE)).date().isoformat()
        if 1 <= h < 5 and st["sleeptalk_night"] != night:
            if rng.random() < 0.12 or (h == 4 and m >= 30):   # 夜中に一度だけ寝言
                return "sleeptalk"
        return None
    if not st["morning_done"]:
        return "morning"
    if h == SLEEP - 1 and m >= 40 and not st["goodnight_done"]:
        return "goodnight"
    if rng.random() > POST_PROB:
        return None
    if now.weekday() == 4 and h >= 19 and rng.random() < 0.5:  # 金曜の夜はバー
        return "bar"
    return "regular"


def topic_payload(t: topics.Topic) -> dict:
    return {"key": t.key, "title": t.title, "weight": t.weight, "grave": t.grave,
            "headlines": sorted({i.title for i in t.items}, key=len)[:4], "links": t.links()}


def pick_topic(now, st, items, quakes, rng):
    """話題を選ぶ。新しい地震 → 大きな話題 → 小さな話題 → 暮らし の順に、重みをつけて。"""
    for q in quakes:
        key = f"quake-{q['at']:%Y%m%d%H%M}"
        if key not in st["commented"]:
            grave = collect.intensity_rank(q["maxi"]) >= collect.intensity_rank("6-")
            return {"key": key, "title": f"{q['area']}で地震", "weight": 1, "grave": grave,
                    "headlines": [f"{q['area']}で地震、M{q['mag']}、最大震度{q['maxi']}"], "links": []}
    fresh = [i for i in items if i.published is None or now - i.published < timedelta(hours=18)]
    ts = [t for t in topics.cluster(fresh) if t.key not in st["commented"]]
    big = [t for t in ts if t.weight >= 2]
    small = [t for t in ts if t.weight == 1]
    r = rng.random()
    if big and (r < 0.55 or not small):
        # 大きいほど選ばれやすいが、いつも一番とは限らない
        return topic_payload(rng.choices(big[:8], weights=[t.weight for t in big[:8]])[0])
    if small and r < 0.75:
        return topic_payload(rng.choice(small[:30]))
    return None   # 暮らしの呟き


def tick(now: datetime, llm, fixtures: Path | None = None, force: bool = False) -> dict | None:
    st = {**DEFAULT_STATE, **load(STATE, {})}
    tweets = load(TWEETS, [])
    rng = random.Random(int(now.timestamp()))

    today = now.date().isoformat()
    if st["day"] != today and now.hour >= WAKE:            # 新しい一日
        st.update(day=today, morning_done=False, goodnight_done=False)
        # 一晩眠ると気分は少し平らに戻る
        st["mood"] = {k: round(v * 0.5 + DEFAULT_STATE["mood"][k] * 0.5, 3) for k, v in st["mood"].items()}

    scene = decide_scene(now, st, rng)
    if scene is None and force and WAKE <= now.hour < SLEEP:
        scene = "regular"
    if scene is None:
        save(STATE, st)
        return None

    cfg = load(CONFIG, {})
    ctx = {"now": now, "scene": scene, "season": season.season_info(now), "mood": st["mood"],
           "sarcasm_allowed": st["since_sarcasm"] >= SARCASM_GAP,
           "recent": [t["text"] for t in tweets[-8:]]}

    if scene in ("morning", "regular", "bar"):
        ctx["weather"] = collect.fetch_weather(cfg.get("weather", {}), fixtures)
    if scene in ("regular", "bar"):
        last = st["last_quake_check"]
        since = datetime.fromisoformat(last) if last else now - timedelta(hours=3)
        quakes = collect.fetch_quakes(since, fixtures)
        items, errors = collect.fetch_news(cfg, fixtures)
        st["last_quake_check"] = now.isoformat()
        ctx["topic"] = pick_topic(now, st, items, quakes, rng)
        if scene == "regular" and ctx["topic"] is None:
            ctx["scene"] = scene = "life"
        if errors:
            print("RSS取得の失敗:", *errors, sep="\n  ")

    d = generate(llm, ctx)

    # 気分は少しずつしか動かない（慣性）
    w = 0.25 if d["severity"] < 3 else 0.5
    st["mood"] = {"valence": round(st["mood"]["valence"] * (1 - w) + d["valence"] * w, 3),
                  "arousal": round(st["mood"]["arousal"] * (1 - w) + d["arousal"] * w, 3)}
    st["since_sarcasm"] = 0 if d["expression"] == "片眉を上げる" else st["since_sarcasm"] + 1
    if scene == "morning":
        st["morning_done"] = True
    elif scene == "goodnight":
        st["goodnight_done"] = True
    elif scene == "sleeptalk":
        st["sleeptalk_night"] = (now - timedelta(hours=WAKE)).date().isoformat()
    topic = ctx.get("topic")
    if topic:
        st["commented"][topic["key"]] = now.isoformat()
    cutoff = (now - timedelta(days=2)).isoformat()
    st["commented"] = {k: v for k, v in st["commented"].items() if v > cutoff}

    rec = {"at": now.isoformat(timespec="minutes"), "text": d["text"], "expression": d["expression"],
           "scene": scene, "topic": topic["title"] if topic else None,
           "links": topic["links"] if topic else []}
    tweets.append(rec)
    save(TWEETS, tweets[-KEEP:])
    save(STATE, st)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="APIを呼ばずに試す")
    ap.add_argument("--fixtures", type=Path, help="ネットに出ず、保存したRSS等を読む")
    ap.add_argument("--now", help="時刻を指定（例 2026-10-08T09:00）")
    ap.add_argument("--force", action="store_true", help="確率に関係なく呟かせる（起きている時間のみ）")
    a = ap.parse_args()
    now = datetime.fromisoformat(a.now).replace(tzinfo=JST) if a.now else datetime.now(JST)
    # GitHubの「Run workflow」で手動実行したときは、確かめやすいよう必ず呟かせる
    force = a.force or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    rec = tick(now, FakeLLM() if a.dry_run else ClaudeLLM(), a.fixtures, force)
    print(json.dumps(rec, ensure_ascii=False) if rec else f"{now:%H:%M} 今回は呟かない")


if __name__ == "__main__":
    main()
