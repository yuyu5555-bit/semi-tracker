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
    # ── 国内の新工場・投資案件(固有名詞。ここが逆引きの本命) ──
    "ラピダス": ["ラピダス", "Rapidus"],
    "JASM(TSMC熊本)": ["JASM", "TSMC熊本", "ＴＳＭＣ熊本", "熊本第一工場", "熊本第二工場", "菊陽町"],
    "キオクシア北上": ["北上工場", "キオクシア岩手", "K2 工場"],
    "キオクシア四日市": ["四日市工場", "キオクシア四日市"],
    "キオクシア": ["キオクシア", "Kioxia"],
    "マイクロン広島": ["マイクロン広島", "Micron広島", "広島工場"],
    "PSMC宮城(JSMC)": ["JSMC", "力晶", "PSMC", "SBIセミコンダクター"],
    "ルネサス": ["ルネサス", "Renesas"],
    "ローム": ["ローム", "ROHM"],
    "ソニーセミコン": ["ソニーセミコンダクタ", "ソニーセミコンダクタソリューションズ"],
    "三菱電機(パワー半導体)": ["三菱電機"],
    "富士電機": ["富士電機"],
    "サンケン電気": ["サンケン電気"],
    "三重富士通セミ": ["三重富士通", "JSファンダリ"],
    "TSMC": ["TSMC", "ＴＳＭＣ", "台湾積体電路", "Taiwan Semiconductor"],
    # ── 海外ファウンドリ / IDM / メモリ(供給先の固有名詞) ──
    "Samsung": ["Samsung", "サムスン", "三星電子", "サムスン電子"],
    "SK hynix": ["hynix", "ハイニックス", "ハイニクス", "SKハイニックス"],
    "Intel": ["Intel", "インテル"],
    "Micron": ["Micron", "マイクロン"],
    "GlobalFoundries": ["GlobalFoundries", "グローバルファウンドリーズ", "グローバルファウンダリーズ"],
    "UMC(聯華)": ["聯華電子", "ユナイテッド・マイクロエレクトロニクス"],
    "SMIC(中芯)": ["中芯国際", "SMIC"],
    "Nanya(南亜)": ["南亜科技", "ナンヤ"],
    "Winbond(華邦)": ["華邦電子", "Winbond"],
    "Western Digital": ["ウエスタンデジタル", "Western Digital"],
    "Infineon": ["インフィニオン", "Infineon"],
    "STマイクロ": ["STマイクロエレクトロニクス", "STMicroelectronics"],
    "Texas Instruments": ["テキサス・インスツルメンツ", "Texas Instruments"],
    "onsemi": ["オンセミ", "onsemi", "ON Semiconductor"],
    # ── 海外の装置 / 材料の主役(競合・提携先) ──
    "ASML": ["ASML", "エーエスエムエル"],
    "Applied Materials": ["Applied Materials", "アプライドマテリアルズ", "アプライド・マテリアルズ"],
    "Lam Research": ["Lam Research", "ラムリサーチ", "ラム・リサーチ"],
    "KLA": ["KLA", "ケーエルエー"],
    "Teradyne": ["Teradyne", "テラダイン"],
    "Entegris": ["Entegris", "インテグリス"],
    # ── AI / IT の最終顧客(固有名詞) ──
    "NVIDIA": ["NVIDIA", "エヌビディア"],
    "AMD": ["AMD", "エーエムディー"],
    "Apple": ["Apple", "アップル"],
    "Broadcom": ["Broadcom", "ブロードコム"],
    "Qualcomm": ["Qualcomm", "クアルコム"],
    "Google": ["Google", "グーグル", "Alphabet"],
    "Amazon(AWS)": ["Amazon", "アマゾン", "AWS"],
    "Microsoft": ["Microsoft", "マイクロソフト"],
    "Meta": ["Meta Platforms", "メタ・プラットフォームズ"],
    "Tesla": ["Tesla", "テスラ"],
    "OpenAI": ["OpenAI"],
    # ── 先端テーマ(件数が絞れる=識別力のある語だけ。SiC/GaN等の一般語は除外) ──
    "CoWoS": ["CoWoS", "ＣｏＷｏＳ"],
    "SoIC": ["SoIC"],
    "HBM": ["HBM", "広帯域メモリ"],
    "ガラス基板": ["ガラス基板", "ガラスコア", "glass substrate"],
    "GAA(ゲート全周)": ["ゲートオールアラウンド", "GAA", "ナノシート"],
    "バックサイド給電": ["バックサイド給電", "裏面電源", "BSPDN"],
    "チップレット": ["チップレット", "chiplet"],
    "High-NA EUV": ["High-NA", "ハイNA"],
    "ハイブリッドボンディング": ["ハイブリッドボンディング", "hybrid bonding", "ハイブリッドボンダー"],
    "パネルレベル(FOPLP)": ["パネルレベル", "FOPLP", "PLP"],
    "EUV": ["EUV", "極端紫外"],
    "先端パッケージ": ["先端パッケージ", "先端実装"],
}



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


