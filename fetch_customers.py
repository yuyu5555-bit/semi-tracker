# -*- coding: utf-8 -*-
"""
主要顧客データの自動取得(年1回で十分)
=====================================
各銘柄が「どの顧客に依存しているか」を有価証券報告書ベースで取得する。
TSMC決算・ASML決算などのイベント時に、影響を受ける銘柄を特定するために使う。

データ源: IRBANK (irbank.net) — 有報の「主要な顧客ごとの情報」を構造化して公開している。
  URL形式: https://irbank.net/{EDINETコード}/customers
  例: 芝浦メカトロニクス(6590) → https://irbank.net/E01757/customers

証券コード → EDINETコードの変換には、金融庁が公開するEDINETコード一覧(CSV)を使う。
  https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip

出力: docs/customers.json
  {証券コード: [{customer, amount_oku, year, ratio_pct}, ...]}
"""
from __future__ import annotations
import csv
import io
import json
import re
import urllib.request
import zipfile
from datetime import datetime, timezone

TIMEOUT = 30
UA = {"User-Agent": "Mozilla/5.0 (semi-tracker customer fetcher)"}
EDINET_CODELIST = ("https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip")


def _get(url: str, decode: str | None = "utf-8") -> str | bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    return raw.decode(decode, errors="ignore") if decode else raw


def load_edinet_code_map() -> dict[str, str]:
    """金融庁のEDINETコード一覧から {証券コード4桁: EDINETコード} を作る。"""
    try:
        raw = _get(EDINET_CODELIST, decode=None)
    except Exception as e:
        print(f"    [顧客] EDINETコード一覧の取得失敗: {type(e).__name__}: {e}")
        return {}

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
            if not name:
                print("    [顧客] ZIP内にCSVが見つからない")
                return {}
            with z.open(name) as f:
                text = f.read().decode("cp932", errors="ignore")
    except Exception as e:
        print(f"    [顧客] ZIP展開失敗: {e}")
        return {}

    code_map = {}
    reader = csv.reader(io.StringIO(text))
    header_idx = None
    for row in reader:
        if not row:
            continue
        # ヘッダ行を探す(「ＥＤＩＮＥＴコード」「証券コード」を含む行)
        if header_idx is None:
            joined = "".join(row)
            if "ＥＤＩＮＥＴコード" in joined or "EDINETコード" in joined:
                for i, c in enumerate(row):
                    if "ＥＤＩＮＥＴ" in c or "EDINET" in c:
                        header_idx = i
                    if "証券コード" in c:
                        sec_idx = i
                continue
        if header_idx is None:
            continue
        try:
            edinet = row[header_idx].strip()
            sec = row[sec_idx].strip()
        except Exception:
            continue
        if not edinet.startswith("E") or not sec:
            continue
        # 証券コードは5桁(末尾0)で入っていることがある → 4桁に正規化
        sec4 = sec[:4] if len(sec) == 5 and sec.endswith("0") else sec
        if len(sec4) == 4:
            code_map[sec4] = edinet
    print(f"    [顧客] EDINETコード対応表: {len(code_map)}社")
    return code_map


def _parse_customers(md: str) -> list[dict]:
    """IRBANKの顧客ページ本文から、最新年の顧客リストを抽出。"""
    # 「## 2026年」のような年見出しで区切られ、その下に顧客名と金額が並ぶ
    sections = re.split(r"\n##\s*(\d{4})年\s*\n", md)
    if len(sections) < 3:
        return []
    # sections = [前置き, '2026', 本文, '2025', 本文, ...]
    year = sections[1]
    body = sections[2]

    out = []
    # 例: [Taiwan Semiconductor Manufacturing Company,Ltd.](...)  3月31日\n344億8200万円  1セグメント
    for m in re.finditer(
        r"\[([^\]]+)\]\([^)]*customers\?m=[^)]*\)[^\n]*\n\s*"
        r"([\d,]+)億(?:([\d,]+)万)?円(?:\s+対売上比([\d.]+)%)?",
        body,
    ):
        name = m.group(1).strip()
        oku = int(m.group(2).replace(",", ""))
        man = int(m.group(3).replace(",", "")) if m.group(3) else 0
        ratio = float(m.group(4)) if m.group(4) else None
        amount_oku = oku + man / 10000
        out.append({
            "customer": name,
            "amount_oku": round(amount_oku, 1),
            "year": int(year),
            "ratio_pct": ratio,
        })
    return out


def fetch_customers(codes: list[str], code_map: dict[str, str]) -> dict[str, list[dict]]:
    """指定銘柄の主要顧客を取得。"""
    result = {}
    ok = ng = nomap = 0
    for code in codes:
        edinet = code_map.get(code)
        if not edinet:
            nomap += 1
            continue
        url = f"https://irbank.net/{edinet}/customers"
        try:
            md = _get(url)
        except Exception as e:
            ng += 1
            print(f"    [顧客] {code}: 取得失敗 {type(e).__name__}")
            continue
        items = _parse_customers(md)
        if items:
            for it in items:
                it["edinet"] = edinet   # IRBANKへのリンク用
            result[code] = items
            ok += 1
    print(f"    [顧客] 取得成功 {ok}社 / 失敗 {ng}社 / EDINETコード無し {nomap}社")
    return result


def main():
    ns = {}
    exec(open("themes.py", encoding="utf-8").read(), ns)
    jp_codes = set()
    for m in ns["MACRO"]:
        for s in m["subs"]:
            for k in ("jp", "solo"):
                for e in s.get(k, []):
                    c = str(e[0])
                    if len(c) == 4 and c.isdigit():
                        jp_codes.add(c)

    print(f"[顧客データ] 対象: 日本株 {len(jp_codes)}銘柄")
    code_map = load_edinet_code_map()
    if not code_map:
        print("EDINETコード対応表が取れなかったため中止")
        return

    customers = fetch_customers(sorted(jp_codes), code_map)

    out = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customers": customers,
    }
    with open("docs/customers.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"docs/customers.json 更新: {len(customers)}社")

    # TSMCを顧客に持つ銘柄を表示(確認用)
    tsmc = [(c, i) for c, its in customers.items() for i in its
            if "Taiwan Semiconductor" in i["customer"] or "TSMC" in i["customer"]]
    print(f"\nTSMCを主要顧客とする銘柄: {len(tsmc)}社")
    for c, i in sorted(tsmc, key=lambda x: -x[1]["amount_oku"])[:15]:
        print(f"  {c}: {i['amount_oku']}億円 ({i['year']}年)")


if __name__ == "__main__":
    main()
