# -*- coding: utf-8 -*-
"""
主要顧客データの自動取得(EDINET公式API版)
==========================================
各銘柄が「どの顧客に依存しているか」を有価証券報告書から取得する。
TSMC決算・ASML決算などのイベント時に、影響を受ける銘柄を特定するために使う。

経緯(2026-07):
  当初IRBANKをスクレイピングしたが HTTP 403 Forbidden で全滅(bot判定)。
  金融庁のEDINET公式APIに切り替えた。APIキーがあれば正規利用者として扱われる。

仕組み:
  1. 書類一覧API で有価証券報告書(docTypeCode=120)のdocIDを収集
     - 有報は決算期末から3ヶ月以内提出。3月期決算が大半なので6月に集中する。
     - 日付ループで探す(過去1年分の主要提出日をカバー)
  2. 書類取得API(type=5) でCSV(ZIP)を取得
     - 中身は UTF-16LE のタブ区切り
  3. 「主要な顧客ごとの情報」(InformationForEachOfMainCustomers)の
     テキストブロックから顧客名と金額を抽出

必要な環境変数: EDINET_API_KEY (GitHub Secretsに登録)
出力: docs/customers.json
"""
from __future__ import annotations
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

TIMEOUT = 40
API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
UA = {"User-Agent": "semi-tracker/1.0 (personal research)"}

# 有報の提出が集中する期間(3月期決算 → 6月下旬に集中)。
# ここを重点的に探すことで、少ないリクエストで大半をカバーする。
DOC_TYPE_YUHO = "120"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _api_key() -> str:
    key = os.environ.get("EDINET_API_KEY", "").strip()
    if not key:
        print("    [顧客] EDINET_API_KEY が未設定。GitHub Secretsに登録してください。")
    return key


def collect_yuho_docids(key: str, target_secs: set[str], days_back: int = 400) -> dict[str, str]:
    """書類一覧APIを日付ループで叩き、対象銘柄の有報docIDを集める。
    返り値: {証券コード4桁: docID}  (同じ会社は最新のものを採用)
    """
    found: dict[str, tuple[str, str]] = {}  # sec -> (submitDate, docID)
    today = date.today()
    checked = hit_days = 0

    for i in range(days_back):
        d = today - timedelta(days=i)
        # 有報は平日にしか提出されない
        if d.weekday() >= 5:
            continue
        # 6〜7月(3月期の有報)と、それ以外の月末付近を重点的に見る
        # 全部見ると400リクエストになるので、有報が出る可能性が高い日に絞る
        if not (d.month in (6, 7) or d.day >= 25 or d.day <= 5):
            continue

        url = (f"{API_BASE}/documents.json?date={d:%Y-%m-%d}&type=2"
               f"&Subscription-Key={urllib.parse.quote(key)}")
        try:
            data = json.loads(_get(url).decode("utf-8"))
        except urllib.error.HTTPError as e:
            if checked < 2:
                print(f"    [顧客] 書類一覧 {d}: HTTP {e.code} {e.reason}")
            checked += 1
            continue
        except Exception as e:
            if checked < 2:
                print(f"    [顧客] 書類一覧 {d}: {type(e).__name__}: {e}")
            checked += 1
            continue
        checked += 1

        results = data.get("results") or []
        day_hit = 0
        for r in results:
            if r.get("docTypeCode") != DOC_TYPE_YUHO:
                continue
            if r.get("csvFlag") != "1":
                continue
            sec = (r.get("secCode") or "").strip()
            if not sec:
                continue
            sec4 = sec[:4] if len(sec) == 5 and sec.endswith("0") else sec
            if sec4 not in target_secs:
                continue
            sub = r.get("submitDateTime") or ""
            doc = r.get("docID") or ""
            if not doc:
                continue
            prev = found.get(sec4)
            if prev is None or sub > prev[0]:
                found[sec4] = (sub, doc)
                day_hit += 1
        if day_hit:
            hit_days += 1
        time.sleep(0.25)  # EDINETへの負荷軽減

        # 対象を全部見つけたら早期終了
        if len(found) >= len(target_secs):
            break

    print(f"    [顧客] 書類一覧: {checked}日分を確認 / 有報が見つかった日 {hit_days}日 "
          f"/ 対象銘柄の有報 {len(found)}件")
    return {sec: doc for sec, (_sub, doc) in found.items()}


def _read_csv_from_zip(raw: bytes) -> str:
    """EDINETのZIP(type=5)から、有報本文CSV(jpcrp)のテキストを取り出す。
    CSVは UTF-16LE のタブ区切り。
    """
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = [n for n in z.namelist()
                 if "XBRL_TO_CSV" in n and n.lower().endswith(".csv")
                 and "jpcrp" in n.lower()]
        if not names:
            return ""
        with z.open(names[0]) as f:
            return f.read().decode("utf-16", errors="ignore")


