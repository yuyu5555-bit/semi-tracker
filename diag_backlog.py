#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""受注残高(order backlog)の実データ構造を確認する診断スクリプト(読み取り専用)。

fetch_customers.py の実績あるEDINET取得部をそのまま流用する:
  _get / _api_key / collect_yuho_docids / _read_csv_from_zip / _rows_from_csv / _clean_cell

EDINETの有報CSVで「受注残高」がどの形で入るかは会社差が大きく、主に2系統:
  A) 数値ファクト行     … 要素ID末尾が OrderBacklog 等、項目名「受注残高」、値=数値
  B) テキストブロック   … 【生産、受注及び販売の状況】等のHTML/ベタ文字に受注残高が埋まる
この診断はA/B両方を分けてダンプし、どちらで来るか・会社ごとの表記ゆれを一発で確定させる。

state.json も backlog_display.json も書かない。純粋にログを吐くだけ。

実行:
  EDINET_API_KEY=xxxx python3 diag_backlog.py
"""
from __future__ import annotations
import re
import time
import urllib.parse

from fetch_customers import (
    API_BASE, _get, _api_key, collect_yuho_docids,
    _read_csv_from_zip, _rows_from_csv, _clean_cell,
)

# 診断対象(受注生産型=装置・検査・後工程・搬送の代表)。証券コード4桁。
TARGET_SECS = [
    "8035",  # 東京エレクトロン
    "7735",  # SCREEN
    "6920",  # レーザーテック
    "6728",  # アルバック
    "6857",  # アドバンテスト
    "7729",  # 東京精密
    "6146",  # ディスコ
    "6323",  # ローツェ
    "6315",  # TOWA
    "6266",  # タツモ
    "6590",  # 芝浦メカトロニクス
    "6227",  # AIメカテック
    "6258",  # 平田機工
    "6264",  # マルマエ
]

# 受注残まわりを拾うキーワード
KW_ITEM = ("受注残高", "受注残", "受注高", "受注実績", "受注")          # 数値ファクトの項目名
KW_TEXT = ("受注残高", "受注残", "生産、受注及び販売", "生産受注販売",
           "生産、受注および販売", "受注実績", "OrderBacklog", "Backlog")  # テキストブロック
KW_EID = ("orderbacklog", "ordersreceived", "backlog", "orders")        # 要素ID(英字)

NUM_RE = re.compile(r"^[\d,]+$")


def _vis(s: str, n: int = 1200) -> str:
    return s.replace("\r", "").replace("\n", " ⏎ ")[:n]


def diagnose(text: str, sec: str) -> None:
    try:
        rows = _rows_from_csv(text)
    except Exception as e:
        print(f"    [受注残:{sec}] CSV解析失敗: {type(e).__name__}: {e}")
        return

    # 本文に受注残の語があるか(まず存在確認)
    has_backlog = ("受注残" in text) or ("生産、受注" in text)
    print(f"    [受注残:{sec}] CSV行数={len(rows)} / 本文に受注残の語: {has_backlog}")

    numeric_hits = 0
    text_hits = 0
    for r in rows:
        if not r:
            continue
        eid = r[0]
        eid_l = eid.lower()
        item = r[1] if len(r) > 1 else ""
        val = r[-1] if r else ""   # EDINET CSVは値が常に最終列

        # --- A) 数値ファクト(項目名 or 要素IDが受注系、値が数値) ---
        is_num_fact = (
            (any(k in item for k in KW_ITEM) or any(k in eid_l for k in KW_EID))
            and val and NUM_RE.match(val.replace(" ", ""))
        )
        if is_num_fact:
            numeric_hits += 1
            if numeric_hits <= 8:
                ctx = r[3] if len(r) > 3 else ""      # 相対年度など
                unit = r[7] if len(r) > 7 else ""
                print(f"      [A数値] eid={eid} 項目名={item!r} 値={val!r} "
                      f"文脈={ctx!r} 単位={unit!r}")
            continue

        # --- B) テキストブロック(値に受注残/生産受注販売を含む) ---
        joined = "\t".join(r)
        if any(k in joined for k in KW_TEXT):
            text_hits += 1
            if text_hits <= 3:
                is_html = ("<tr" in val.lower()) or ("<td" in val.lower())
                print(f"      [Bテキスト] eid={eid} 項目名={item!r} HTML表={is_html}")
                print(f"          値(先頭1200字)= {_vis(val)}")
                # 受注残高の周辺だけ抜き出し(平文化して確認)
                plain = _clean_cell(val)
                idx = plain.find("受注残")
                if idx >= 0:
                    print(f"          '受注残'周辺= {_vis(plain[max(0, idx-40):idx+180], 240)}")

    print(f"    [受注残:{sec}] → 数値ファクト {numeric_hits}件 / テキストブロック該当 {text_hits}件\n")


def main() -> None:
    key = _api_key()
    if not key:
        return

    targets = set(TARGET_SECS)
    print(f"[受注残診断] 対象 {len(targets)}社の有報docIDを収集…")
    docids = collect_yuho_docids(key, targets, days_back=500, per_company=1)
    print(f"[受注残診断] docID取得: {len(docids)}社\n")

    for sec in TARGET_SECS:
        docs = docids.get(sec)
        if not docs:
            print(f"    [受注残:{sec}] 有報docIDが見つからない(対象期間に提出なし?)\n")
            continue
        doc = docs[0]
        url = (f"{API_BASE}/documents/{doc}?type=5"
               f"&Subscription-Key={urllib.parse.quote(key)}")
        try:
            text = _read_csv_from_zip(_get(url))
        except Exception as e:
            print(f"    [受注残:{sec}] 書類取得/解凍失敗: {type(e).__name__}: {e}\n")
            continue
        diagnose(text, sec)
        time.sleep(0.4)

    print("[受注残診断] 完了。上の [A数値]/[Bテキスト] を見て、"
          "受注残がどの形式で入るか確定できます。")


if __name__ == "__main__":
    main()
