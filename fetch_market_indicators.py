# -*- coding: utf-8 -*-
"""
市況インジケーター自動取得: TSMC月次売上 + SOX指数(PHLX半導体指数)
====================================================================
実需の先行指標(TSMC月次)と市場の温度感(SOX)を毎回自動更新する。
どちらも公式/準公式ソースからの取得。失敗しても他のデータ更新は継続する。
"""
from __future__ import annotations
import json
import re
import urllib.request
from datetime import datetime, timezone

TIMEOUT = 25
UA = {"User-Agent": "Mozilla/5.0 (semi-tracker data fetcher)"}


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# TSMC 月次売上(公式IR: investor.tsmc.com)
# ---------------------------------------------------------------------------
MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Sept": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def fetch_tsmc_monthly_revenue(years: list[int] | None = None) -> list[dict]:
    """TSMC IR公式ページの月次売上表(HTML)をパースして返す。
    返り値: [{year, month, net_revenue_mntd, yoy_pct}, ...] (新しい順)
    """
    if years is None:
        this_year = datetime.now(timezone.utc).year
        years = [this_year, this_year - 1]

    out = []
    for y in years:
        url = f"https://investor.tsmc.com/english/monthly-revenue/{y}"
        try:
            html = _http_get(url)
        except Exception as e:
            print(f"TSMC {y} fetch error: {e}")
            continue

        # 表の行を抽出。TSMC IRページの実際のHTML構造は現時点で未検証のため、
        # HTML(<td>区切り)・素のテキスト表記の両方を試す形にしてある。
        # ここが崩れて0件になっても、他のデータ更新には影響しない(呼び出し側で握りつぶす)。
        patterns = [
            # パターンA: HTMLタグ区切り(<td>月</td>...<td>数字</td>...<td>%</td>)
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s*'
            r'(?:</td>|<[^>]+>)*\s*([\d,]{3,})\s*(?:</td>|<[^>]+>)*\s*([\d.]+)%',
            # パターンB: 素のテキスト/マークダウン的な表記(| 区切りやスペース区切り)
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s*[|>]?\s*([\d,]{3,})\s*[|]?\s*([\d.]+)%',
        ]
        rows = []
        for pat in patterns:
            found = re.findall(pat, html, re.S)
            if found:
                rows = found
                break
        for mon_str, rev_str, yoy_str in rows:
            month = MONTH_MAP.get(mon_str)
            if not month:
                continue
            try:
                rev = int(rev_str.replace(",", ""))
                yoy = float(yoy_str)
            except ValueError:
                continue
            out.append({"year": y, "month": month, "net_revenue_mntd": rev, "yoy_pct": yoy})

    # 重複排除(年月キー) + 新しい順ソート
    seen, dedup = set(), []
    for r in sorted(out, key=lambda r: (r["year"], r["month"]), reverse=True):
        key = (r["year"], r["month"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    return dedup[:14]  # 直近14ヶ月分あれば十分


# ---------------------------------------------------------------------------
# SOX指数(PHLX Semiconductor Sector Index, ^SOX) — Yahoo Finance
# ---------------------------------------------------------------------------
def fetch_sox_index() -> dict:
    """SOX指数の直近日足(終値・前日比・年初来)を返す。取得失敗時は空dict。"""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%5ESOX"
           "?range=1y&interval=1d&includeAdjustedClose=false")
    try:
        text = _http_get(url)
        data = json.loads(text)
        res = data["chart"]["result"][0]
        ts = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
        pairs = [(t, c) for t, c in zip(ts, closes) if c is not None]
        if len(pairs) < 2:
            return {}
        last_ts, last_close = pairs[-1]
        prev_close = pairs[-2][1]
        chg_pct = (last_close - prev_close) / prev_close * 100 if prev_close else None

        # 年初来(その年の最初の取得済み値を起点に)
        last_year = datetime.fromtimestamp(last_ts, tz=timezone.utc).year
        ytd_start = next(
            (c for t, c in pairs if datetime.fromtimestamp(t, tz=timezone.utc).year == last_year),
            pairs[0][1],
        )
        ytd_pct = (last_close - ytd_start) / ytd_start * 100 if ytd_start else None

        # 52週高値/安値と、高値からの調整率(織り込み度合いの目安)
        # 「実需は拡大中でも、株価は既に高値から調整している」という
        # 実需レイヤーと株価レイヤーの非対称を可視化するための指標。
        all_closes = [c for _, c in pairs]
        hi52 = max(all_closes)
        lo52 = min(all_closes)
        from_high_pct = (last_close - hi52) / hi52 * 100 if hi52 else None

        spark = [round(c, 1) for _, c in pairs[-30:]]  # 直近30日分の推移(グラフ用)
        date_str = datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        return {
            "name": "SOX (PHLX半導体指数)",
            "last": round(last_close, 2),
            "chg_pct": round(chg_pct, 2) if chg_pct is not None else None,
            "ytd_pct": round(ytd_pct, 2) if ytd_pct is not None else None,
            "hi52": round(hi52, 2),
            "lo52": round(lo52, 2),
            "from_high_pct": round(from_high_pct, 2) if from_high_pct is not None else None,
            "date": date_str,
            "spark": spark,
        }
    except Exception as e:
        print(f"SOX fetch error: {e}")
        return {}


if __name__ == "__main__":
    print("=== TSMC月次売上 ===")
    for r in fetch_tsmc_monthly_revenue():
        print(f"  {r['year']}-{r['month']:02d}: {r['net_revenue_mntd']:,} 百万NTD (YoY {r['yoy_pct']}%)")
    print("\n=== SOX指数 ===")
    print(fetch_sox_index())
