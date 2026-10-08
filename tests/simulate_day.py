"""一日分（15分刻み）を模擬データで走らせ、何をいつ呟くかを確かめる。

  python tests/simulate_day.py 2026-10-08
本物のデータは汚さない（一時フォルダに書く）。APIは呼ばない。
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from issantei import main as M  # noqa: E402
from issantei.generate import FakeLLM  # noqa: E402

day = sys.argv[1] if len(sys.argv) > 1 else "2026-10-08"
fixtures = Path(__file__).parent / "fixtures"
with tempfile.TemporaryDirectory() as tmp:
    M.TWEETS = Path(tmp) / "tweets.json"
    M.STATE = Path(tmp) / "state.json"
    t = datetime.fromisoformat(day + "T05:45").replace(tzinfo=M.JST)
    end = t + timedelta(hours=24, minutes=30)
    count = 0
    while t <= end:
        rec = M.tick(t, FakeLLM(), fixtures)
        if rec:
            count += 1
            print(f"{t:%m/%d %H:%M} [{rec['scene']:<9}] {rec['expression']:<6} {rec['text']}")
        t += timedelta(minutes=15)
    print(f"\n合計 {count} 回")
