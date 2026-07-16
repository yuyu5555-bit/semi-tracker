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

    厳格化(2026-07): 会計方針の長文などから会計用語(当連結会計年度 等)を
    誤って拾っていたため、
      1) 顧客テーブル専用の要素ID(MainCustomers かつ TextBlock)に限定
      2) 顧客名は「法人格・企業名らしい形」のものだけ採用
      3) 会計用語を明示的に除外
    """
    block = ""
    for line in text.split("\n"):
        # 顧客テーブルのテキストブロックだけを対象にする
        if "MainCustomers" not in line:
            continue
        if "TextBlock" not in line and "Table" not in line:
            continue
        cells = line.rstrip("\r").split("\t")
        if len(cells) < 2:
            continue
        cand = max(cells, key=len)
        if len(cand) > len(block):
            block = cand
    if not block:
        return []

    # 表構造を保って平文化
    t = block
    t = re.sub(r"</t[dh]>", "\t", t, flags=re.I)
    t = re.sub(r"</tr>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
         .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))

    # 会計用語・見出し(顧客名ではないもの)
    NG_WORDS = (
        "連結会計年度", "事業年度", "会計期間", "決算", "セグメント", "区分",
        "相手先", "顧客", "名称", "売上高", "売上", "金額", "合計", "主要",
        "減価償却", "償却", "残高", "減損", "損失", "利益", "百万円", "千円",
        "単位", "注記", "該当事項", "みなし", "取得", "及び", "内、", "台湾",
        "当期", "前期", "増加", "減少", "その他", "計", "％", "%",
    )

    def looks_like_company(s: str) -> bool:
        # 企業名らしい形か: 法人格を含む / アルファベット主体の社名 / カタカナ社名
        if any(w in s for w in ("株式会社", "㈱", "(株)", "有限会社",
                                "Ltd", "Inc", "Corp", "Co.", "LLC", "GmbH",
                                "Limited", "Company", "Holdings", "S.A.")):
            return True
        # アルファベット比率が高い(海外企業名)
        alpha = sum(c.isascii() and c.isalpha() for c in s)
        if alpha >= 4 and alpha >= len(s) * 0.5:
            return True
        # 全部カタカナ(3文字以上)の社名
        if len(s) >= 3 and re.fullmatch(r"[ァ-ヶー・\s]+", s):
            return True
        return False

    out = []
    for row in t.split("\n"):
        cells = [c.strip() for c in row.split("\t") if c.strip()]
        if len(cells) < 2:
            continue
        name = amount = ratio = None
        for c in cells:
            if amount is None and re.fullmatch(r"[\d,]{3,}", c):
                try:
                    amount = int(c.replace(",", ""))
                except ValueError:
                    pass
                continue
            if ratio is None and re.fullmatch(r"\d{1,2}\.\d+%?", c):
                try:
                    ratio = float(c.rstrip("%"))
                except ValueError:
                    pass
                continue
            if name is None and len(c) >= 3 and not re.fullmatch(r"[\d,.\s%△▲\-()]+", c):
                name = c
        if not name or amount is None:
            continue
        # 会計用語を含むものは除外
        if any(w in name for w in NG_WORDS):
            continue
        # 企業名らしい形でなければ除外
        if not looks_like_company(name):
            continue
        if amount < 100:
            continue
        out.append({"customer": name, "amount_raw": amount, "ratio_pct": ratio})

    best = {}
    for it in out:
        n = it["customer"]
        if n not in best or it["amount_raw"] > best[n]["amount_raw"]:
            best[n] = it
    items = sorted(best.values(), key=lambda x: -x["amount_raw"])[:6]
    return [{"customer": x["customer"],
             "amount_oku": round(x["amount_raw"] / 100, 1),
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
