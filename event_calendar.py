# -*- coding: utf-8 -*-
"""
半導体イベントカレンダー — 世界の主要半導体・AI銘柄の決算日を自動収集
========================================================================
方針: 推測日程は載せない。取得できた確定/公表済みの日程だけを載せる。

ソース:
  1) TSMC 公式IR (investor.tsmc.com) — 月次売上・決算の"時刻つき確定日程"。
     台風による延期なども反映される最も正確な一次ソース。
  2) Yahoo Finance quoteSummary (calendarEvents) — 主要25銘柄の決算日。
     株価取得(fetch_yahoo)と同じホストで認証不要。
  3) TDnet 開示(fetch_disclosures側) — 日本企業の決算発表予定日。

いずれも失敗時は空を返し、他のデータ更新は止めない。
"""
from __future__ import annotations
import json
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone

TIMEOUT = 20
UA = {"User-Agent": "Mozilla/5.0 (semi-tracker calendar fetcher)"}
TSMC_CAL = "https://investor.tsmc.com/english/financial-calendar"
YF_SUMMARY = ("https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
              "?modules=calendarEvents")

# 世界の主要半導体・AI銘柄(相場を動かす順に重要度を設定)
# level 3 = 相場全体を動かす / 2 = セクターを動かす
WATCH = [
    # --- AI・GPU・CPU ---
    ("NVDA", "NVIDIA",            "US", 3, "AI需要の総本山。ガイダンスが半導体全体の方向を決める"),
    ("AMD",  "AMD",               "US", 3, "MI系GPUの進捗。NVIDIA対抗の実力が問われる"),
    ("AVGO", "Broadcom",          "US", 3, "カスタムAI ASIC(Google/Meta等)の受注動向"),
    ("MRVL", "Marvell",           "US", 3, "カスタムAI・光通信。ASIC需要の先行指標"),
    ("INTC", "Intel",             "US", 2, "18A立ち上げとファウンドリ戦略の進捗"),
    ("ARM",  "ARM",               "US", 2, "設計IP。スマホ/データセンター両にらみ"),
    ("QCOM", "Qualcomm",          "US", 2, "スマホ市況とエッジAIの温度感"),
    ("TXN",  "Texas Instruments", "US", 2, "アナログ半導体。産業・車載の需要バロメーター"),
    # --- メモリ ---
    ("MU",   "Micron",            "US", 3, "HBM/DRAM/NANDの価格と需給。メモリ市況の最前線"),
    ("WDC",  "Western Digital",   "US", 2, "NAND/HDD。データセンター向けストレージ需要"),
    # --- 半導体製造装置(SPE) ---
    ("AMAT", "Applied Materials", "US", 3, "前工程装置の最大手。ファブ投資の実弾"),
    ("LRCX", "Lam Research",      "US", 3, "エッチング/成膜。メモリ投資と連動性が高い"),
    ("KLAC", "KLA",               "US", 2, "検査・計測。歩留まり改善needsの温度計"),
    ("ASML", "ASML",              "NL", 3, "露光装置。受注(ブッキング)がファブ投資の先行指標"),
    # --- ファウンドリ・その他 ---
    ("TSM",  "TSMC (ADR)",        "TW", 3, "世界最大のファウンドリ。設備投資計画が最大の材料"),
    ("GFS",  "GlobalFoundries",   "US", 1, "成熟ノード。車載・産業の需要動向"),
    ("UMC",  "UMC",               "TW", 1, "成熟ノードのファウンドリ"),
    # --- 光・ネットワーク(AIインフラ) ---
    ("ANET", "Arista Networks",   "US", 2, "データセンタースイッチ。AI配線需要"),
    ("COHR", "Coherent",          "US", 2, "光通信部品。CPO/光電融合の本命"),
    ("LITE", "Lumentum",          "US", 2, "光部品。データセンター向け需要"),
    # --- ハイパースケーラー(設備投資の出し手) ---
    ("MSFT", "Microsoft",         "US", 2, "AI設備投資(CapEx)計画。データセンター需要の源泉"),
    ("GOOGL","Alphabet",          "US", 2, "TPU自社開発とCapEx計画"),
    ("META", "Meta",              "US", 2, "MTIA自社開発とCapEx計画"),
    ("AMZN", "Amazon",            "US", 2, "Trainium自社開発とCapEx計画"),
]


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# 1) TSMC 公式IR(月次売上・決算の確定日程。時刻つき)
# ---------------------------------------------------------------------------
def _classify_tsmc(title: str):
    t = title.lower()
    if "monthly sales" in t or "monthly revenue" in t:
        return ("月次", 3, "AI需要の最速の実需指標。前月比・前年比の伸びが焦点")
    if "results" in t or "earnings" in t:
        return ("決算", 3, "設備投資計画と需要見通し。装置・材料株の最大の材料")
    if "shareholders" in t:
        return ("株主総会", 1, "")
    return ("その他", 1, "")


