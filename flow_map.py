# -*- coding: utf-8 -*-
"""
フロービジュアル定義: 半導体製造の4ステージ
① 前工程(ウェハー製造ループ)
② 中工程・先端パッケージング
③ 後工程(検査・テスト)
④ 光電融合(CPO)

各ステージ = {key, name, icon, color, desc, visual, steps:[...]}
各工程(step) = {name, icon, desc, roles:[{label, codes:[...]}], visual(任意)}
  roles: 「装置」「材料」など役割ごとに銘柄コードをまとめる
  visual: 簡単な層構造などのビジュアルタイプ(フロント側で描画)
"""

FLOW = [
    {
        "key": "front",
        "name": "① 前工程(ウェハー製造)",
        "icon": "🔄",
        "color": "#3FA7D6",
        "desc": "1枚のウェハーに何層もの回路を焼き付ける。洗浄→成膜→露光→エッチング→平坦化を繰り返すループ工程。",
        "visual": "loop",  # ループを示す
        "steps": [
            {
                "name": "洗浄",
                "icon": "💧",
                "desc": "各工程の前後でウェハー表面の不純物を除去",
                "roles": [
                    {"label": "装置", "codes": ["ACMR", "7735", "8035"]},
                ],
            },
            {
                "name": "成膜(Deposition)",
                "icon": "🧪",
                "desc": "ウェハー表面に薄い膜を形成",
                "roles": [
                    {"label": "装置", "codes": ["AMAT", "LRCX", "8035", "6525", "6728", "6387"]},
                    {"label": "材料", "codes": ["4369", "4401", "4004"]},
                ],
            },
            {
                "name": "リソグラフィ(露光)",
                "icon": "💡",
                "desc": "回路パターンを光で焼き付ける最重要工程",
                "roles": [
                    {"label": "装置", "codes": ["ASML", "7731", "7751", "6925"]},
                    {"label": "材料(レジスト)", "codes": ["4186", "4063", "4901", "4187"]},
                    {"label": "マスク・検査", "codes": ["6920", "7741", "429A"]},
                ],
            },
            {
                "name": "エッチング(削る)",
                "icon": "🔬",
                "desc": "不要な膜を削り取り回路を形成",
                "roles": [
                    {"label": "装置", "codes": ["LRCX", "AMAT", "8035"]},
                    {"label": "材料(ガス/液)", "codes": ["4047", "4044", "4109", "4022"]},
                ],
            },
            {
                "name": "イオン注入・活性化",
                "icon": "⚡",
                "desc": "不純物を打ち込み半導体の性質を作る",
                "roles": [
                    {"label": "装置", "codes": ["AMAT"]},
                ],
            },
            {
                "name": "平坦化(CMP)",
                "icon": "🪞",
                "desc": "表面を磨いて平らにし次の層へ",
                "roles": [
                    {"label": "装置", "codes": ["AMAT", "6361"]},
                    {"label": "材料(スラリー)", "codes": ["5384", "4368", "4462"]},
                ],
            },
            {
                "name": "検査・計測(Metrology)",
                "icon": "🔎",
                "desc": "各層を1つも見逃さず監視(絶対防衛線)",
                "roles": [
                    {"label": "装置", "codes": ["KLAC", "ONTO", "NVMI", "BRKR", "KEYS", "7729", "7717"]},
                    {"label": "日本勢", "codes": ["6920"]},
                ],
            },
        ],
    },
    {
        "key": "middle",
        "name": "② 中工程・先端パッケージング",
        "icon": "📦",
        "color": "#E0A458",
        "desc": "異なるチップ(ロジック/GPU・HBM)を1つに統合。CoWoS→CoPoS(ガラス基板)へ進化中。上から【チップレット】【インターポーザー】【パッケージ基板/PCB】の3層構造が核心。",
        "visual": "package3",  # 3層構造(チップ/インターポーザー/基板)
        "steps": [
            {
                "name": "支持基板接合(Bonding)",
                "icon": "🔗",
                "desc": "ガラスキャリアでウェハーを支持",
                "roles": [
                    {"label": "装置・材料", "codes": ["5201", "8035"]},
                ],
            },
            {
                "name": "RDL形成(再配線層)",
                "icon": "🕸️",
                "desc": "微細な配線層をスパッタ/成膜で構築",
                "roles": [
                    {"label": "装置", "codes": ["8035", "6728", "AMAT", "ACMR"]},
                    {"label": "材料", "codes": ["4004", "5016"]},
                ],
            },
            {
                "name": "★インターポーザー(シリコン/ガラス)",
                "icon": "🔲",
                "desc": "GPUとHBMを繋ぐ中間層。CoPoSで四角いガラスパネル化が進む最重要領域",
                "roles": [
                    {"label": "シリコンIP", "codes": ["8035", "6146", "3436"]},
                    {"label": "ガラス基板・材料", "codes": ["5214", "5201", "7741", "7746", "3110"]},
                    {"label": "検査(ガラス対応)", "codes": ["6920", "7729", "KLAC", "ONTO"]},
                ],
            },
            {
                "name": "チップレット統合(Bonding)",
                "icon": "🧩",
                "desc": "GPUとHBMを高密度に接合",
                "roles": [
                    {"label": "装置", "codes": ["6315", "7731", "6146"]},
                    {"label": "検査", "codes": ["7729", "6857"]},
                ],
            },
            {
                "name": "封止(モールド)・パッケージング",
                "icon": "🛡️",
                "desc": "樹脂で保護し1パッケージ化",
                "roles": [
                    {"label": "装置", "codes": ["6315", "7735"]},
                    {"label": "材料", "codes": ["4203", "4061", "4004", "2802"]},
                ],
            },
            {
                "name": "支持基板剥離・研削",
                "icon": "✂️",
                "desc": "支持基板を外して薄化",
                "roles": [
                    {"label": "装置", "codes": ["7735", "6146", "6338"]},
                ],
            },
            {
                "name": "★パッケージ基板・PCB(土台)",
                "icon": "🟩",
                "desc": "パッケージの一番下の土台。CoPoSではガラスコア基板も候補",
                "roles": [
                    {"label": "基板(製品)", "codes": ["4062", "6787", "6971", "6958", "6837"]},
                    {"label": "素材(CCL/銅箔/ガラス)", "codes": ["4004", "4182", "3110", "4203", "5706", "5201", "5214"]},
                    {"label": "製造装置・薬品", "codes": ["6327", "6336", "6278", "4626", "4971", "4975", "6134", "6656"]},
                ],
            },
        ],
    },
    {
        "key": "back",
        "name": "③ 後工程(検査・テスト)",
        "icon": "✅",
        "color": "#5B9279",
        "desc": "完成した半導体を最終検査・テスト。品質と性能を評価して出荷。",
        "visual": "test",
        "steps": [
            {
                "name": "最終検査・テスト(ATE)",
                "icon": "🧪",
                "desc": "電気的特性を全数テスト",
                "roles": [
                    {"label": "テスタ", "codes": ["TER", "COHU", "FORM", "6857"]},
                    {"label": "プローブカード", "codes": ["6871", "6855", "6627"]},
                ],
            },
            {
                "name": "外観・寸法検査",
                "icon": "🔎",
                "desc": "欠陥や寸法を精密測定",
                "roles": [
                    {"label": "検査装置", "codes": ["KLAC", "ONTO", "7729", "6920"]},
                ],
            },
            {
                "name": "ダイシング・個片化",
                "icon": "🔪",
                "desc": "ウェハーをチップに切り分ける",
                "roles": [
                    {"label": "装置", "codes": ["6146", "6315", "6338"]},
                ],
            },
        ],
    },
    {
        "key": "cpo",
        "name": "④ 光電融合(CPO)",
        "icon": "🌈",
        "color": "#9D7CF0",
        "desc": "GPU・HBM・光エンジンを3D統合し、電気を光に変換。低電力・大容量化の次世代技術(NVIDIA Vera Rubin等)。",
        "visual": "cpo",  # 光電融合の立体構造
        "steps": [
            {
                "name": "CPO光エンジン結合",
                "icon": "💠",
                "desc": "電気→光変換エンジンをパッケージに統合",
                "roles": [
                    {"label": "光エンジン/部品", "codes": ["6524", "6834", "LITE", "COHR"]},
                ],
            },
            {
                "name": "外部レーザー光源(InP基板)",
                "icon": "🔆",
                "desc": "インジウムリン基板のレーザー光源",
                "roles": [
                    {"label": "デバイス", "codes": ["6613", "4980", "6777"]},
                    {"label": "InP材料", "codes": ["5016"]},
                ],
            },
            {
                "name": "シリコンフォトニクス層",
                "icon": "🌐",
                "desc": "光を通す配線層",
                "roles": [
                    {"label": "光配線・部品", "codes": ["5801", "5803", "5802", "6841"]},
                ],
            },
            {
                "name": "光ファイバー接続",
                "icon": "🧵",
                "desc": "パッケージ外部と光でつなぐ",
                "roles": [
                    {"label": "ファイバー/配線", "codes": ["5801", "5803", "5802", "6703", "6701", "6702", "5985"]},
                ],
            },
            {
                "name": "検点・テスト",
                "icon": "🔬",
                "desc": "CPOデバイスの精密測定・平坦化",
                "roles": [
                    {"label": "検査", "codes": ["6857", "7729", "6754", "6339", "6521"]},
                ],
            },
        ],
    },
]


def resolve_flow(all_symbols):
    """銘柄コードを{code,name,market}に解決してフロントに渡す形にする"""
    out = []
    for stage in FLOW:
        st = {"key": stage["key"], "name": stage["name"], "icon": stage["icon"],
              "color": stage["color"], "desc": stage["desc"],
              "visual": stage.get("visual"), "steps": []}
        for step in stage["steps"]:
            roles = []
            for role in step["roles"]:
                items = []
                for code in role["codes"]:
                    if code in all_symbols:
                        nm, mkt = all_symbols[code]
                        items.append({"code": code, "name": nm, "market": mkt})
                if items:
                    roles.append({"label": role["label"], "items": items})
            st["steps"].append({
                "name": step["name"], "icon": step["icon"],
                "desc": step["desc"], "roles": roles,
                "visual": step.get("visual"),
            })
        out.append(st)
    return out
