"""話題のまとめ：似た見出しを束ね、何社が報じたかで大小をはかる。"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from .collect import Item

_STRIP = re.compile(r"[【\[（(].*?[】\]）)]|[「」『』“”\"'、。・：:！!？?\s　…|｜]")

# 見出しに出たら「深刻」とみなす語。茶化さず、憂いの顔で静かに触れる対象。
GRAVE_WORDS = ["死亡", "死者", "犠牲", "殺害", "殺人", "遺体", "行方不明", "墜落",
               "津波警報", "大津波", "特別警報", "震度6", "震度7", "テロ", "銃撃", "空爆", "戦死",
               "killed", "dead", "dies", "death toll", "massacre", "shooting", "murder", "stabbing",
               "missing", "earthquake kills", "airstrike", "terror", "crash kills"]


def _norm(title: str) -> str:
    t = unicodedata.normalize("NFKC", title)
    if t.isascii():
        return re.sub(r"\s+", " ", t).strip()
    return _STRIP.sub("", t)


def _grams(t: str) -> set[str]:
    if t.isascii():   # 英語は単語で比べる
        words = [w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 2]
        return set(words) or {t}
    return {t[i:i + 2] for i in range(len(t) - 1)} or {t}


@dataclass
class Topic:
    title: str                      # 代表の見出し
    items: list[Item] = field(default_factory=list)
    grams: set[str] = field(default_factory=set)

    @property
    def sources(self) -> set[str]:
        return {i.source for i in self.items}

    @property
    def weight(self) -> int:
        """何社が報じたか。大きな話題ほど大きい。"""
        return len(self.sources)

    @property
    def key(self) -> str:
        return hashlib.sha1(_norm(self.title)[:24].encode()).hexdigest()[:12]

    @property
    def grave(self) -> bool:
        return any(w in i.title.lower() for i in self.items for w in GRAVE_WORDS)

    def links(self, n: int = 3) -> list[dict]:
        seen, out = set(), []
        for i in self.items:
            if i.source in seen or not i.link:
                continue
            seen.add(i.source)
            out.append({"source": i.source, "title": i.title, "url": i.link})
            if len(out) >= n:
                break
        return out


def cluster(items: list[Item], threshold: float = 0.33) -> list[Topic]:
    topics: list[Topic] = []
    for it in items:
        g = _grams(_norm(it.title))
        best, best_s = None, 0.0
        for t in topics:
            inter = len(g & t.grams)
            s = inter / (len(g | t.grams) or 1)
            # 片方がもう片方に含まれるような短い見出しも拾う
            s = max(s, inter / (min(len(g), len(t.grams)) or 1) * 0.8)
            if s > best_s:
                best, best_s = t, s
        if best is not None and best_s >= threshold:
            best.items.append(it)   # 代表の見出しの文字並びだけで比べ、束が膨らみすぎないようにする
        else:
            topics.append(Topic(it.title, [it], set(g)))
    topics.sort(key=lambda t: t.weight, reverse=True)
    return topics


def grams_of(title: str) -> set[str]:
    return _grams(_norm(title))


def similar(a: set[str], b: set[str], threshold: float = 0.3) -> bool:
    """続報かどうか。固有名詞などの文字並びが十分重なれば同じ出来事とみなす。"""
    inter = len(a & b)
    return max(inter / (len(a | b) or 1), inter / (min(len(a), len(b)) or 1) * 0.8) >= threshold
