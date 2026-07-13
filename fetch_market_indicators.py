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
# TSMC 月次売上 — SEC EDGAR経由(公式・確実なソース)
# ---------------------------------------------------------------------------
# 背景: investor.tsmc.com の月次売上ページはJavaScriptで描画されており、
# 単純なHTML取得(urllib)では数字が取れないことが判明した(2026-07)。
# 代わりにTSMCが米SEC(証券取引委員会)へ提出する Form 6-K(月次売上報告)を使う。
# これはSECの公式API(静的JSON・認証不要)から確実に一覧取得でき、
# 本文もシンプルな静的HTMLなので安定してパースできる。
SEC_TSM_CIK = "0001046179"  # TSMCのSEC CIK番号(固定)
SEC_SUBMISSIONS = f"https://data.sec.gov/submissions/CIK{SEC_TSM_CIK}.json"
SEC_UA = {"User-Agent": "semi-tracker research contact@example.com"}  # SECはUser-Agent必須

MONTH_MAP = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}


def _get_sec(url: str) -> str:
    req = urllib.request.Request(url, headers=SEC_UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="ignore")


def fetch_tsmc_monthly_revenue(max_months: int = 8) -> list[dict]:
    """SEC EDGARからTSMCのForm 6-K(月次売上報告)を取得し、直近分をパースして返す。
    返り値: [{year, month, net_revenue_mntd, yoy_pct}, ...] (新しい順)
    """
    try:
        data = json.loads(_get_sec(SEC_SUBMISSIONS))
    except Exception as e:
        print(f"SEC submissions fetch error: {e}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])

    candidates = []
    for i, form in enumerate(forms):
        if form != "6-K":
            continue
        candidates.append((dates[i], accessions[i], docs[i]))
    candidates.sort(reverse=True)  # 新しい提出日順

    out = []
    checked = 0
    for filing_date, accession, doc in candidates:
        if checked >= max_months + 6:  # 売上以外の6-Kも混じるため少し多めに見る
            break
        checked += 1
        acc_nodash = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(SEC_TSM_CIK)}/{acc_nodash}/{doc}"
        try:
            html = _get_sec(url)
        except Exception:
            continue

        # タイトルから対象月を特定: 例 "TSMC May 2026 Revenue Report"
        m_title = re.search(
            r"TSMC\s+(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{4})\s+Revenue Report",
            html,
        )
        if not m_title:
            continue  # 月次売上報告以外の6-K(決算・その他開示)はスキップ
        month = MONTH_MAP[m_title.group(1)]
        year = int(m_title.group(2))

        # Net Revenue行から当月実績とYoY%を抽出(表がテキスト化されても崩れにくいよう緩めに)
        m_rev = re.search(
            r"Net Revenue[^\d\-]*?([\d,]{6,})[^\d\-]*?([\d,]{6,})[^\d\-]*?"
            r"(-?[\d.]+)[^\d\-]*?([\d,]{6,})[^\d\-]*?(-?[\d.]+)",
            html,
        )
        if not m_rev:
            continue
        try:
            net_revenue = int(m_rev.group(1).replace(",", ""))
            yoy_pct = float(m_rev.group(5))
        except ValueError:
            continue

        out.append({
            "year": year, "month": month,
            "net_revenue_mntd": net_revenue, "yoy_pct": yoy_pct,
        })
        if len(out) >= max_months:
            break

    # 重複排除 + 新しい順
    seen, dedup = set(), []
    for r in sorted(out, key=lambda r: (r["year"], r["month"]), reverse=True):
        key = (r["year"], r["month"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)
    return dedup


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
