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


def collect_yuho_docids(key: str, target_secs: set[str],
                        days_back: int = 800, per_company: int = 3) -> dict[str, list[str]]:
    """書類一覧APIを日付ループで叩き、対象銘柄の有報docIDを"新しい順に最大per_company件"集める。
    返り値: {証券コード4桁: [docID(新しい順), ...]}

    2026-07 修正(取りこぼしの根治):
      1) 日付フィルタを撤廃 → 平日は全部見る。決算期は会社ごとに様々で
         (3月期=6月提出 / 2月期=5月提出 / 6月期=9月提出 / 12月期=3月提出 …)、
         月末縛りだと5/22提出(ローツェ等)を丸ごと取り逃していた。
      2) 早期終了を撤廃 → 全期間を見て各社の複数期を集める。
         (旧実装は全社が1件揃うと止まり、9月提出(AIメカ等)まで遡れていなかった)
      3) 各社 最新 per_company 件を返し、パース側が"新しい順に見て最初に顧客が
         取れた期"を採用できるようにする(最新にあれば最新、無ければ過去へ自動フォールバック)。
    """
    found: dict[str, list[tuple[str, str]]] = {}  # sec -> [(submitDateTime, docID), ...]
    today = date.today()
    checked = hit_days = 0

    for i in range(days_back):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:      # 有報は平日提出。土日だけ省く
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
            doc = r.get("docID") or ""
            if not doc:
                continue
            sub = r.get("submitDateTime") or ""
            lst = found.setdefault(sec4, [])
            if doc not in [x[1] for x in lst]:
                lst.append((sub, doc))
                day_hit += 1
        if day_hit:
            hit_days += 1
        time.sleep(0.2)  # EDINETへの負荷軽減

    out: dict[str, list[str]] = {}
    multi = 0
    for sec, lst in found.items():
        lst.sort(key=lambda x: x[0], reverse=True)   # 提出日時の新しい順
        out[sec] = [doc for _sub, doc in lst[:per_company]]
        if len(out[sec]) >= 2:
            multi += 1

    print(f"    [顧客] 書類一覧: {checked}日分を確認 / 有報が見つかった日 {hit_days}日 "
          f"/ 対象銘柄 {len(out)}社(うち複数期あり {multi}社)")
    return out


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
                            "Electronics", "Semiconductor", "Technolog",
                            # 日本語社名の手掛かり(セグメント名には出ない語に限定)
                            "グループ", "ホールディングス", "電力", "製作所",
                            "電機", "重工", "銀行", "商事", "自動車", "製鉄",
                            "鉱業", "電子", "精機", "通信", "ガス")):
        return True
    alpha = sum(c.isascii() and c.isalpha() for c in s)
    if alpha >= 4 and alpha >= len(s) * 0.5:  # 海外社名(英字主体)
        return True
    if len(s) >= 4 and re.fullmatch(r"[ァ-ヶー・\s]+", s):  # カタカナ社名
        return True
    return False


# 「記載を省略」型(=載せる顧客が無い。空が正常)を示す言い回し
_OMIT_MARKERS = (
    "記載を省略", "占める相手先はない", "占める相手先がない",
    "占める特定の顧客", "10％以上を占める外部顧客がいない",
    "10%以上を占める外部顧客がいない", "該当事項なし", "該当事項はありません",
    "顧客がいないため",
)

# ベタ文字列中の見出し・ラベル(顧客名ではない)。長い順に消す。
_CUST_LABELS = (
    "主要な顧客ごとの情報", "顧客の名称又は氏名", "顧客の名称若しくは氏名",
    "相手先の名称又は氏名", "関連するセグメント名", "顧客の名称", "相手先の名称",
    "名称又は氏名", "セグメント名", "相手先", "氏名", "名称", "売上高",
    "（単位：百万円）", "(単位：百万円)", "単位：百万円",
    "（単位:百万円）", "(単位:百万円)",
)


def _strip_labels(s: str) -> str:
    for w in _CUST_LABELS:
        s = s.replace(w, "")
    # 先頭の見出し番号・全角/半角スペース・記号を落とす
    return s.strip("　 　.．・:：0123456789０-９\t\r\n")


def _parse_plaintext_customers(plain: str, default_div: int) -> list[dict]:
    """区切り文字の無いベタ文字列(EDINETの実データはこれが多い)から、
    「主要な顧客ごとの情報」区画ごとに"先頭1社"を確実に取る。

    2社目以降はグルー文字列で社名とセグメント名の境界が曖昧になり誤爆するため、
    ここでは取りにいかない(取り逃しは許容・ゴミは出さない)。表形式の会社は
    _parse_html_table 側が複数社に対応する。前期/当期など複数区画は個別に処理し、
    同名は最大額で寄せる(=当期側が残る)。
    """
    out = []
    for m in re.finditer("主要な顧客ごとの情報", plain):
        start = m.end()
        # 区画の終端: 次の見出し/年度/区画のうち最も手前(無ければ+320字)
        ends = []
        for marker in ("【", "当連結会計年度", "前連結会計年度",
                       "報告セグメント", "主要な顧客ごとの情報", "関連情報"):
            j = plain.find(marker, start + 3)
            if j != -1:
                ends.append(j)
        end = min(ends) if ends else start + 320
        window = plain[start:min(end, start + 320)]

        # 省略型はスキップ(=顧客が無い。空が正常)
        if any(w in window for w in _OMIT_MARKERS):
            continue

        clean = _strip_labels(window)
        # 先頭の金額を探す(3桁以上、％表記や年号は除外)
        am = re.search(r"(?<![\d,])([1-9]\d{0,2}(?:,\d{3})+|\d{3,})(?!\s*[％%])"
                       r"(?!\s*年)(?!\s*月)(?!\s*日)", clean)
        if not am:
            continue
        name = _strip_labels(clean[:am.start()])
        # 社名の妥当性(先頭区画は位置で信頼できるが、最低限のノイズ除去)
        if len(name) < 2 or len(name) > 40:
            continue
        if any(w in name for w in ("記載", "省略", "該当", "情報", "セグメント",
                                    "単位", "注）", "(注", "百万円")):
            continue
        try:
            amount = int(am.group(1).replace(",", ""))
        except ValueError:
            continue
        div = _unit_divisor(window) or default_div
        oku = round(amount / div, 1)
        if oku < 1:
            continue
        out.append({"customer": name, "amount_oku": oku, "ratio_pct": None})
    return out


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
        # (a) HTML表として解釈できる会社(表形式の有報)
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
            if oku < 1:
                continue
            out.append({"customer": name, "amount_oku": oku, "ratio_pct": ratio})

        # (b) 区切りの無いベタ文字列(実データはこちらが多い)
        plain = _clean_cell(html)
        out.extend(_parse_plaintext_customers(plain, div))

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