def fetch_tsmc_events() -> list[dict]:
    try:
        html = _get(TSMC_CAL)
        print(f"    [TSMC] HTML取得成功: {len(html)}文字")
    except Exception as e:
        print(f"    [TSMC] HTTP失敗: {type(e).__name__}: {e}")
        return []
    # 日付らしき文字列が本文に含まれるか(JS描画なら含まれない)
    date_hits = len(re.findall(r"\d{4}-\d{2}-\d{2}", html))
    print(f"    [TSMC] 本文中の日付パターン: {date_hits}件"
          + ("(0件=JavaScript描画のため静的HTMLに数字が無い)" if date_hits == 0 else ""))
    out = []
    for m in re.finditer(
        r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}):\d{2}.*?\*(TSMC[^*]{3,120}?)\*",
        html, re.S,
    ):
        title = re.sub(r"\s+", " ", m.group(3).strip())
        kind, level, note = _classify_tsmc(title)
        out.append({
            "date": m.group(1), "time": m.group(2), "tz": "台北",
            "country": "TW", "kind": kind, "level": level,
            "title": title, "note": note, "url": TSMC_CAL, "confirmed": True,
        })
    seen, ded = set(), []
    for e in out:
        k = (e["date"], e["title"][:40])
        if k not in seen:
            seen.add(k)
            ded.append(e)
    return ded


# ---------------------------------------------------------------------------
# 2) Yahoo Finance: 主要銘柄の決算日
# ---------------------------------------------------------------------------
def fetch_earnings_dates() -> list[dict]:
    """主要銘柄の次回決算日を取得(診断ログ付き)。

    2026-07: 0件が続いたため、何が起きているかを必ずログに出すようにした。
    - HTTPが失敗したのか
    - 接続はできたが events.earnings が空なのか
    - 未来の決算日が無いだけなのか
    をログで区別できるようにする。
    """
    out = []
    now = datetime.now(timezone.utc).date()
    stat = {"http_error": 0, "no_result": 0, "no_earnings": 0, "no_future": 0, "ok": 0}
    first_dump_done = False

    for sym, name, country, level, note in WATCH:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{sym}?range=1y&interval=1d&events=earnings")
        try:
            raw = _get(url)
        except Exception as e:
            stat["http_error"] += 1
            print(f"    [HTTP失敗] {sym}: {type(e).__name__}: {e}")
            continue

        try:
            data = json.loads(raw)
        except Exception as e:
            stat["http_error"] += 1
            print(f"    [JSON解析失敗] {sym}: {e} / 先頭200字: {raw[:200]!r}")
            continue

        res = (data.get("chart", {}).get("result") or [None])[0]
        if not res:
            stat["no_result"] += 1
            if not first_dump_done:
                print(f"    [result無し] {sym} / レスポンス先頭300字: {raw[:300]!r}")
                first_dump_done = True
            continue

        earnings = (res.get("events") or {}).get("earnings") or {}
        if not earnings:
            stat["no_earnings"] += 1
            if not first_dump_done:
                keys = list(res.keys())
                ev_keys = list((res.get("events") or {}).keys())
                print(f"    [earnings無し] {sym} / res keys={keys} / events keys={ev_keys}")
                first_dump_done = True
            continue

        future = []
        for _k, ev in earnings.items():
            ts = ev.get("earningsDate") or ev.get("date")
            if not ts:
                continue
            d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            if d >= now:
                future.append(d)
        if not future:
            stat["no_future"] += 1
            continue

        d = min(future)
        stat["ok"] += 1
        out.append({
            "date": d.strftime("%Y-%m-%d"), "time": "", "tz": "",
            "country": country, "kind": "決算", "level": level,
            "title": f"{name} 決算", "note": note,
            "url": f"https://finance.yahoo.com/quote/{sym}",
            "confirmed": True,
        })

    print(f"  → 決算日を取得できた銘柄: {len(out)}/{len(WATCH)}")
    print(f"    内訳: 成功{stat['ok']} / HTTP失敗{stat['http_error']} / "
          f"result無し{stat['no_result']} / earnings無し{stat['no_earnings']} / "
          f"未来日程無し{stat['no_future']}")
    return out


# ---------------------------------------------------------------------------
def build_event_calendar(days_ahead: int = 60) -> list[dict]:
    today = date.today()
    end = today + timedelta(days=days_ahead)

    print("[1/2] TSMC公式カレンダーを取得中...")
    tsmc = fetch_tsmc_events()
    print(f"  → TSMC: {len(tsmc)}件")

    print("[2/2] 主要銘柄の決算日を取得中...")
    earnings = fetch_earnings_dates()

    events = tsmc + earnings
    print(f"合計 {len(events)}件(期間フィルタ前)")

    up = []
    seen = set()
    for e in events:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (today <= d <= end):
            continue
        key = (e["date"], e["title"][:30])
        if key in seen:
            continue
        seen.add(key)
        up.append(e)

    up.sort(key=lambda x: (x["date"], -x["level"]))
    return up


if __name__ == "__main__":
    evs = build_event_calendar()
    print(f"取得 {len(evs)} 件\n")
    for e in evs:
        stars = "★" * e["level"]
        tm = f" {e['time']}({e['tz']})" if e.get("time") else ""
        mark = "" if e["confirmed"] else " (予定)"
        print(f"{e['date']}{tm} [{e['country']}] {stars} {e['title']}{mark}")
