#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""セグメント別売上(主要事業＝半導体純度)を作れるか、テキストブロックの実内容を確認する
診断(読み取り専用)。

診断で「セグメント情報は数値タグではなくテキストブロック」と確定済み。そのブロックが
HTML表なのかベタ文字なのか、事業区分名と売上金額がどう並ぶのかを実データで見て、
パーサー方針を固める(顧客パーサーと同じ手順。憶測で書かない)。

fetch_customers.py の EDINET 取得部を流用。何も書き込まない。
実行: EDINET_API_KEY=xxxx python3 diag_segments.py
"""
from __future__ import annotations
import re
import time
import urllib.parse

from fetch_customers import (
    API_BASE, _get, _api_key, collect_yuho_docids,
    _read_csv_from_zip, _rows_from_csv,
)

TARGET_SECS = ["8035", "6146", "6920", "6323", "7735", "4062"]  # 装置・部材の代表

# セグメント情報のテキストブロックを示す語(要素ID/項目名)
SEG_EID = ("segmentinformation", "notessegment", "reportablesegments",
           "informationaboutreportablesegments")
SEG_ITEM = ("セグメント情報", "報告セグメント", "セグメントごとの", "セグメント情報等")


def _vis(s: str, n: int = 2000) -> str:
    return s.replace("\r", "").replace("\n", " ⏎ ")[:n]


def _strip_tags(html: str) -> str:
    s = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", s).strip()


def diagnose(text: str, sec: str) -> None:
    try:
        rows = _rows_from_csv(text)
    except Exception as e:
        print(f"    [SEG:{sec}] CSV解析失敗: {type(e).__name__}: {e}")
        return

    hits = 0
    for r in rows:
        if len(r) < 2:
            continue
        eid = r[0]
        eid_l = eid.lower()
        item = r[1]
        val = r[-1] if r else ""
        if not (any(e in eid_l for e in SEG_EID) or any(i in item for i in SEG_ITEM)):
            continue
        if len(val) < 40:                 # 中身のあるブロックだけ
            continue
        hits += 1
        if hits > 3:
            break
        is_html = ("<tr" in val.lower()) or ("<td" in val.lower())
        print(f"    [SEG:{sec}] eid={eid}")
        print(f"        項目名={item!r} / HTML表={is_html} / 長さ={len(val)}")
        if is_html:
            # 表のセルだけ順に並べて、事業区分と数値の並びを見る
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", val, re.S | re.I)
            cells = [_strip_tags(c) for c in cells]
            cells = [c for c in cells if c]
            print(f"        表セル(先頭60個)= {cells[:60]}")
        else:
            print(f"        ベタ文字(先頭1600字)= {_vis(_strip_tags(val), 1600)}")
        print()

    if hits == 0:
        print(f"    [SEG:{sec}] セグメント情報のテキストブロックが見つからない\n")


def main() -> None:
    key = _api_key()
    if not key:
        return
    targets = set(TARGET_SECS)
    print(f"[SEG診断] 対象 {len(targets)}社の有報docIDを収集…")
    docids = collect_yuho_docids(key, targets, days_back=500, per_company=1)
    print(f"[SEG診断] docID取得: {len(docids)}社\n")
    for sec in TARGET_SECS:
        docs = docids.get(sec)
        if not docs:
            print(f"    [SEG:{sec}] 有報docIDなし\n")
            continue
        url = (f"{API_BASE}/documents/{docs[0]}?type=5"
               f"&Subscription-Key={urllib.parse.quote(key)}")
        try:
            text = _read_csv_from_zip(_get(url))
        except Exception as e:
            print(f"    [SEG:{sec}] 取得失敗: {type(e).__name__}: {e}\n")
            continue
        diagnose(text, sec)
        time.sleep(0.4)
    print("[SEG診断] 完了。HTML表かベタ文字か、事業区分名と売上金額の並びを見て"
          "パーサー方針を決めます。")


if __name__ == "__main__":
    main()
