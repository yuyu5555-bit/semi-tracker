#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EDINET 有報/半期の本文を「ホット案件キーワード」で全文検索し、10%閾値と無関係に
"その案件に言及している銘柄"を逆引きする(案件発掘用)。

背景: 有報の「主要な顧客(10%以上)」は ①年1 ②10%未満は消える ③売上計上後、の3重遅れ。
これを補うため、監視中の半導体銘柄の開示本文を横断し、ラピダス/JASM/TSMC 等の
キーワードへの"言及の有無＋前後文"を拾う。金額ではなく発掘の手がかり。

EDINET公式APIに全文検索機能は無いため、fetch_customers.py の実績あるEDINET取得部
(collect_yuho_docids / _get / _read_csv_from_zip)を流用し、取得したCSV本文を自前で grep する。

制約(正直に):
 - 対象は監視中の半導体銘柄のみ(全上場4000社ではない)。半導体トラッカーには十分。
 - 拾えるのは「言及の有無＋前後文」。受注額ではない。
 - 画像PDF内の文字は取得対象外(EDINETのCSVはテキストのみ)。

出力: docs/mentions.json
 { "generated": ISO日時,
   "keywords": { 案件名: [ {sec, name, snippet}, ... ] },   # 案件→言及銘柄
   "by_stock": { sec: [案件名, ...] } }                      # 銘柄→言及案件
