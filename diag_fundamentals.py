#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「依存構造」を有報から作れるか確認する診断(読み取り専用)。

狙う情報と、それが EDINET CSV で①数値タグ(標準要素=確実に取れる)か②テキストブロック
(顧客と同じくパース必要)かを、実データで確定させる:
  - 売上高(全体)          … 半導体純度の分母
  - セグメント情報        … 半導体セグメントの売上・利益(=純度)
  - 地域ごとの情報        … 海外売上比率・台湾/中国/韓国依存
  - 設備投資額            … capex の伸び(増産シグナル)
  - 研究開発費            … R&D比率

fetch_customers.py の EDINET 取得部(_get / collect_yuho_docids / _read_csv_from_zip /
_rows_from_csv)を流用。state も json も書かない。ログを吐くだけ。

実行: EDINET_API_KEY=xxxx python3 diag_fundamentals.py
"""
from __future__ import annotations
import re
import time
import urllib.parse

from fetch_customers import (
    API_BASE, _get, _api_key, collect_yuho_docids,
    _read_csv_from_zip, _rows_from_csv,
)

TARGET_SECS = [
    "8035",  # 東京エレクトロン
    "6146",  # ディスコ
    "6857",  # アドバンテスト
    "6920",  # レーザーテック
    "6323",  # ローツェ
    "7735",  # SCREEN
]

# 拾う項目のカテゴリ(項目名 or 要素IDに含まれる語)
CATS = {
    "売上": (("売上高",), ("netsales", "revenue")),
    "セグメント": (("セグメント",), ("segment",)),
    "地域": (("地域ごと", "所在地別", "海外売上", "地域別", "国又は地域"),
            ("oversea", "geographic", "bylocation", "areainformation")),
    "設備投資": (("設備投資", "有形固定資産の取得"), ("capitalexpenditure", "capitalexpenditures")),
    "R&D": (("研究開発費",), ("researchanddevelopment",)),
    "受注": (("受注残高", "受注高", "受注実績", "受注状況", "生産、受注及び販売"),
            ("orderbacklog", "ordersreceived", "ordersreceipt", "backlog")),
}

NUM_RE = re.compile(r"^-?[\d,]+(\.\d+)?$")


def _vis(s: str, n: int = 220) -> str:
    return re.sub(r"\s+", " ", s.replace("\r", " ").replace("\n", " ")).strip()[:n]


def _cat_of(item: str, eid_l: str) -> str | None:
    for cat, (jp, en) in CATS.items():
        if any(w in item for w in jp) or any(w in eid_l for w in en):
            return cat
    return None


def diagnose(text: str, sec: str) -> None:
    try:
        rows = _rows_from_csv(text)
    except Exception as e:
        print(f"    [依存:{sec}] CSV解析失敗: {type(e).__name__}: {e}")
        return
    print(f"    [依存:{sec}] CSV行数={len(rows)}")

    seen = {c: 0 for c in CATS}          # カテゴリ別の表示件数(絞る)
    tag_cnt = {c: 0 for c in CATS}       # 数値タグ件数
    tb_cnt = {c: 0 for c in CATS}        # テキストブロック件数
    for r in rows:
        if not r:
            continue
        eid = r[0]
        eid_l = eid.lower()
        item = r[1] if len(r) > 1 else ""
        cat = _cat_of(item, eid_l)
        if not cat:
            continue
        val = r[-1] if r else ""
        ctx = r[2] if len(r) > 2 else ""
        rel = r[3] if len(r) > 3 else ""
        unit = r[7] if len(r) > 7 else ""
        is_num = bool(val) and bool(NUM_RE.match(val.replace(" ", "")))
        is_tb = ("TextBlock" in eid) or (len(val) > 80 and not is_num)
        if is_num:
            tag_cnt[cat] += 1
        elif is_tb:
            tb_cnt[cat] += 1

        # カテゴリごとに数値は最大4件、テキストブロックは最大1件だけ表示
        show = (is_num and seen[cat] < 4) or (is_tb and tb_cnt[cat] == 1)
        if show:
            seen[cat] += 1
            kind = "数値" if is_num else ("テキスト" if is_tb else "その他")
            print(f"      [{cat}/{kind}] eid={eid}")
            print(f"          項目名={item!r} 値={_vis(val, 160)!r} 単位={unit!r} 年度={rel!r} ctx={ctx!r}")

    summ = " / ".join(f"{c}:数値{tag_cnt[c]}・文{tb_cnt[c]}" for c in CATS)
    print(f"    [依存:{sec}] → {summ}\n")


def main() -> None:
    key = _api_key()
    if not key:
        return
    targets = set(TARGET_SECS)
    print(f"[依存構造診断] 対象 {len(targets)}社の有報docIDを収集…")
    docids = collect_yuho_docids(key, targets, days_back=500, per_company=1)
    print(f"[依存構造診断] docID取得: {len(docids)}社\n")
    for sec in TARGET_SECS:
        docs = docids.get(sec)
        if not docs:
            print(f"    [依存:{sec}] 有報docIDなし\n")
            continue
        url = (f"{API_BASE}/documents/{docs[0]}?type=5"
               f"&Subscription-Key={urllib.parse.quote(key)}")
        try:
            text = _read_csv_from_zip(_get(url))
        except Exception as e:
            print(f"    [依存:{sec}] 取得失敗: {type(e).__name__}: {e}\n")
            continue
        diagnose(text, sec)
        time.sleep(0.4)
    print("[依存構造診断] 完了。各カテゴリが『数値』で取れれば標準タグ=確実、"
          "『文』が中心ならテキストブロックのパースが必要、と判定できます。")


if __name__ == "__main__":
    main()
