# -*- coding: utf-8 -*-
"""
半導体テーマトラッカー — 手動メンテはここ"だけ"(四半期に1回でOK)
================================================================
■ 全部自動で動くもの(手を触れない):
  今日の要注目 / 今週のハイライト / 今週の解説(自動) /
  最新ヘッドライン(RSS 13媒体) / 見出しの銘柄ハイライト /
  適時開示・決算(TDnet 保有銘柄・自動) / フロー図の今週n件

■ 手動はこの2つだけ:
  - MACRO.capex : 主要ファブの設備投資ガイダンス。数ヶ月変わらないので、
                  各社の決算/発表があった時に value・yoy_pct・note・as_of を更新。
  - MACRO.wsts  : WSTS/SIAの月次売上(毎月上旬発表)。series の末尾に1行足すだけ。
  ※ どちらも "as_of"(市況の更新日)を出すので、古くなったら一目で分かる。

■ 解説を自分で書きたい週だけ:
  WEEKLY の mode を "manual" にして title/body/start/end を記入(通常は "auto" のまま)。

編集 → GitHubにcommit → Actions で update-data を Run。以上。
※ CapEx/WSTS は 2026-07 時点の実データ。
"""

WEEKLY = {
    "mode": "auto",
    "title": "",
    "body": "",
    "start": "",
    "end": ""
}

MACRO = {
    "wsts": {
        "label": "WSTS 世界半導体売上(月次)",
        "unit": "十億ドル",
        "series": [
            {
                "month": "2025-11",
                "value": 75.3,
                "yoy_pct": 29.8
            },
            {
                "month": "2025-12",
                "value": 79.6,
                "yoy_pct": 34.0
            },
            {
                "month": "2026-01",
                "value": 82.5,
                "yoy_pct": 46.1
            },
            {
                "month": "2026-02",
                "value": 88.8,
                "yoy_pct": 61.8
            },
            {
                "month": "2026-03",
                "value": 99.5,
                "yoy_pct": 79.2
            },
            {
                "month": "2026-04",
                "value": 110.5,
                "yoy_pct": 93.9
            },
            {
                "month": "2026-05",
                "value": 120.6,
                "yoy_pct": 104.1
            }
        ]
    },
    "capex": {
        "label": "主要ファブ CapEx ガイダンス(2026年)",
        "as_of": "2026-07時点",
        "items": [
            {
                "company": "TSMC",
                "value": 54,
                "yoy_pct": 32,
                "note": "計画520〜560億ドル(前年409億ドル)。2nm・CoWoS増産"
            },
            {
                "company": "Samsung",
                "value": 74,
                "yoy_pct": 128,
                "note": "110兆ウォン(半導体CapEx+R&D)。HBM4・先端ファウンドリ"
            },
            {
                "company": "SK hynix",
                "value": 20,
                "yoy_pct": 40,
                "note": "前年比4割増(SC-IQ推計)。HBM4集中・Yongin前倒し"
            },
            {
                "company": "Micron",
                "value": 25,
                "yoy_pct": 81,
                "note": "広島含む増産。HBM専用ファブ増設"
            },
            {
                "company": "Intel",
                "value": 17,
                "yoy_pct": -4,
                "note": "横ばい〜微減。18A立ち上げ優先・投資選別"
            },
            {
                "company": "キオクシア",
                "value": 3.0,
                "yoy_pct": 66,
                "note": "FY26-28 年平均約4,700億円(前年比+66%)。BiCS 10・北上第2/第3棟。※円建てを$換算(150円/$概算)"
            },
            {
                "company": "GF",
                "value": 1.5,
                "yoy_pct": 100,
                "note": "2026は売上の15〜20%(前年8%から急増)。シリコンフォトニクス・22FDX需要"
            },
            {
                "company": "UMC",
                "value": 7.0,
                "yoy_pct": 0,
                "note": "年70億ドル超で成熟ノード能力増強。Intelと12nm FinFET協業"
            },
            {
                "company": "SMIC",
                "value": 7.3,
                "yoy_pct": 0,
                "note": "2025年並み(監査純資産の20%超)。中国内需・国産化が牽引"
            }
        ]
    },
    "cycle": "2026年市場はWSTS予測で前年比90%増の1兆5,112億ドル——初の1兆ドル超え。AIデータセンター向けのメモリ・ロジックが牽引する拡大フェーズ。業界CapExも約2,000億ドル(+20%)と過去最高で、装置・材料の受注環境は強い。リスクは2027年の米IT投資の持続性、対中規制、韓国大増産による中期の供給過剰。"
}
