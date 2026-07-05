# -*- coding: utf-8 -*-
"""
半導体 工程マップ定義（装置｜材料 の2カラム対比）
製造プロセス順に、各工程へ「装置メーカー」と「材料メーカー」を対応させる。
コードは themes.py の all_symbols() に存在するものを使用。

各工程: {"stage","name","desc","equip":[codes],"material":[codes]}
  stage: maker/design/front/back/facility
"""

ALIAS = {"TOK": "4186"}

PROCESS_MAP = [
    # ── メーカー(設計・製造の主体) ──
    {
        "stage": "maker", "name": "半導体メーカー", "icon": "🏭", "desc": "チップを生み出す主体",
        "groups": [
            {"label": "垂直統合(IDM)", "stocks": ["INTC", "MU", "6723"]},
            {"label": "ファブレス(設計のみ)", "stocks": ["NVDA", "AMD", "AVGO", "MRVL", "ARM"]},
            {"label": "ファウンドリ(受託製造)", "stocks": ["TSM", "GFS"]},
        ],
    },
    {
        "stage": "design", "name": "① 設計・開発(EDA)", "icon": "✏️", "desc": "回路を設計するソフト・IP",
        "equip": ["SNPS", "CDNS", "ARM"], "material": ["6526"],
    },
    # ── 前工程 ──
    {
        "stage": "front", "name": "② シリコンウェハー製造", "icon": "💿", "desc": "チップの土台となる円盤",
        "equip": ["3445"], "material": ["4063", "3436", "6890"],
    },
    {
        "stage": "front", "name": "③ フォトマスク製造", "icon": "🎭", "desc": "回路の原版(ハンコ)",
        "equip": ["6920"], "material": ["429A", "7741", "7911", "7912"],
    },
    {
        "stage": "front", "name": "④ 成膜(Deposition)", "icon": "🧱", "desc": "ウェハに薄い膜を積む",
        "equip": ["8035", "AMAT", "LRCX", "6525", "6728", "6387", "6590"],
        "material": ["4369", "4401", "4090"],
    },
    {
        "stage": "front", "name": "⑤ フォトレジスト塗布", "icon": "🖌️", "desc": "感光材を塗る(コータ)",
        "equip": ["8035", "7735"], "material": ["4186", "4005", "4901", "4970"],
    },
    {
        "stage": "front", "name": "⑥ 露光(リソグラフィ)", "icon": "💡", "desc": "回路設計図を焼き付ける",
        "equip": ["ASML", "7731", "7751", "6925"], "material": ["4021", "4187"],
    },
    {
        "stage": "front", "name": "⑦ エッチング(膜を除去)", "icon": "🔬", "desc": "不要な膜を削る",
        "equip": ["LRCX", "8035", "AMAT", "6387"],
        "material": ["4047", "4044", "4109", "4022"],
    },
    {
        "stage": "front", "name": "⑧ 洗浄(Clean)", "icon": "💧", "desc": "汚れ・微粒子を除去",
        "equip": ["7735", "8035", "ACMR"], "material": ["4091", "4368"],
    },
    {
        "stage": "front", "name": "⑨ 平坦化(CMP)", "icon": "⚙️", "desc": "表面を磨いて平らに",
        "equip": ["6361", "AMAT"], "material": ["4004", "4368", "4966"],
    },
    {
        "stage": "front", "name": "⑩ ウェハー検査・計測", "icon": "🔍", "desc": "欠陥・寸法を検査",
        "equip": ["6920", "KLAC", "ONTO", "NVMI", "BRKR", "KEYS", "7729", "7717"],
        "material": [],
    },
    # ── 後工程 ──
    {
        "stage": "back", "name": "⑪ ダイシング", "icon": "🔪", "desc": "ウェハをチップに切り分け",
        "equip": ["6146", "7729", "6338"], "material": ["6988"],
    },
    {
        "stage": "back", "name": "⑫ パッケージング", "icon": "📦", "desc": "GPUと一緒にABF基板に載せる",
        "equip": ["6315", "KLIC", "NDSN", "6227"],
        "material": ["4062", "2802", "4203", "4061", "4626", "7966", "6768"],
    },
    {
        "stage": "back", "name": "⑬ テスト", "icon": "✅", "desc": "良品/不良を判定",
        "equip": ["6857", "TER", "COHU", "FORM", "6871", "6855", "6941", "6627", "7729", "6337"],
        "material": [],
    },
    # ── ファシリティ(全工程を支える) ──
    {
        "stage": "facility", "name": "◆ 超純水・薬液・ガス供給", "icon": "🚰", "desc": "全工程を支えるインフラ",
        "equip": ["6370", "6368", "6254"],  # 栗田・オルガノ・野村マイクロ
        "material": ["4091", "6055", "ENTG", "LIN"],
    },
    {
        "stage": "facility", "name": "◆ クリーンルーム・空調", "icon": "🌀", "desc": "製造環境を作る",
        "equip": ["1979", "1969", "1980", "1961"], "material": [],
    },
    {
        "stage": "facility", "name": "◆ 装置部品・サブシステム", "icon": "🔩", "desc": "装置を構成する精密部品",
        "equip": ["6273", "6407", "6490", "6323", "6324", "6264", "6486", "6877", "MKSI", "ICHR", "UCTT", "AEIS"],
        "material": [],
    },
    {
        "stage": "facility", "name": "◆ 半導体商社", "icon": "🚚", "desc": "流通を担う",
        "equip": ["3132", "2760", "8154", "3156", "2737", "ARW", "AVT"], "material": [],
    },
]