def _parse_customers_from_csv(text: str) -> list[dict]:
    """CSVから「主要な顧客ごとの情報」テキストブロックを見つけ、顧客と金額を抽出。

    EDINETのCSV(type=5)は UTF-16 のタブ区切りで、列は概ね:
      要素ID / 項目名 / コンテキストID / 相対年度 / 連結・個別 / 期間・時点 / ユニットID / 単位 / 値
    値の列(最後)に、有報本文のHTMLがそのまま入っている。
    """
    block = ""
    for line in text.split("\n"):
        if "MainCustomers" not in line and "主要な顧客" not in line:
            continue
        cells = line.rstrip("\r").split("\t")
        if len(cells) < 2:
            continue
        # 値は最終列に入るのが基本だが、念のため一番長いセルを本文とみなす
        cand = max(cells, key=len)
        if len(cand) > len(block):
            block = cand
    if not block:
        return []

    # HTMLを平文化。表のセル区切りを保持するため、tdの境界を区切り文字に変換する。
    t = block
    t = re.sub(r"</t[dh]>", "\t", t, flags=re.I)   # セル区切り → タブ
    t = re.sub(r"</tr>", "\n", t, flags=re.I)      # 行区切り → 改行
    t = re.sub(r"<[^>]+>", "", t)                  # 残りのタグを除去
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
         .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))

    out = []
    for row in t.split("\n"):
        cells = [c.strip() for c in row.split("\t") if c.strip()]
        if len(cells) < 2:
            continue
        # 行の中から「顧客名らしいセル」と「金額らしいセル」を拾う
        name = None
        amount = None
        ratio = None
        for c in cells:
            # 金額: カンマ区切りの3桁以上の数字
            if amount is None and re.fullmatch(r"[\d,]{3,}", c):
                try:
                    amount = int(c.replace(",", ""))
                except ValueError:
                    pass
                continue
            # 比率: 12.3 のような小数(％表記含む)
            if ratio is None and re.fullmatch(r"\d{1,2}\.\d+%?", c):
                try:
                    ratio = float(c.rstrip("%"))
                except ValueError:
                    pass
                continue
            # 顧客名: 数字だけでない、ある程度の長さの文字列
            if name is None and len(c) >= 3 and not re.fullmatch(r"[\d,.\s%△▲-]+", c):
                name = c
        if not name or amount is None:
            continue
        # 見出し行を除外
        if any(w in name for w in ("相手先", "顧客", "名称", "売上高", "金額", "合計",
                                   "セグメント", "区分", "当連結", "前連結", "主要")):
            continue
        if amount < 100:
            continue
        out.append({"customer": name, "amount_raw": amount, "ratio_pct": ratio})

    # 同じ顧客名は金額最大のものを残す
    best = {}
    for it in out:
        n = it["customer"]
        if n not in best or it["amount_raw"] > best[n]["amount_raw"]:
            best[n] = it
    items = sorted(best.values(), key=lambda x: -x["amount_raw"])[:6]
    return [{"customer": x["customer"],
             "amount_oku": round(x["amount_raw"] / 100, 1),   # 百万円 → 億円
             "year": None,
             "ratio_pct": x.get("ratio_pct")}
            for x in items]


def fetch_customers(docids: dict[str, str], key: str) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    ok = ng = empty = 0
    for i, (sec, doc) in enumerate(sorted(docids.items())):
        url = (f"{API_BASE}/documents/{doc}?type=5"
               f"&Subscription-Key={urllib.parse.quote(key)}")
        try:
            raw = _get(url)
        except urllib.error.HTTPError as e:
            ng += 1
            if ng <= 3:
                print(f"    [顧客] {sec}: 書類取得 HTTP {e.code} {e.reason}")
            continue
        except Exception as e:
            ng += 1
            if ng <= 3:
                print(f"    [顧客] {sec}: {type(e).__name__}: {e}")
            continue

        try:
            text = _read_csv_from_zip(raw)
        except Exception as e:
            ng += 1
            continue

        items = _parse_customers_from_csv(text)
        if items:
            for it in items:
                it["sec"] = sec
            result[sec] = items
            ok += 1
            if ok <= 5:  # 最初の5社だけ、何が抽出できたかログに出す
                names = [x["customer"] for x in items]
                print(f"    [顧客] {sec}: {names}")
        else:
            empty += 1
            if empty <= 2:
                # 顧客ブロック自体が見つかったかを診断
                has_block = ("MainCustomers" in text) or ("主要な顧客" in text)
                print(f"    [顧客] {sec}: 抽出0件 / 顧客ブロックの存在: {has_block}")
        time.sleep(0.4)  # EDINETへの負荷軽減

    print(f"    [顧客] 取得成功 {ok}社 / 顧客情報なし {empty}社 / 失敗 {ng}社")
    return result


def main():
    key = _api_key()
    if not key:
        return

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
    docids = collect_yuho_docids(key, jp_codes)
    if not docids:
        print("有報が1件も見つからなかったため中止")
        return

    customers = fetch_customers(docids, key)

    out = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customers": customers,
    }
    with open("docs/customers.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"docs/customers.json 更新: {len(customers)}社")

    # 確認用: TSMCを顧客に持つ銘柄
    tsmc = [(c, i) for c, its in customers.items() for i in its
            if "Taiwan Semiconductor" in i["customer"] or "TSMC" in i["customer"]]
    print(f"\nTSMCを主要顧客とする銘柄: {len(tsmc)}社")
    for c, i in sorted(tsmc, key=lambda x: -x[1]["amount_oku"])[:15]:
        print(f"  {c}: {i['amount_oku']}億円")


if __name__ == "__main__":
    main()
