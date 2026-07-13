# -*- coding: utf-8 -*-
"""
今日のAI分析 — 全銘柄の値動き・出来高・移動平均乖離をClaudeに読ませて
デイトレ/スイング候補・警戒銘柄・資金の流れを自動でまとめる。

必要な環境変数: ANTHROPIC_API_KEY (GitHub Secretsに登録)
呼び出しは update_data.py の実行時(1日1回)のみ。サイト訪問数とは無関係。
失敗しても株価更新は継続する(ここだけ握りつぶす設計)。
"""
from __future__ import annotations
import json
import os
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1200
TIMEOUT = 60


def _fmt_p(v):
    if v is None:
        return "–"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def _build_market_text(quotes: dict, updated: str) -> str:
    """quotes(株価辞書)から、Claudeに読ませる市況テキストを組み立てる。"""
    rows = []
    for sym, q in quotes.items():
        rows.append({"sym": sym, **q})

    lines = []
    lines.append(f"【半導体トラッカー 市場サマリー】データ日付:{(updated or '')[:10]}")
    lines.append("※終値ベース。デイトレ=今日の値動き・出来高、スイング=数日〜数週の押し目\n")

    def block(title, filt, key, top=8):
        items = [r for r in rows if filt(r)]
        items.sort(key=lambda r: (r.get(key) or 0), reverse=True)
        items = items[:top]
        lines.append(f"■ {title}")
        if not items:
            lines.append("  該当なし")
        for r in items:
            vr = r.get("volRatio")
            vr_txt = "–" if vr is None else f"{vr:.1f}x"
            lines.append(
                f"  {r['sym']} {r.get('name','')}: 前日比{_fmt_p(r.get('chg'))} / "
                f"出来高{vr_txt} / "
                f"5日線乖離{_fmt_p(r.get('dev5'))} / 25日線乖離{_fmt_p(r.get('dev25'))} / "
                f"RSI{r.get('rsi') if r.get('rsi') is not None else '–'}"
            )
        lines.append("")

    block("資金流入急増(出来高2倍↑＋上昇2%↑)",
          lambda r: r.get("daytrade") == "資金流入急増", "volRatio")
    block("初動の兆し(出来高1.5倍↑＋小幅上昇)",
          lambda r: r.get("daytrade") == "初動の兆し", "volRatio")
    block("押し目候補(25日線近辺・RSI中立)",
          lambda r: r.get("signal") == "押し目", "ret1m")
    block("直近下落(1ヶ月マイナス)",
          lambda r: (r.get("ret1m") or 0) < -5, "ret1m", top=6)
    block("過熱・天井警戒(25日線乖離大＋RSI高)",
          lambda r: r.get("signal") == "過熱", "dev25", top=6)

    return "\n".join(lines)


def build_prompt(quotes: dict, updated: str) -> str:
    header = (
        "あなたは半導体株に詳しい投資分析アシスタントです。"
        "以下は私の半導体テーマトラッカーから出力した本日の市場データです。"
        "これを読んで、次の観点で分析してください:\n\n"
        "【分析してほしいこと】\n"
        "1. 今日のデイトレ候補トップ3(資金流入・出来高急増を重視)。各銘柄の根拠を簡潔に。\n"
        "2. スイング候補トップ3(押し目・RSI・52週位置を考慮)。なぜ今が買い場か。\n"
        "3. 過熱・天井圏で手を出すと危険な銘柄があれば警告。\n"
        "4. 総括: 今日のセクター資金の流れを2〜3行で。\n\n"
        "※投資助言ではなく自己責任判断用の材料整理です。断定を避け、根拠とリスクを併記してください。"
        "全体で400字程度、日本語、見出し＋簡潔な箇条書きでまとめてください。\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    return header + _build_market_text(quotes, updated)


def generate_daily_analysis(quotes: dict, updated: str) -> dict:
    """Claude APIで今日の分析を生成。失敗時は空を返す(呼び出し側で握りつぶす)。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY 未設定 — AI分析はスキップ")
        return {}

    prompt = build_prompt(quotes, updated)
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        if not text:
            return {}
        return {"body": text.strip(), "generated_at": updated}
    except Exception as e:
        print(f"AI分析生成エラー: {e}")
        return {}


if __name__ == "__main__":
    # ローカル動作確認用(実データが無ければダミーで確認)
    dummy = {
        "8035": {"name": "東京エレクトロン", "chg": 2.5, "volRatio": 2.1, "dev5": 1.2,
                 "dev25": 3.0, "rsi": 55, "daytrade": "資金流入急増", "ret1m": 8.0},
    }
    res = generate_daily_analysis(dummy, "2026-07-11")
    print(res or "(APIキー未設定、または生成失敗)")
