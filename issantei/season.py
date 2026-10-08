"""季節：二十四節気（太陽の黄経から計算）と、月ごとの草花の手がかり。"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

SEKKI = ["春分", "清明", "穀雨", "立夏", "小満", "芒種", "夏至", "小暑", "大暑", "立秋", "処暑", "白露",
         "秋分", "寒露", "霜降", "立冬", "小雪", "大雪", "冬至", "小寒", "大寒", "立春", "雨水", "啓蟄"]

FLORA = {1: "水仙、蝋梅", 2: "梅、福寿草", 3: "沈丁花、桃、菜の花", 4: "桜、菜の花、若葉", 5: "藤、つつじ、新緑",
         6: "紫陽花、梔子", 7: "朝顔、百日紅", 8: "向日葵、百日紅、夾竹桃", 9: "彼岸花、萩、秋桜",
         10: "金木犀、秋桜、すすき", 11: "菊、紅葉、銀杏", 12: "山茶花、南天、冬木立"}


def sun_longitude(t: datetime) -> float:
    """太陽の視黄経（度）。Meeusの略算式で、誤差はおよそ0.01度。"""
    jd = t.astimezone(timezone.utc).timestamp() / 86400 + 2440587.5
    T = (jd - 2451545.0) / 36525
    L0 = 280.46646 + 36000.76983 * T + 0.0003032 * T * T
    M = math.radians(357.52911 + 35999.05029 * T - 0.0001537 * T * T)
    C = ((1.914602 - 0.004817 * T - 0.000014 * T * T) * math.sin(M)
         + (0.019993 - 0.000101 * T) * math.sin(2 * M) + 0.000289 * math.sin(3 * M))
    omega = math.radians(125.04 - 1934.136 * T)
    return (L0 + C - 0.00569 - 0.00478 * math.sin(omega)) % 360


def season_info(now: datetime) -> dict:
    """{"sekki": 今の節気, "sekki_today": 今日が節気の入りなら名前, "flora": 草花}"""
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    a = int(sun_longitude(day0) // 15)
    b = int(sun_longitude(day0 + timedelta(days=1)) // 15)
    return {
        "sekki": SEKKI[int(sun_longitude(now) // 15)],
        "sekki_today": SEKKI[b] if a != b else None,
        "flora": FLORA[now.month],
    }