_SENT_END = "。．！？!?\n\r"        # 文の区切り
_SCAN = 110                          # ヒット前後にたどる最大文字数
# 表を示すマーカー: 見出し語 or 詰まった数字連結(24,01819.3 のような表セルの結合)
_NUMRUN = re.compile(r"\d[\d,\.]{3,}\d")
_TABLE_WORD = ("割合(%)", "割合（％）", "(百万円)", "（百万円）", "(千円)", "（千円）",
               "販売高", "受注高", "生産高", "セグメントの名称",
               "顧客の名称", "相手先の名称", "主要な顧客ごとの情報")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\r", " ").replace("\n", " ")).strip("　 、,・)）(（ ")


def _readable_snippet(text: str, idx: int, kwlen: int) -> str:
    """ヒット位置を含む1文を切り出し、前後の表ノイズ(数字連結・見出し)を削って返す。"""
    a = idx
    while a > 0 and text[a - 1] not in _SENT_END and idx - a < _SCAN:
        a -= 1
    b = idx + kwlen
    while b < len(text) and text[b] not in _SENT_END and b - (idx + kwlen) < _SCAN:
        b += 1
    span = text[a:b]
    rel = idx - a
    # キーワード前: 最後の数字連結の直後で切る(表の値を落とす)
    cut = 0
    for m in _NUMRUN.finditer(span[:rel]):
        cut = m.end()
    # キーワード後: 最初の数字連結の手前で切る
    end = len(span)
    m = _NUMRUN.search(span, rel + kwlen)
    if m:
        end = m.start()
    return _clean(span[cut:end])


def _digit_noise_ratio(s: str) -> float:
    if not s:
        return 1.0
    noise = sum(c.isdigit() or c in "().,%％　 、,-−./／:：;" for c in s)
    return noise / len(s)


def _is_readable(s: str) -> bool:
    """人が読める"文"か。表の数字羅列・見出し行を弾く。"""
    if len(s) < 12:
        return False
    if any(w in s for w in _TABLE_WORD):
        return False
    if len(_NUMRUN.findall(s)) >= 2:          # 数字連結が複数=表
        return False
    if _digit_noise_ratio(s) > 0.4:
        return False
    # 述語/助詞が含まれる(=文らしい)
    if not any(p in s for p in ("は", "が", "を", "に", "と", "の", "で", "し", "する", "した",
                                "ている", "供給", "採用", "取引", "納入", "向け", "含ま", "占め")):
        return False
    return True


def find_mentions(text: str) -> dict[str, str]:
    """各案件キーワードについて「読める1文」のスニペットを返す。{案件名: snippet}
      - ラテン別名は語境界一致('gan'が'organ'に誤爆しない。HBMはHBM3Eに一致)
      - 表の数字羅列/見出しは弾き、文章での言及を優先
      - 読める文が無く表内のみの言及なら snippet="" (=表内言及。件数には数える)
    """
    low = text.lower()
    hits: dict[str, str] = {}
    for canon, aliases in KEYWORDS.items():
        best_snip = None
        found = False
        for al in aliases:
            positions: list[tuple[int, int]] = []
            if al.isascii():
                for m in re.finditer(r"(?<![a-z])" + re.escape(al.lower()) + r"(?![a-z])", low):
                    positions.append((m.start(), len(al)))
                    if len(positions) >= 40:
                        break
            else:
                st = 0
                while len(positions) < 40:
                    i = text.find(al, st)
                    if i < 0:
                        break
                    positions.append((i, len(al)))
                    st = i + len(al)
            for (i, l) in positions:
                found = True
                s = _readable_snippet(text, i, l)
                if _is_readable(s):
                    best_snip = s
                    break
            if best_snip:
                break
        if found:
            hits[canon] = best_snip or ""    # 読める文が無ければ空(表内言及)
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
