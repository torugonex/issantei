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
ENGLISH_EVERY = (3, 5)  # 英語で呟く間隔。3〜5回に1回
# 古風な決まり文句。直近の日本語の呟きで使ったものは、しばらく使わせない
MANNERISMS = ["とな", "なり", "そうな", "よろしい", "けり", "ものだ"]
MANNERISM_WINDOW = 4
# 弔意の言葉。直近3回の「憂い」の呟きで使ったものは、次に使わせない
CONDOLENCES = ["黙祷", "安らかなれ", "言葉もない", "冥福", "合掌",
               "Rest in peace", "Requiescat", "Paix à", "Repose en paix"]
CONDOLENCE_WINDOW = 3
WAKE, SLEEP = 6, 23     # 6時に起き、23時に寝る
KEEP = 400              # tweets.json に残す件数

DEFAULT_STATE = {"mood": {"valence": 0.2, "arousal": -0.1}, "since_sarcasm": SARCASM_GAP,
                 "day": "", "morning_done": False, "goodnight_done": False,
                 "sleeptalk_night": "", "commented": {}, "commented_titles": [], "last_quake_check": "",
                 "since_english": 0, "next_english": 4}


def load(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def post_prob(now: datetime, st: dict) -> float:
    """前回の起動から間が空いたぶん、呟く確率を上げる（GitHub側の遅れや取りこぼしを埋める）。"""
    try:
        gap = (now - datetime.fromisoformat(st["last_tick"])).total_seconds() / 60
    except (KeyError, TypeError, ValueError):
        gap = 15
    gap = max(5.0, min(gap, 60.0))
    return 1 - (1 - POST_PROB) ** (gap / 15)


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
    if rng.random() > post_prob(now, st):
        return None
    if now.weekday() == 4 and h >= 19 and rng.random() < 0.5:  # 金曜の夜はバー
        return "bar"
    return "regular"


def topic_payload(t: topics.Topic) -> dict:
    return {"key": t.key, "title": t.title, "weight": t.weight, "grave": t.grave,
            "headlines": sorted({i.title for i in t.items}, key=len)[:4], "links": t.links()}


def pick_topic(now, st, items, quakes, rng, lang="ja"):
    """話題を選ぶ。新しい地震 → 大きな話題 → 小さな話題 → 暮らし の順に、重みをつけて。
    英語の回は英語のRSSから選ぶ（地震は日本語の回で扱う）。"""
    items = [i for i in items if i.lang == lang]
    for q in (quakes if lang == "ja" else []):
        key = f"quake-{q['at']:%Y%m%d%H%M}"
        if key not in st["commented"]:
            grave = collect.intensity_rank(q["maxi"]) >= collect.intensity_rank("6-")
            return {"key": key, "title": f"{q['area']}で地震", "weight": 1, "grave": grave,
                    "headlines": [f"{q['area']}で地震、M{q['mag']}、最大震度{q['maxi']}"], "links": []}
    fresh = [i for i in items if i.published is None or now - i.published < timedelta(hours=18)]
    # 見出しの言い回しが変わっただけの同じ出来事（続報）は、一日のうちは繰り返さない
    seen = [topics.grams_of(x["title"]) for x in st.get("commented_titles", [])]
    ts = [t for t in topics.cluster(fresh)
          if t.key not in st["commented"] and not any(topics.similar(t.grams, g) for g in seen)]
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
    st["last_tick"] = now.isoformat()
    if scene is None and force and WAKE <= now.hour < SLEEP:
        scene = "regular"
    if scene is None:
        save(STATE, st)
        return None

    # 英語で呟くか。朝の第一声（天気の一字）と寝言は日本語のまま
    lang = "en" if (scene in ("regular", "bar", "life", "goodnight")
                    and st["since_english"] + 1 >= st["next_english"]) else "ja"

    cfg = load(CONFIG, {})
    sinfo = season.season_info(now)
    recent_text = " ".join(t["text"] for t in tweets[-15:])
    fresh_flora = [f for f in sinfo["flora"] if f not in recent_text] or sinfo["flora"]
    season_hint = rng.choice(fresh_flora) if scene in ("morning", "goodnight") and rng.random() < 0.5 else None
    recent_ja = [t["text"] for t in tweets if t.get("lang", "ja") == "ja"][-MANNERISM_WINDOW:]
    avoid_endings = [w for w in MANNERISMS if any(w in t for t in recent_ja)] if lang == "ja" else []
    recent_grave = [t["text"] for t in tweets if t.get("expression") == "憂い"][-CONDOLENCE_WINDOW:]
    avoid_endings += [w for w in CONDOLENCES if any(w.lower() in t.lower() for t in recent_grave)]
    ctx = {"now": now, "scene": scene, "lang": lang, "season_hint": season_hint, "avoid_endings": avoid_endings, "season": sinfo, "mood": st["mood"],
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
        ctx["topic"] = pick_topic(now, st, items, quakes, rng, lang)
        if scene == "regular" and ctx["topic"] is None:
            ctx["scene"] = scene = "life"
            if rng.random() < 0.5:   # 暮らしの呟きでも、季節に触れるのは二回に一回
                ctx["season_hint"] = rng.choice(fresh_flora)
        if errors:
            print("RSS取得の失敗:", *errors, sep="\n  ")

    try:
        d = generate(llm, ctx)
    except (ValueError, json.JSONDecodeError):
        print("::warning title=一傘亭::呟きの返事を読み取れなかったので、今回は見送ります。")
        save(STATE, st)
        return None

    # 気分は少しずつしか動かない（慣性）
    w = 0.25 if d["severity"] < 3 else 0.5
    st["mood"] = {"valence": round(st["mood"]["valence"] * (1 - w) + d["valence"] * w, 3),
                  "arousal": round(st["mood"]["arousal"] * (1 - w) + d["arousal"] * w, 3)}
    st["since_sarcasm"] = 0 if d["expression"] == "片眉を上げる" else st["since_sarcasm"] + 1
    if lang == "en":
        st["since_english"] = 0
        st["next_english"] = rng.randint(*ENGLISH_EVERY)
    elif scene != "sleeptalk":
        st["since_english"] += 1
    if scene == "morning":
        st["morning_done"] = True
    elif scene == "goodnight":
        st["goodnight_done"] = True
    elif scene == "sleeptalk":
        st["sleeptalk_night"] = (now - timedelta(hours=WAKE)).date().isoformat()
    topic = ctx.get("topic")
    if topic:
        st["commented"][topic["key"]] = now.isoformat()
        st.setdefault("commented_titles", []).append({"title": topic["title"], "at": now.isoformat()})
    cutoff = (now - timedelta(days=2)).isoformat()
    st["commented"] = {k: v for k, v in st["commented"].items() if v > cutoff}
    day_ago = (now - timedelta(hours=24)).isoformat()
    st["commented_titles"] = [x for x in st.get("commented_titles", []) if x["at"] > day_ago][-80:]

    rec = {"at": now.isoformat(timespec="minutes"), "text": d["text"], "expression": d["expression"],
           "scene": scene, "lang": lang, "topic": topic["title"] if topic else None,
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
    # 「Run workflow」で force にチェックを入れたときだけ必ず呟かせる。
    # 外部の時計（cron-job.org）からの起動は、ふだんどおり確率で呟く
    force = a.force or os.environ.get("ISSANTEI_FORCE", "").lower() == "true"
    rec = tick(now, FakeLLM() if a.dry_run else ClaudeLLM(), a.fixtures, force)
    print(json.dumps(rec, ensure_ascii=False) if rec else f"{now:%H:%M} 今回は呟かない")


def _explain(ex: Exception) -> str:
    """失敗の理由を、GitHubの画面で読める日本語にする（キーの中身は出さない）。"""
    import urllib.error
    if isinstance(ex, KeyError) and "ANTHROPIC_API_KEY" in str(ex):
        return "APIキーが見つかりません。Secretsに ANTHROPIC_API_KEY という名前で登録されているか確かめてください。"
    if isinstance(ex, urllib.error.HTTPError) and "anthropic" in (ex.url or ""):
        try:
            detail = json.loads(ex.read()).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        hint = {401: "APIキーが正しくないか、無効になっています。",
                400: "リクエストが受け付けられませんでした（残高不足やモデル名の誤りのことがあります）。",
                403: "このキーでは使えない操作です。",
                404: "モデル名が見つかりません。",
                429: "利用の上限に達しました。少し待つか、上限を確かめてください。",
                529: "Anthropic側が混み合っています。次の回で動くはずです。"}.get(ex.code, "")
        return f"Claude APIがエラー {ex.code} を返しました。{hint} {detail}".strip()
    return f"{type(ex).__name__}: {ex}"


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        msg = _explain(ex)
        print(f"::error title=一傘亭::{msg}")
        raise
