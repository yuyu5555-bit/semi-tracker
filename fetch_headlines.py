#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RSS から半導体関連ヘッドラインを取得し data/headlines.json を生成する。

媒体(2026-07 時点):
  - Google News(各 site: 指定): 日経 / Bloomberg日本 / ロイター / CNBC / Reuters /
    WSJ / 日刊工業 / 東洋経済 / ダイヤモンド / マイナビ
  - 直接 RSS: XenoSpectrum半導体 / XenoSpectrum AI / 日経xTECH / ITmedia /
    EE Times Japan / DigiTimes / SemiEngineering / Tom's Hardware
  ※Bloombergは日本語版(bloomberg.co.jp)のみ。PC Watchは除外済み。

依存は標準ライブラリのみ(update_prices.py と同方針)。

実行例:
  python3 pipeline/fetch_headlines.py
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OUT_PATH = DATA_DIR / "headlines.json"
KEEP_DAYS = 14
MAX_ITEMS = 150   # ニュースのページ送り用に保存件数を拡大(旧72→150)
UA = (
    "Mozilla/5.0 (compatible; semi-tracker; +https://github.com/yuyu5555-bit/semi-tracker)"
)

# Google News 検索 URL を組み立てる
def _gnews(query: str, *, hl: str, gl: str, ceid: str) -> str:
    return (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + f"&hl={hl}&gl={gl}&ceid={ceid}"
    )


# (表示名, feed URL, タイトルに半導体キーワード必須か)
FEEDS: list[tuple[str, str, bool]] = [
    (
        "日経",
        _gnews("半導体 when:7d site:nikkei.com", hl="ja", gl="JP", ceid="JP:ja"),
        True,
    ),
    # --- 海外通信社: 日本語版(Google Newsが各社の日本語記事を配信) ---
    (
        "Bloomberg日本",
        _gnews("(半導体 OR TSMC OR エヌビディア OR メモリ OR 半導体株) site:bloomberg.co.jp",
               hl="ja", gl="JP", ceid="JP:ja"),
        True,
    ),
    (
        "ロイター",
        _gnews("半導体 when:7d site:jp.reuters.com", hl="ja", gl="JP", ceid="JP:ja"),
        True,
    ),
    (
        "CNBC",
        _gnews("semiconductor when:7d site:cnbc.com", hl="en-US", gl="US", ceid="US:en"),
        True,
    ),
    # --- 海外通信社: 英語版(速報性・網羅性のため残す) ---
    (
        "Reuters",
        _gnews("semiconductor when:7d site:reuters.com", hl="en-US", gl="US", ceid="US:en"),
        True,
    ),
    (
        "WSJ",
        _gnews("semiconductor when:7d site:wsj.com", hl="en-US", gl="US", ceid="US:en"),
        True,
    ),
    # --- 日本語メディア ---
    (
        "日刊工業",
        _gnews("半導体 when:7d site:nikkan.co.jp", hl="ja", gl="JP", ceid="JP:ja"),
        True,
    ),
    (
        "東洋経済",
        _gnews("半導体 when:7d site:toyokeizai.net", hl="ja", gl="JP", ceid="JP:ja"),
        True,
    ),
    (
        "ダイヤモンド",
        _gnews("半導体 when:7d site:diamond.jp", hl="ja", gl="JP", ceid="JP:ja"),
        True,
    ),
    (
        "マイナビ",
        _gnews("半導体 when:7d site:news.mynavi.jp", hl="ja", gl="JP", ceid="JP:ja"),
        True,
    ),
    # --- XenoSpectrum(半導体・AI特化の日本語ニュース。WordPressのタグ/トピック別RSS) ---
    (
        "XenoSpectrum半導体",
        "https://xenospectrum.com/tag/semiconductor/feed/",
        False,  # タグで既に半導体に絞られているのでキーワード必須にしない
    ),
    (
        "XenoSpectrum AI",
        "https://xenospectrum.com/topics/ai/feed/",
        False,  # AIトピックで絞られている
    ),
    (
        "日経xTECH",
        "https://xtech.nikkei.com/rss/index.rdf",
        True,
    ),
    (
        "ITmedia",
        "https://rss.itmedia.co.jp/rss/2.0/tf_electronics.xml",
        False,
    ),
    (
        "EE Times Japan",
        "https://rss.itmedia.co.jp/rss/2.0/eetimes.xml",
        False,
    ),
    (
        "DigiTimes",
        "https://www.digitimes.com/rss/daily.xml",
        True,
    ),
    (
        "SemiEngineering",
        "https://semiengineering.com/feed/",
        False,
    ),
    (
        "Tom's Hardware",
        "https://www.tomshardware.com/feeds/tag/semiconductors",
        False,
    ),
]

