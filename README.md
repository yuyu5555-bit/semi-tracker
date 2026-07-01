# 半導体テーマトラッカー (米国 ⇌ 日本) — 個人用PWA

年初来の累積騰落率ラインで「どのテーマに資金が来て、どこから抜けたか」を一望するトラッカー。
8マクロテーマ → 約30サブテーマ(最下層) → 米国株 / 連動日本株 / 日本単独テーマ。91銘柄収録。

- データ源: Stooq 無料日足(APIキー不要)
- ホスティング: GitHub Pages(無料) / 自動更新: GitHub Actions が平日2回
- サーバー管理なし

## 構成
- `themes.py` … 銘柄マスタ(マクロ→サブ→米国/連動日本/日本単独)。**追加・変更はここだけ**
- `update_data.py` … Stooqから取得し年初来累積騰落率を計算 → `docs/data.json`
- `docs/` … PWA本体(index.html / manifest / sw.js / icons / data.json)
- `.github/workflows/update.yml` … 平日2回の自動更新

## セットアップ
1. GitHubでリポジトリ作成 → このフォルダを push
2. Settings → Pages → Source「Deploy from a branch」/ branch `main` / フォルダ `/docs`
3. Actions タブ → update-data → Run workflow(初回は手動。実データが入る)
4. `https://<ユーザー名>.github.io/<リポジトリ名>/` をスマホで開く → 「ホーム画面に追加」

以後は平日に自動更新。アプリは最新 data.json を読む。オフラインでも前回データ表示。

## 銘柄・テーマの追加
`themes.py` の `MACRO` を編集 → push。フロントは触らなくてよい(data.json経由で反映)。
- 米国株: `["NVDA","NVIDIA"]`
- 連動日本株: `["6857","アドバンテスト","連動理由"]`
- 日本単独: `["4186","東京応化工業","内容"]`

## ローカルで試す
```
python update_data.py
cd docs && python -m http.server 8000   # → http://localhost:8000
```

## 注意
- Stooq日足=終値ベース(リアルタイムではない)。秒単位ライブは有料データが必要。
- 2026年の株式分割銘柄(フジクラ/古河電工/住友電工/東エレ等)は分割調整の確認を推奨。
- 新規上場銘柄(キオクシア285A等)はStooqにデータが無い場合あり→画面下部に「取得失敗」表示。
- 個人利用専用。投資助言ではない。
