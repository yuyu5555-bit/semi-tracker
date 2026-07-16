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
import csv
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


# 匿名化された顧客表記（TSMC等の実名特定には使えないので除外）
_ANON = ("A社", "B社", "C社", "D社", "E社", "F社", "甲", "乙", "丙",
         "当社", "同社", "一部の顧客", "特定の顧客")


def _rows_from_csv(text: str) -> list[list[str]]:
    """EDINET CSV(TAB区切り)を、引用符で囲まれた"複数行の値"まで正しく解釈して行配列に。

    ★ここが現行バグの核心★
      旧実装は text.split("\n") で素朴に行分割していたが、EDINETの実CSVでは
      「主要な顧客ごとの情報」のHTML(表)が値セルに入り、その中に改行を含むため
      CSV仕様どおりダブルクォートで囲まれている。素朴分割だと表が複数の物理行に
      割れ、顧客名の<tr>行を取り逃して抽出0件になっていた。csvモジュールで解釈すれば
      引用符内の改行が1つのセルとして保持される。
    """
    return list(csv.reader(io.StringIO(text), delimiter="\t", quotechar='"'))


def _clean_cell(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&nbsp;", " ").replace("&#160;", " ").replace("&amp;", "&")
         .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", s).strip()


def _find_customer_value_cells(rows: list[list[str]]) -> list[tuple[str, str, str]]:
    """顧客テキストブロックの値セル(HTML)を列挙。要素IDの表記ゆれに対応。

    要素IDは会社/タクソノミ年度で揺れる:
      ...InformationAboutMajorCustomersTextBlock
      ...InformationForEachOfMainCustomersTextBlock  など
    → 「要素IDに Customer を含み Block を含む」または「項目名に 主要な顧客」で拾う。
    返り値: [(要素ID, 項目名, 値セルHTML), ...]
    """
    out = []
    for r in rows:
        if not r:
            continue
        eid = r[0]
        eid_l = eid.lower()
        item = r[1] if len(r) > 1 else ""
        is_cust = (("customer" in eid_l and "block" in eid_l)
                   or "主要な顧客" in item
                   or "主要な顧客ごとの情報" in "\t".join(r))
        if not is_cust:
            continue
        val = max(r, key=len)  # 値セル(いちばん長いセル=HTMLブロック)
        out.append((eid, item, val))
    return out


def _parse_html_table(html: str) -> list[list[str]]:
    """値セルのHTMLを <tr>=行 / <td>,<th>=セル として2次元配列に。"""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.I | re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.I | re.S)
        rows.append([_clean_cell(c) for c in cells])
    return rows


def _looks_like_company(s: str) -> bool:
    if s in _ANON:
        return False
    if any(w in s for w in ("株式会社", "㈱", "(株)", "（株）", "有限会社", "合同会社",
                            "Ltd", "Inc", "Corp", "Co.", "LLC", "GmbH", "N.V.",
                            "Limited", "Company", "Holdings", "S.A.", "PLC",
                            "Electronics", "Semiconductor", "Technolog")):
        return True
    alpha = sum(c.isascii() and c.isalpha() for c in s)
    if alpha >= 4 and alpha >= len(s) * 0.5:  # 海外社名(英字主体)
        return True
    if len(s) >= 4 and re.fullmatch(r"[ァ-ヶー・\s]+", s):  # カタカナ社名
        return True
    return False


def _unit_divisor(html: str) -> int:
    """表ヘッダ等の単位表記から、円→億円 の除数を決める。
    有報のセグメント情報はほぼ百万円。念のため千円/円も見る。
    """
    if "百万円" in html:
        return 100          # 百万円 → 億円
    if "千円" in html:
        return 100_000      # 千円 → 億円
    if re.search(r"[（(]円[）)]", html):
        return 100_000_000  # 円 → 億円
    return 100              # 既定: 百万円扱い