"""
from __future__ import annotations
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone

from fetch_customers import (
    API_BASE, _get, _api_key, collect_yuho_docids, _read_csv_from_zip,
)

# ─────────────────────────────────────────────────────────────
# キーワード辞書(案件名 → 表記ゆれの別名リスト)。ここを編集すれば増減できる。
# ラテン文字はケース無視で照合、日本語はそのまま照合する。
# ─────────────────────────────────────────────────────────────
KEYWORDS: dict[str, list[str]] = {
    # 国内の建設・投資案件(受注が発生する現場)
    "ラピダス": ["ラピダス", "Rapidus"],
    "JASM(TSMC熊本)": ["JASM", "TSMC熊本", "ＴＳＭＣ熊本", "熊本第一工場", "熊本第二工場"],
    "キオクシア北上": ["北上", "キオクシア岩手"],
    "キオクシア四日市": ["四日市", "キオクシア四日市"],
    "Micron広島": ["マイクロン広島", "Micron広島", "広島工場"],
    "PSMC宮城": ["PSMC", "力晶", "JSMC"],
    "ルネサス": ["ルネサス", "Renesas"],
    "ソニーセミコン": ["ソニーセミコンダクタ", "ソニーセミコンダクタソリューションズ"],
    # 海外主役(供給先)
    "TSMC": ["TSMC", "ＴＳＭＣ", "台湾積体電路", "Taiwan Semiconductor"],
    "Samsung": ["Samsung", "サムスン", "三星電子"],
    "SK hynix": ["SK hynix", "SKハイニックス", "エスケーハイニックス", "hynix"],
    "Intel": ["Intel", "インテル"],
    "NVIDIA": ["NVIDIA", "エヌビディア"],
    "Micron": ["Micron", "マイクロン"],
    "Applied Materials": ["Applied Materials", "アプライド マテリアルズ", "アプライドマテリアルズ"],
    "ASML": ["ASML", "エーエスエムエル"],
    "Lam Research": ["Lam Research", "ラムリサーチ"],
    # テーマ
    "CoWoS": ["CoWoS", "ＣｏＷｏＳ"],
    "HBM": ["HBM", "広帯域メモリ"],
    "先端パッケージ": ["先端パッケージ", "先端実装", "パネルレベルパッケージ", "PLP"],
    "EUV": ["EUV", "極端紫外線"],
    "パワー半導体SiC": ["SiC", "炭化ケイ素", "パワー半導体"],
    "GaN": ["GaN", "窒化ガリウム"],
}

SNIPPET_PAD = 60   # ヒット箇所の前後に取る文字数


def _load_target_secs() -> set[str]:
    """themes.py の監視銘柄(日本株4桁)を対象にする。fetch_customers と同じ範囲。"""
    ns: dict = {}
    exec(open("themes.py", encoding="utf-8").read(), ns)
    codes = set()
    for m in ns["MACRO"]:
        for s in m["subs"]:
            for k in ("jp", "solo"):
                for e in s.get(k, []):
                    c = str(e[0])
                    if len(c) == 4 and c.isdigit():
                        codes.add(c)
    return codes


def _clean_snippet(text: str, idx: int, kwlen: int) -> str:
    a = max(0, idx - SNIPPET_PAD)
    b = min(len(text), idx + kwlen + SNIPPET_PAD)
    s = text[a:b].replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()


def find_mentions(text: str) -> dict[str, str]:
    """本文テキストから、各案件キーワードの初出スニペットを返す。{案件名: snippet}"""
    low = text.lower()
    hits: dict[str, str] = {}
    for canon, aliases in KEYWORDS.items():
        for al in aliases:
            if al.isascii():                       # ラテン: ケース無視
                idx = low.find(al.lower())
            else:                                  # 日本語: そのまま
                idx = text.find(al)
            if idx >= 0:
                hits[canon] = _clean_snippet(text, idx, len(al))
                break
    return hits


def main() -> None:
    key = _api_key()
    if not key:
        return

    targets = _load_target_secs()
    print(f"[案件逆引き] 対象: 監視銘柄 {len(targets)}社 / キーワード {len(KEYWORDS)}件")
    docids = collect_yuho_docids(key, targets, days_back=500, per_company=1)
    print(f"[案件逆引き] 有報docID: {len(docids)}社ぶん取得。本文を全文検索します…")

    # 銘柄名(themes.pyから)。ログ/表示用。
    ns: dict = {}
    exec(open("themes.py", encoding="utf-8").read(), ns)
    name_of: dict[str, str] = {}
    for m in ns["MACRO"]:
        for s in m["subs"]:
            for k in ("jp", "solo"):
                for e in s.get(k, []):
                    c = str(e[0])
                    if len(c) == 4 and c.isdigit() and len(e) > 1:
                        name_of.setdefault(c, str(e[1]))

    keywords_out: dict[str, list[dict]] = {k: [] for k in KEYWORDS}
    by_stock: dict[str, list[str]] = {}
    ok = 0
    for sec in sorted(docids):
        docs = docids[sec]
        if not docs:
            continue
        url = (f"{API_BASE}/documents/{docs[0]}?type=5"
               f"&Subscription-Key={urllib.parse.quote(key)}")
        try:
            text = _read_csv_from_zip(_get(url))
        except Exception:
            continue
        hits = find_mentions(text)
        if hits:
            ok += 1
            by_stock[sec] = sorted(hits.keys())
            nm = name_of.get(sec, sec)
            for canon, snip in hits.items():
                keywords_out[canon].append({"sec": sec, "name": nm, "snippet": snip})
        time.sleep(0.35)

    # 件数の多い順に、各案件の言及銘柄数をログ(効果測定)
    print(f"[案件逆引き] 何らかの案件に言及した銘柄: {ok}社")
    for canon in sorted(keywords_out, key=lambda k: -len(keywords_out[k])):
        lst = keywords_out[canon]
        if lst:
            names = [x["name"] for x in lst][:8]
            print(f"    {canon}: {len(lst)}社  例: {names}")

    out = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "keywords": {k: v for k, v in keywords_out.items() if v},   # 0件の案件は省く
        "by_stock": by_stock,
    }
    with open("docs/mentions.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"docs/mentions.json 更新: 案件{len(out['keywords'])}件 / 言及銘柄{len(by_stock)}社")


if __name__ == "__main__":
    main()
