# -*- coding: utf-8 -*-
"""
イベントカレンダーだけを更新する(週1回実行)。
決算日程は日々変わらないので、株価更新(1日12回)とは分離してある。
出力: docs/calendar.json  → サイトが読み込んで表示する。
"""
import json
from datetime import datetime, timezone

from event_calendar import build_event_calendar


def main():
    events = build_event_calendar(days_ahead=75)
    out = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "events": events,
    }
    with open("docs/calendar.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"docs/calendar.json 更新: {len(events)}件")
    for e in events[:15]:
        stars = "★" * e["level"]
        tm = f" {e['time']}" if e.get("time") else ""
        print(f"  {e['date']}{tm} [{e['country']}] {stars} {e['title']}")


if __name__ == "__main__":
    main()