# 広域フィード用の半導体関連キーワード(小文字比較)
SEMICON_KEYWORDS = (
    "半導体",
    "semiconductor",
    "chip",
    "chips",
    "foundry",
    "fab",
    "wafer",
    "tsmc",
    "nvidia",
    "intel",
    "amd",
    "asml",
    "memory",
    "dram",
    "nand",
    "hbm",
    "gpu",
    "ai半導体",
    "パワー半導体",
    "soc",
    "fpga",
    "eda",
    "micron",
    "hynix",
    "samsung",
    "kioxia",
    "rapidus",
    "smic",
    "qualcomm",
    "broadcom",
    "lithography",
    "litho",
    "packaging",
    "substrate",
    "mcu",
    "mpu",
    "chipmaker",
    "chipmakers",
    "CoWoS",
    "CoPoS",
    "PLP",
    "FOPLP",
    "先端パッケージング",
    "ガラス基板",
    "次世代ガラス基板",
    "RDL",
    "TGV",
    "FCBGA",
    "ABF",
    "CCL",
    "銅張積層板",
    "異方性導電膜",
    "リードフレーム",
    "セラミックパッケージ",
    "封止材",
    "モールドレジン",
    "フリップチップボンダ",
    "インターポーザ",
    "光インターポーザ",
    "光電融合",
    "シリコンフォトニクス",
    "CPO",
    "光トランシーバ",
    "光エンジン",
    "マルチコアファイバー",
    "光ファイバー母材",
    "波長可変レーザー",
    "外部レーザー光源",
    "量子ドットレーザー",
    "IOWN",
    "光海底ケーブル",
    "EUV",
    "ArF",
    "KrF",
    "リソグラフィ",
    "ナノインプリント",
    "フォトレジスト",
    "フォトマスク",
    "CMP",
    "ALD",
    "CVD",
    "PVD",
    "スパッタリング装置",
    "イオン注入装置",
    "ドライエッチング装置",
    "枚葉式洗浄装置",
    "ウェハー接合装置",
    "露光装置",
    "成膜装置",
    "半導体前工程",
    "半導体中工程",
    "半導体製造装置",
    "超純水",
    "プローブカード",
    "テストソケット",
    "バーンインソケット",
    "メモリテスタ",
    "ダイサ",
    "OSAT",
    "ファイナルテスト",
    "ウェハテスト",
    "半導体テスト",
    "SiC",
    "インジウムリン",
    "化合物半導体",
    "高純度フッ素化合物",
    "特殊ガス",
    "CMPスラリー",
    "高周波デバイス",
    "MLCC",
    "ASIC",
    "PCB",
    "AMAT",
    "Rubin",
    "SK hynix",
    "SKハイニクス",
    "サムスン",
    "キオクシア",
    "UMC",
    "MediaTek",
    "メディアテック",
    "鴻海",
    "Foxconn",
    "日月光",
    "ASE",
    "力積電",
    "聯電",
    "世界先進",
    "Vanguard",
)


def _http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_pub_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw[: len(fmt) + 2], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    # Google News: "記事タイトル - 媒体名"
    title = re.sub(
        r"\s+[-–—]\s+(日本経済新聞|Reuters|ロイター|Bloomberg\.com|Bloomberg|CNBC|WSJ|The Wall Street Journal|日刊工業新聞|東洋経済オンライン|東洋経済)\s*$",
        "",
        title,
    )
    return title


def _is_noise(title: str) -> bool:
    lower = title.lower()
    return "stock price & latest news" in lower or lower.startswith("scia.pk")


def _matches_semiconductor(text: str) -> bool:
    lower = text.lower()
    return any(kw in text or kw in lower for kw in SEMICON_KEYWORDS)


def _item_id(url: str, title: str) -> str:
    digest = hashlib.sha256(f"{url}|{title}".encode()).hexdigest()[:16]
    return f"hl-{digest}"


def _append_item(
    out: list[dict],
    *,
    title: str,
    url: str,
    source: str,
    require_keywords: bool,
    pub_raw: str | None,
) -> None:
    title = _clean_title(title)
    url = url.strip()
    if not title or not url:
        return
    if _is_noise(title):
        return
    if require_keywords and not _matches_semiconductor(title):
        return
    pub = _parse_pub_date(pub_raw)
    out.append(
        {
            "id": _item_id(url, title),
            "date": pub.strftime("%Y-%m-%d") if pub else date.today().isoformat(),
            "title": title,
            "url": url,
            "source": source,
            "published_at": pub.isoformat() if pub else None,
        }
    )