def _parse_customers_from_csv(text: str) -> list[dict]:
    """CSVから「主要な顧客ごとの情報」ブロックを見つけ、顧客名と金額を抽出。

    2026-07 修正:
      1) csvモジュールで引用符つき複数行の値を正しく1セルとして取得(核心バグ修正)
      2) 要素ID判定を MajorCustomers 等の表記ゆれに対応
      3) 値セルHTMLを表として解釈し、行ごとに(社名/金額/割合)を取り出す
      4) 匿名顧客(A社/B社等)や記載省略は自然に0件になる
    """
    if not text:
        return []
    rows = _rows_from_csv(text)
    cells = _find_customer_value_cells(rows)
    if not cells:
        return []

    out = []
    for _eid, _item, html in cells:
        div = _unit_divisor(html)
        for tr in _parse_html_table(html):
            name = amount = ratio = None
            for c in tr:
                if amount is None and re.fullmatch(r"[\d,]{2,}", c):
                    try:
                        amount = int(c.replace(",", ""))
                    except ValueError:
                        pass
                    continue
                if ratio is None and re.fullmatch(r"\d{1,2}(\.\d+)?%?", c):
                    try:
                        ratio = float(c.rstrip("%"))
                    except ValueError:
                        pass
                    continue
                if (name is None and len(c) >= 2
                        and not re.fullmatch(r"[\d,.\s%△▲\-（）()]+", c)):
                    name = c
            if not name or amount is None:
                continue
            if not _looks_like_company(name):
                continue
            oku = round(amount / div, 1)
            if oku < 1:  # 1億円未満は主要顧客として扱わない
                continue
            out.append({"customer": name, "amount_oku": oku, "ratio_pct": ratio})

    best = {}
    for it in out:
        n = it["customer"]
        if n not in best or it["amount_oku"] > best[n]["amount_oku"]:
            best[n] = it
    items = sorted(best.values(), key=lambda x: -x["amount_oku"])[:6]
    return [{"customer": x["customer"], "amount_oku": x["amount_oku"],
             "year": None, "ratio_pct": x.get("ratio_pct")}
            for x in items]


def _diagnose_customer_block(text: str, sec: str) -> None:
    """本番ログに"実際の顧客ブロックの生構造"を出す診断。
    これを1回本番で走らせれば、モックと実物のズレが一目で分かる。
    """
    # 1) 生テキスト: キーワード周辺をそのまま(改行は ⏎ で可視化)
    hit_kw = None
    for kw in ("主要な顧客", "MajorCustomers", "MainCustomers", "Customer"):
        idx = text.find(kw)
        if idx >= 0:
            hit_kw = kw
            snip = text[max(0, idx - 80): idx + 1400]
            snip = snip.replace("\r", "").replace("\n", " ⏎ ")
            print(f"    [診断:{sec}] キーワード '{kw}' 検出 @ {idx}")
            print(f"      生テキスト(先頭1400字): {snip[:1400]}")
            break
    if hit_kw is None:
        print(f"    [診断:{sec}] 顧客キーワードがCSV本文に見当たらない")

    # 2) CSV構造: 顧客関連の"行"を、要素ID・項目名・値の先頭とともに
    try:
        rows = _rows_from_csv(text)
    except Exception as e:
        print(f"    [診断:{sec}] csv解析で例外: {type(e).__name__}: {e}")
        return
    cells = _find_customer_value_cells(rows)
    print(f"    [診断:{sec}] 顧客ブロック該当セル: {len(cells)}件 / CSV総行数 {len(rows)}")
    for i, (eid, item, val) in enumerate(cells[:2]):
        vis = val.replace("\r", "").replace("\n", " ⏎ ")
        print(f"      #{i} 要素ID={eid!r} 項目名={item!r}")
        print(f"          値HTML(先頭900字)= {vis[:900]}")
        tbl = _parse_html_table(val)
        print(f"          表として解釈した行: {len(tbl)}行 / 例(先頭3行)={tbl[:3]}")


def fetch_customers(docids: dict[str, str], key: str) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    ok = ng = empty = 0
    diag_done = 0
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
        # --- 診断: 最初の数社は、抽出できてもできなくても生構造をログに出す ---
        # 本番の実CSV構造をこの1回で確定させるため。慣れたら DIAG_LIMIT=0 で消せる。
        DIAG_LIMIT = 6
        if diag_done < DIAG_LIMIT and (not items or diag_done < 3):
            _diagnose_customer_block(text, sec)
            diag_done += 1

        if items:
            for it in items:
                it["sec"] = sec
            result[sec] = items
            ok += 1
            if ok <= 8:  # 抽出できた社の顧客名をログに
                names = [x["customer"] for x in items]
                print(f"    [顧客] {sec}: {names}")
        else:
            empty += 1
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
