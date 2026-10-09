"""呟きの生成：Claude APIに場面と話題を渡し、一傘亭の呟きをJSONで受け取る。"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from .persona import ENGLISH, EXPRESSIONS, SCENES, SYSTEM

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
WEEKDAYS = "月火水木金土日"


def build_prompt(ctx: dict) -> str:
    now = ctx["now"]
    lines = [f"いまは {now:%Y年%-m月%-d日}（{WEEKDAYS[now.weekday()]}）{now:%H:%M}。"]
    s = ctx["season"]
    lines.append(f"季節：{s['sekki']}の頃" + (f"。今日は{s['sekki_today']}の入り" if s["sekki_today"] else "")
                 + f"。いま見ごろの草花：{s['flora']}。")
    if ctx.get("weather"):
        w = ctx["weather"]
        lines.append(f"{w['place']}の今日の天気：{w['text']}。")
    m = ctx["mood"]
    lines.append(f"いまの気分（-1〜1）：快さ {m['valence']:+.2f}、高ぶり {m['arousal']:+.2f}。この気分が表情と口ぶりににじむように。")
    if not ctx["sarcasm_allowed"]:
        lines.append("最近皮肉を言ったばかりなので、今回は「片眉を上げる」を使わない。")
    lines.append("\n## 場面\n" + SCENES[ctx["scene"]].format(weather=(ctx.get("weather") or {}).get("char", "晴")))
    if ctx.get("topic"):
        t = ctx["topic"]
        lines.append(f"\n## 話題（{t['weight']}社が報道" + ("。深刻な出来事" if t["grave"] else "") + "）")
        for h in t["headlines"]:
            lines.append(f"- {h}")
    if ctx.get("quakes"):
        lines.append("\n## 最近の地震")
        for q in ctx["quakes"]:
            lines.append(f"- {q['at']:%-d日%H時%M分} {q['area']} M{q['mag']} 最大震度{q['maxi']}")
    if ctx.get("lang") == "en":
        lines.append("\n" + ENGLISH)
    if ctx.get("recent"):
        lines.append("\n## 直近の自分の呟き（繰り返さない）")
        for r in ctx["recent"]:
            lines.append(f"- {r}")
    return "\n".join(lines)


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("JSONが見つからない")
    d = json.loads(m.group(0))
    d["text"] = str(d.get("text", "")).strip().strip("「」")
    if d.get("expression") not in EXPRESSIONS:
        d["expression"] = "思案"
    for k in ("valence", "arousal"):
        try:
            d[k] = max(-1.0, min(1.0, float(d.get(k, 0))))
        except (TypeError, ValueError):
            d[k] = 0.0
    try:
        d["severity"] = max(0, min(3, int(d.get("severity", 0))))
    except (TypeError, ValueError):
        d["severity"] = 0
    return d


def check(d: dict, ctx: dict) -> str | None:
    """作法に反していれば理由を返す。"""
    n = len(d["text"])
    if ctx.get("lang") == "en":
        limit = (25, 100)
        if not d["text"].isascii() and any("\u3040" <= c <= "\u9fff" for c in d["text"]):
            return "英語だけで書く（日本語を混ぜない）"
    else:
        limit = (8, 24) if ctx["scene"] == "sleeptalk" else (14, 36)
    if not limit[0] <= n <= limit[1]:
        return f"長さが{n}字。{limit[0]}〜{limit[1]}字に収める"
    if d["expression"] == "片眉を上げる" and not ctx["sarcasm_allowed"]:
        return "今回は皮肉の顔を使わない"
    if ctx.get("topic") and ctx["topic"]["grave"] and d["expression"] not in ("憂い", "思案"):
        return "深刻な出来事なので、憂いの顔で静かに"
    if d["expression"] == "寝顔" and ctx["scene"] != "sleeptalk":
        return "起きているので寝顔は使わない"
    return None


class ClaudeLLM:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ["ANTHROPIC_API_KEY"]
        self.model = model or os.environ.get("ISSANTEI_MODEL") or DEFAULT_MODEL

    def complete(self, user: str) -> str:
        body = json.dumps({
            "model": self.model, "max_tokens": 300, "system": SYSTEM,
            "messages": [{"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(API_URL, data=body, headers={
            "x-api-key": self.api_key, "anthropic-version": "2023-06-01",
            "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())
        return "".join(b.get("text", "") for b in res.get("content", []))


class FakeLLM:
    """試運転用。APIを呼ばずに、場面と話題から決まった形の呟きを返す。"""

    def complete(self, user: str) -> str:
        scene = re.search(r"## 場面\n(.+)", user).group(1)
        topic = re.search(r"## 話題.*\n- (.+)", user)
        if "寝言" in scene:
            return json.dumps({"text": "……それは、私の傘だ……", "expression": "寝顔", "valence": 0, "arousal": -0.5, "severity": 0}, ensure_ascii=False)
        if "最初の呟き" in scene:
            ch = re.search(r"天気の一字（(.)）", scene).group(1)
            return json.dumps({"text": f"{ch}。さて、夜のうちに世の中は何をしでかしたかな", "expression": "微笑", "valence": 0.3, "arousal": 0, "severity": 0}, ensure_ascii=False)
        if "就寝前" in scene:
            return json.dumps({"text": "そろそろ寝る。世の中の続きは、また明日拝見しよう", "expression": "微笑", "valence": 0.2, "arousal": -0.4, "severity": 0}, ensure_ascii=False)
        if "今回は英語で呟く" in user:
            return json.dumps({"text": "(dry run) One reads the papers so the papers need not read one.", "expression": "片眉を上げる" if "皮肉の顔を使わない" not in user else "思案", "valence": 0, "arousal": 0, "severity": 0})
        if topic:
            head = topic.group(1)[:14]
            grave = "深刻" in user
            return json.dumps({"text": f"（試運転）{head}…の件、ふむ", "expression": "憂い" if grave else "思案",
                               "valence": -0.6 if grave else 0, "arousal": 0.1, "severity": 3 if grave else 1}, ensure_ascii=False)
        return json.dumps({"text": "（試運転）道端に秋桜。誰に見せるでもなく立派だ", "expression": "微笑", "valence": 0.4, "arousal": -0.2, "severity": 0}, ensure_ascii=False)


def generate(llm, ctx: dict) -> dict:
    """生成し、作法に反していれば一度だけ言い直させる。それでも駄目なら手直しして使う。"""
    prompt = build_prompt(ctx)
    try:
        d = _parse(llm.complete(prompt))
    except (ValueError, json.JSONDecodeError):
        # まれにJSON以外の返事が来る。一度だけ念を押して頼み直す
        d = _parse(llm.complete(prompt + "\n\n※説明は書かず、指定のJSONだけを一行で出力すること。"))
    problem = check(d, ctx)
    if problem:
        try:
            d2 = _parse(llm.complete(prompt + f"\n\n※前回の案「{d['text']}」（{d['expression']}）は不可：{problem}。作り直すこと。"))
        except (ValueError, json.JSONDecodeError):
            d2 = None
        if d2 and not check(d2, ctx):
            return d2
        if d2:
            d = d2
    # 最後の手直し
    if ctx["scene"] == "sleeptalk":
        d["expression"] = "寝顔"
    elif d["expression"] == "寝顔" or (d["expression"] == "片眉を上げる" and not ctx["sarcasm_allowed"]):
        d["expression"] = "思案"
    if ctx.get("topic") and ctx["topic"]["grave"]:
        d["expression"] = "憂い"
        d["severity"] = 3
    if d["severity"] >= 3:
        d["expression"] = "憂い"
    return d