def _parse_rss2(root: ET.Element, source: str, require_keywords: bool) -> list[dict]:
    channel = root.find("channel")
    if channel is None:
        channel = root

    out: list[dict] = []
    for item in channel.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        if title_el is None or link_el is None:
            continue
        _append_item(
            out,
            title="".join(title_el.itertext()).strip(),
            url="".join(link_el.itertext()).strip(),
            source=source,
            require_keywords=require_keywords,
            pub_raw="".join(pub_el.itertext()).strip() if pub_el is not None else None,
        )
    return out


def _parse_rdf(root: ET.Element, source: str, require_keywords: bool) -> list[dict]:
    ns = {
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rss": "http://purl.org/rss/1.0/",
        "dc": "http://purl.org/dc/elements/1.1/",
    }
    out: list[dict] = []
    for item in root.findall(".//rss:item", ns):
        title = (item.findtext("rss:title", default="", namespaces=ns) or "").strip()
        url = (item.findtext("rss:link", default="", namespaces=ns) or "").strip()
        pub_raw = (item.findtext("dc:date", default="", namespaces=ns) or "").strip() or None
        _append_item(
            out,
            title=title,
            url=url,
            source=source,
            require_keywords=require_keywords,
            pub_raw=pub_raw,
        )
    return out


def _parse_feed(xml_bytes: bytes, source: str, require_keywords: bool) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    if root.tag.endswith("RDF") or root.find("{http://purl.org/rss/1.0/}item") is not None:
        return _parse_rdf(root, source, require_keywords)
    return _parse_rss2(root, source, require_keywords)


def fetch_all() -> tuple[list[dict], list[str]]:
    merged: list[dict] = []
    notes: list[str] = []
    for source, url, require_keywords in FEEDS:
        try:
            xml_bytes = _http_get(url)
            items = _parse_feed(xml_bytes, source, require_keywords)
            merged.extend(items)
            notes.append(f"{source}: {len(items)}件")
        except Exception as exc:
            notes.append(f"{source}: 失敗 ({exc})")
            print(f"** {source}: {exc}", file=sys.stderr)
    return merged, notes


def dedupe_and_trim(items: list[dict], *, today: date) -> list[dict]:
    cutoff = today - timedelta(days=KEEP_DAYS)
    seen: set[str] = set()
    unique: list[dict] = []

    def sort_key(row: dict) -> tuple[str, str]:
        return (row.get("published_at") or row["date"], row["id"])

    for row in sorted(items, key=sort_key, reverse=True):
        try:
            row_date = date.fromisoformat(row["date"])
        except ValueError:
            continue
        if row_date < cutoff:
            continue
        key = re.sub(r"\?.*$", "", row["url"].rstrip("/"))
        norm_title = re.sub(r"\W+", "", row["title"].lower())
        dedupe_key = f"{key}|{norm_title}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        unique.append(row)
        if len(unique) >= MAX_ITEMS:
            break
    return unique


def build_payload(items: list[dict], notes: list[str]) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "_schema": (
            "id: 安定ID / date: YYYY-MM-DD / title: 見出し / url: 原文リンク / "
            "source: 媒体名 / published_at: ISO8601(任意)"
        ),
        "fetched_at": now.isoformat(),
        "sources": [name for name, _, _ in FEEDS],
        "fetch_notes": notes,
        "items": items,
    }


def main() -> int:
    items, notes = fetch_all()
    trimmed = dedupe_and_trim(items, today=date.today())
    if not trimmed:
        print("** ヘッドライン0件 — 既存ファイルを維持", file=sys.stderr)
        return 1

    payload = build_payload(trimmed, notes)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT_PATH} ({len(trimmed)} items)")
    print("; ".join(notes))
    return 0



# ===== update_data.py から使う便利関数 =====
def fetch_headlines(max_items: int = MAX_ITEMS) -> list[dict]:
    """RSS を取得し、半導体フィルタ済みの最新ヘッドラインを返す。"""
    items, notes = fetch_all()
    trimmed = dedupe_and_trim(items, today=date.today())[:max_items]
    return [
        {"source": r["source"], "title": r["title"], "url": r["url"], "date": r["date"]}
        for r in trimmed
    ]


if __name__ == "__main__":
    hs = fetch_headlines()
    print(f"取得 {len(hs)} 件")
    for h in hs[:10]:
        print(" ", h["date"], h["source"], h["title"][:50])