DEBUG_SECS = {"6323", "6227"}   # 詳細ダンプする銘柄(ローツェ・AIメカ)


def fetch_customers(docids_multi: dict[str, list[str]], key: str,
                    debug_secs: set[str] = DEBUG_SECS) -> dict[str, list[dict]]:
    """各社の有報を"新しい順"にパースし、主要顧客が取れた最初の期を採用する。
    最新にあれば最新で確定(過去は見ない)、最新が空なら過去期へ自動フォールバック。
    """
    result: dict[str, list[dict]] = {}
    ok = ng = empty = 0
    latest_only = 0     # 最新期で取れた社数
    used_fallback = 0   # 最新が空で過去期で取れた社数
    empty_diag = 0

    for sec, docs in sorted(docids_multi.items()):
        dbg = sec in debug_secs
        got = None
        got_period = -1
        for pi, doc in enumerate(docs):
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
            except Exception:
                continue

            items = _parse_customers_from_csv(text)

            if dbg:
                has = ("主要な顧客" in text)
                print(f"    [顧客診断] {sec} 期{pi}(doc={doc}): 抽出{len(items)}件 / "
                      f"顧客ブロック{'あり' if has else 'なし'} / "
                      f"顧客={[x['customer'] for x in items]}")
                _diagnose_customer_block(text, sec)

            if items and got is None:
                got = items
                got_period = pi
                if not dbg:
                    break     # 通常は最初のヒットで確定(過去は見ない)
            time.sleep(0.35)

        if got:
            for it in got:
                it["sec"] = sec
            result[sec] = got
            ok += 1
            if got_period == 0:
                latest_only += 1
            else:
                used_fallback += 1
            if ok <= 10 or got_period > 0:
                tag = "最新" if got_period == 0 else f"過去{got_period}期前"
                print(f"    [顧客] {sec}: [{tag}] {[x['customer'] for x in got]}")
        else:
            empty += 1
            if empty_diag < 3 and not dbg and docs:
                try:
                    _dtxt = _read_csv_from_zip(_get(
                        f"{API_BASE}/documents/{docs[0]}?type=5"
                        f"&Subscription-Key={urllib.parse.quote(key)}"))
                    _diagnose_customer_block(_dtxt, sec)
                    empty_diag += 1
                except Exception:
                    pass
        time.sleep(0.2)

    print(f"    [顧客] 取得成功 {ok}社(最新期 {latest_only}社 / 過去期フォールバック {used_fallback}社) "
          f"/ 顧客情報なし {empty}社 / 失敗 {ng}社")
    print(f"    [顧客] ※もし旧実装(最新期のみ)なら {latest_only}社だった → 遡りで +{used_fallback}社")
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

    # ── 空上書き防止ガード ───────────────────────────────────────
    # 抽出が0社/極端に少ない回に当たっても、既存の customers.json を壊さない。
    # ルール: 今回の社数 < 既存の社数 なら書き換えない(=既存を維持)。
    # 年1更新のデータなので「減らさない」で十分安全。増えた時だけ更新される。
    prev_customers = {}
    try:
        with open("docs/customers.json", encoding="utf-8") as _pf:
            prev_customers = json.load(_pf).get("customers", {}) or {}
    except Exception:
        prev_customers = {}
    if len(customers) < len(prev_customers):
        print(f"[顧客] 空上書き防止: 今回 {len(customers)}社 < 既存 {len(prev_customers)}社 "
              f"→ customers.json は書き換えず既存を維持")
        # 参考ログだけ出して終了(ファイルは触らない=gitに差分が出ない)
        return

    out = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customers": customers,
    }
    with open("docs/customers.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"docs/customers.json 更新: {len(customers)}社(前回 {len(prev_customers)}社)")

    # 確認用: TSMCを顧客に持つ銘柄
    tsmc = [(c, i) for c, its in customers.items() for i in its
            if "Taiwan Semiconductor" in i["customer"] or "TSMC" in i["customer"]]
    print(f"\nTSMCを主要顧客とする銘柄: {len(tsmc)}社")
    for c, i in sorted(tsmc, key=lambda x: -x[1]["amount_oku"])[:15]:
        print(f"  {c}: {i['amount_oku']}億円")


if __name__ == "__main__":
    main()
