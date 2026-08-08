#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""有報の"数値タグ"から各銘柄の依存構造(第1段)を作る。

診断(diag_fundamentals.py)で「売上高・設備投資額・研究開発費は数値タグで確実に取れる」
と確定したので、まずこの3本＋前年比を抽出する。セグメント/地域(テキストブロック)は次段。

会社ごとに要素IDが少し違うため、標準タグの候補を優先順に試して取りこぼしを防ぐ。
当期/前期の両方を拾い、前年比・R&D比率・設備投資比率まで計算する。

fetch_customers.py の EDINET 取得部を流用。出力: docs/fundamentals.json
 { "generated":ISO, "items": { sec: {
     "sales_oku":, "sales_yoy":, "capex_oku":, "capex_yoy":,
     "rnd_oku":, "rnd_ratio":, "capex_ratio":, "fy": } } }
"""
from __future__ import annotations
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone

from fetch_customers import (
    API_BASE, _get, _api_key, collect_yuho_docids,
    _read_csv_from_zip, _rows_from_csv,
)

# 各指標の候補(優先順)。(要素ID部分文字列(小文字), 項目名部分文字列)
METRICS = {
    "sales": {
        "primary_eid": ["netsalessummaryofbusinessresults",
                        "revenueifrssummaryofbusinessresults",
                        "operatingrevenuesummaryofbusinessresults",
                        "salesrevenuesnetifrssummaryofbusinessresults",
                        "revenuesummaryofbusinessresults"],
        "eid": ["netsales", "operatingrevenue", "revenueifrs", "revenue"],
        "item": ["売上高", "売上収益", "営業収益"],
    },
    "capex": {
        "primary_eid": [],
        "eid": ["capitalexpenditures", "capitalexpenditure"],
        "item": ["設備投資額", "設備投資"],
    },
    "rnd": {
        "primary_eid": ["researchanddevelopmentexpensessummaryofbusinessresults"],
        "eid": ["researchanddevelopmentexpenses", "researchanddevelopmentcost"],
        "item": ["研究開発費"],
    },
}

NUM_RE = re.compile(r"-?\d+(\.\d+)?$")


def _num(s: str):
    s = (s or "").strip().replace(",", "")
    if not NUM_RE.match(s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _unit_mult(u: str) -> float:
    if "百万" in u:
        return 1e6
    if "千円" in u:
        return 1e3
    return 1.0


def _match(eid_l: str, item: str, eids: list[str], items: list[str]) -> bool:
    return any(e in eid_l for e in eids) or any(i in item for i in items)


def _pick(rows: list[list[str]], cfg: dict):
    """当期・前期の値(円換算)を返す。標準サマリタグを優先。"""
    def scan(eids, items):
        cur = prior = None
        for r in rows:
            if len(r) < 9:
                continue
            eid_l = r[0].lower()
            item = r[1]
            ctx = r[2]
            rel = r[3]
            cons = r[4]
            unit = r[7]
            val = r[-1]
            if "個別" in cons:            # 連結を優先(個別は無視)
                continue
            if not _match(eid_l, item, eids, items):
                continue
            v = _num(val)
            if v is None:
                continue
            mult = _unit_mult(unit)
            is_cur = ("当期" in rel) or ("CurrentYear" in ctx)
            is_prior = (("前期" in rel) and ("前々" not in rel)) or ("Prior1Year" in ctx)
            if is_cur and cur is None:
                cur = v * mult
            elif is_prior and prior is None:
                prior = v * mult
        return cur, prior

    # まず標準サマリタグ、無ければ通常候補
    if cfg["primary_eid"]:
        c, p = scan(cfg["primary_eid"], [])
        if c is not None:
            return c, p
    return scan(cfg["eid"], cfg["item"])


def extract(rows: list[list[str]]) -> dict:
    out = {}
    sales_c, sales_p = _pick(rows, METRICS["sales"])
    capex_c, capex_p = _pick(rows, METRICS["capex"])
    rnd_c, rnd_p = _pick(rows, METRICS["rnd"])
    if sales_c is None:
        return {}                     # 売上が取れない=データ無しとして除外

    OKU = 1e8
    out["sales_oku"] = round(sales_c / OKU, 1)
    if sales_p:
        out["sales_yoy"] = round((sales_c - sales_p) / sales_p * 100, 1)
    if capex_c is not None:
        out["capex_oku"] = round(capex_c / OKU, 1)
        if capex_p:
            out["capex_yoy"] = round((capex_c - capex_p) / capex_p * 100, 1)
        out["capex_ratio"] = round(capex_c / sales_c * 100, 1)
    if rnd_c is not None:
        out["rnd_oku"] = round(rnd_c / OKU, 1)
        out["rnd_ratio"] = round(rnd_c / sales_c * 100, 1)
    return out


def _load_targets():
    ns: dict = {}
    exec(open("themes.py", encoding="utf-8").read(), ns)
    codes, name_of = set(), {}
    for m in ns["MACRO"]:
        for s in m["subs"]:
            for k in ("jp", "solo"):
                for e in s.get(k, []):
                    c = str(e[0])
                    if len(c) == 4 and c.isdigit():
                        codes.add(c)
                        if len(e) > 1:
                            name_of.setdefault(c, str(e[1]))
    return codes, name_of


def main() -> None:
    key = _api_key()
    if not key:
        return
    targets, name_of = _load_targets()
    print(f"[依存構造] 対象: 監視銘柄 {len(targets)}社")
    docids = collect_yuho_docids(key, targets, days_back=500, per_company=1)
    print(f"[依存構造] 有報docID: {len(docids)}社ぶん取得。数値を抽出します…")

    items: dict[str, dict] = {}
    ok = 0
    for sec in sorted(docids):
        docs = docids[sec]
        if not docs:
            continue
        url = (f"{API_BASE}/documents/{docs[0]}?type=5"
               f"&Subscription-Key={urllib.parse.quote(key)}")
        try:
            rows = _rows_from_csv(_read_csv_from_zip(_get(url)))
        except Exception:
            continue
        d = extract(rows)
        if d:
            items[sec] = d
            ok += 1
            if ok <= 12:
                nm = name_of.get(sec, sec)
                print(f"    [依存] {sec} {nm}: 売上{d.get('sales_oku')}億"
                      f"(前年比{d.get('sales_yoy')}%) / 設備投資{d.get('capex_oku')}億"
                      f"(比{d.get('capex_ratio')}%) / R&D{d.get('rnd_oku')}億"
                      f"(比{d.get('rnd_ratio')}%)")
        time.sleep(0.35)

    print(f"[依存構造] 抽出成功: {ok}社")

    # 空上書き防止: 今回が既存より大幅に少なければ維持
    prev = {}
    try:
        with open("docs/fundamentals.json", encoding="utf-8") as f:
            prev = json.load(f).get("items", {}) or {}
    except Exception:
        prev = {}
    if len(items) < len(prev) * 0.7:
        print(f"[依存構造] 空上書き防止: 今回{len(items)} < 既存{len(prev)} → 維持")
        return

    out = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": items,
    }
    with open("docs/fundamentals.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"docs/fundamentals.json 更新: {len(items)}社")


if __name__ == "__main__":
    main()
