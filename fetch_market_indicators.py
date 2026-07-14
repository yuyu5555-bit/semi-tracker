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
# bot判定を避けるため、実ブラウザに近いヘッダー一式を送る(2026-07)。
UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# TSMC 月次売上 — TSMC公式プレスリリース(pr.tsmc.com)
# ---------------------------------------------------------------------------
# 背景: investor.tsmc.com の月次売上ページはJavaScriptで描画され、
# SEC(6-K)は決算以外の開示も混ざり本文構造も複雑で、どちらも安定して
# パースできなかった(2026-07)。
# pr.tsmc.com の個別プレスリリースは静的HTML・平文の英文で、
# 「revenue for May 2026 was approximately NT$416.98 billion, an increase of
#  1.5 percent from April 2026 and an increase of 30.1 percent from May 2025.」
# という一文に売上・前月比・前年比が全て含まれており、最も安定して読み取れる。
#
# 一覧ページ(latest-news)には他のニュースも混ざり、1回の実行で取れるのは
# 直近2〜4ヶ月分程度。そのため前回実行時の結果(previous)を引き継いで
# マージし、実行を重ねるごとに履歴が蓄積される設計にしてある。
TSMC_LIST_URLS = [
    "https://pr.tsmc.com/english/latest-news",
    "https://pr.tsmc.com/english/news-archives",
]
TSMC_MONTHS_RE = ("January|February|March|April|May|June|July|August|"
                  "September|October|November|December")
TSMC_MONTH_MAP = {m: i + 1 for i, m in enumerate([
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
])}


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = (text.replace("&nbsp;", " ").replace("&#160;", " ")
            .replace("&#8217;", "'").replace("&rsquo;", "'")
            .replace("&#8211;", "-").replace("&ndash;", "-"))
    return re.sub(r"\s+", " ", text)


def _find_revenue_report_ids(html: str) -> list[str]:
    """一覧ページのHTMLから「◯◯ Revenue Report」記事のニュースIDを抽出。"""
    ids = []
    for m in re.finditer(
        r'href="(?:https://pr\.tsmc\.com)?/english/news/(\d+)"'
        rf'[\s\S]{{0,300}}?TSMC\s+(?:{TSMC_MONTHS_RE})\s+\d{{4}}\s+Revenue Report',
        html,
    ):
        ids.append(m.group(1))
    return ids


def _parse_tsmc_revenue_page(html: str) -> dict | None:
    """個別プレスリリースの平文から、対象月・売上・前年比を抽出。"""
    text = _strip_tags(html)
    m = re.search(
        rf"revenue for ({TSMC_MONTHS_RE}) (\d{{4}}) was approximately NT\$([\d,]+\.?\d*) billion,\s*"
        r"(?:an increase|a decrease) of [\d.]+ percent from \w+ \d{4} and\s*"
        r"(an increase|a decrease) of ([\d.]+) percent from \w+ \d{4}",
        text,
    )
    if not m:
        return None
    month = TSMC_MONTH_MAP[m.group(1)]
    year = int(m.group(2))
    value_billion = float(m.group(3).replace(",", ""))
    yoy = float(m.group(5))
    if m.group(4) == "a decrease":
        yoy = -yoy
    return {
        "year": year, "month": month,
        "net_revenue_mntd": round(value_billion * 1000),  # 億NTD→百万NTD
        "yoy_pct": yoy,
    }


def fetch_tsmc_monthly_revenue(max_months: int = 8, previous: list[dict] | None = None) -> list[dict]:
    """TSMC公式プレスリリースから月次売上を取得し、前回結果とマージして返す。
    previous: 前回実行時のリスト(呼び出し側が docs/data.json から読んで渡す)。
              渡さなければ今回スクレイピングできた分のみになる。
    """
    ids = []
    for url in TSMC_LIST_URLS:
        try:
            html = _http_get(url)
            found = _find_revenue_report_ids(html)
            has_kw = "Revenue Report" in html
            print(f"    [TSMC月次] {url}")
            print(f"      HTML {len(html)}文字 / 'Revenue Report'の文字列: "
                  f"{'あり' if has_kw else 'なし(JS描画の可能性)'} / 記事ID抽出: {len(found)}件")
            ids += found
        except Exception as e:
            print(f"    [TSMC月次] HTTP失敗 ({url}): {type(e).__name__}: {e}")

    seen, uniq_ids = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq_ids.append(i)
    uniq_ids.sort(key=int, reverse=True)  # IDが大きいほど新しい

    scraped = []
    for nid in uniq_ids[:max_months + 4]:  # 非売上記事の混入を見込み少し多めに試す
        try:
            page = _http_get(f"https://pr.tsmc.com/english/news/{nid}")
        except Exception:
            continue
        parsed = _parse_tsmc_revenue_page(page)
        if parsed:
            scraped.append(parsed)
        if len(scraped) >= max_months:
            break

    print(f"    [TSMC月次] 記事から抽出できた月次データ: {len(scraped)}件 "
          f"(前回引き継ぎ: {len(previous or [])}件)")

    # 前回結果とマージ(同じ年月は今回の値で上書き) → 実行を重ねるほど履歴が充実
    merged = {(r["year"], r["month"]): r for r in (previous or [])}
    for r in scraped:
        merged[(r["year"], r["month"])] = r

    out = sorted(merged.values(), key=lambda r: (r["year"], r["month"]), reverse=True)
    return out[:max_months]


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


# ---------------------------------------------------------------------------
# 米国長期金利(10年債利回り, ^TNX) — Yahoo Finance
# ---------------------------------------------------------------------------
def fetch_us10y_yield() -> dict:
    """米10年国債利回り(^TNX)の直近日足を返す。取得失敗時は空dict。
    ^TNXは"利回り×10"で配信される(例: 44.2 は 4.42%)ため /10 する。
    """
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX"
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
        last_ts, last_raw = pairs[-1]
        prev_raw = pairs[-2][1]
        last_yield = last_raw / 10
        prev_yield = prev_raw / 10
        chg_bp = (last_yield - prev_yield) * 100  # bp(ベーシスポイント)変化

        all_yields = [c / 10 for _, c in pairs]
        hi52 = max(all_yields)
        lo52 = min(all_yields)

        spark = [round(c / 10, 3) for _, c in pairs[-30:]]
        date_str = datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        return {
            "name": "米10年国債利回り",
            "last": round(last_yield, 3),
            "chg_bp": round(chg_bp, 1),
            "hi52": round(hi52, 3),
            "lo52": round(lo52, 3),
            "date": date_str,
            "spark": spark,
        }
    except Exception as e:
        print(f"US10Y fetch error: {e}")
        return {}


if __name__ == "__main__":
    print("=== TSMC月次売上 ===")
    for r in fetch_tsmc_monthly_revenue():
        print(f"  {r['year']}-{r['month']:02d}: {r['net_revenue_mntd']:,} 百万NTD (YoY {r['yoy_pct']}%)")
    print("\n=== SOX指数 ===")
    print(fetch_sox_index())
    print("\n=== 米10年国債利回り ===")
    print(fetch_us10y_yield())
