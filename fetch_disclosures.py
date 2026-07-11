# -*- coding: utf-8 -*-
"""
適時開示(TDnet)自動取得 — 保有銘柄の「本物のイベント」だけを拾う
================================================================
yanoshin TDnet WebAPI(非公式・無料・認証不要)から、themes.py の日本株コードに
該当する適時開示(決算短信・業績予想修正・配当・決算発表予定日 等)を取得する。
未来の予定を勝手に作らず、実際に開示された本物だけを返す。取れなければ空。

出力: [{date, code, name, title, url, kind}]  ← update_data.py が data.json に載せる
"""
from __future__ import annotations
import json
import urllib.request
from datetime import date, timedelta

API = "https://webapi.yanoshin.jp/webapi/tdnet/list/{range}.json?limit=3000"
LOOKBACK_DAYS = 12      # 直近何日分の開示を見るか
MAX_ITEMS = 40          # 表示上限
TIMEOUT = 25

# 拾う開示の種別(タイトル部分一致)。相場が動くものだけに絞る。
KIND_RULES = [
    ("予想修正", ("業績予想", "予想の修正", "上方修正", "下方修正", "修正に関する")),
    ("決算", ("決算短信", "四半期", "通期", "業績")),
    ("配当", ("配当", "株主還元", "増配", "復配")),
    ("自己株", ("自己株式", "自社株")),
    ("資本", ("株式分割", "公募", "第三者割当", "新株予約権", "立会外分売")),
    ("開示予定", ("決算発表予定", "発表予定日")),
    ("提携・M&A", ("提携", "買収", "子会社", "合併", "資本業務")),
]


def _jp_codes_from_themes():
    """themes.py から日本株4桁コードの集合を作る(英字コード285A等も含む)。"""
    ns = {}
    exec(open("themes.py", encoding="utf-8").read(), ns)
    codes = set()
    for m in ns["MACRO"]:
        for s in m["subs"]:
            for e in s.get("jp", []) + s.get("solo", []):
                codes.add(str(e[0]).strip())
    return codes


def _kind_of(title: str):
    for kind, keys in KIND_RULES:
        if any(k in title for k in keys):
            return kind
    return None


def _norm(api_code: str) -> str:
    """API の5桁コード(末尾0)を themes.py の4桁表記に正規化。"""
    c = str(api_code).strip()
    if len(c) == 5 and c.endswith("0"):
        return c[:4]
    return c


def fetch_disclosures(max_items: int = MAX_ITEMS) -> list[dict]:
    codes = _jp_codes_from_themes()
    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    url = API.format(range=f"{start:%Y%m%d}-{end:%Y%m%d}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "semi-tracker/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"disclosures fetch error: {e}")
        return []

    out, seen = [], set()
    for it in data.get("items", []):
        t = it.get("Tdnet") or it
        if not t:
            continue
        code = _norm(t.get("company_code", ""))
        if code not in codes:
            continue
        title = (t.get("title") or "").strip()
        kind = _kind_of(title)
        if kind is None:               # 相場に効かない事務的開示は除外
            continue
        pub = (t.get("pubdate") or "")[:10]  # YYYY-MM-DD
        key = code + title[:20]
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "date": pub,
            "code": code,
            "name": (t.get("company_name") or "").strip(),
            "title": title,
            "url": t.get("document_url") or "",
            "kind": kind,
        })
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:max_items]


if __name__ == "__main__":
    ds = fetch_disclosures()
    print(f"取得 {len(ds)} 件(保有銘柄の適時開示)")
    for d in ds[:15]:
        print(" ", d["date"], d["code"], f"[{d['kind']}]", d["name"], d["title"][:40])
