# -*- coding: utf-8 -*-
"""
半導体テーマトラッカー データ更新スクリプト
Stooq の無料CSVから日足を取得し、直近約1年分の日足(date,close)を
そのまま docs/data.json に格納する。期間別の累積騰落率はフロント側で
任意期間を切り出して計算する(10D/1M/2M/3M/6M/1Y/YTDを瞬時に切替可能)。

銘柄・テーマの編集は themes.py の MACRO を触るだけ。
実行: python update_data.py
"""
import csv, io, json, os, sys, time, urllib.request
from datetime import datetime, timezone
from themes import MACRO, all_symbols
try:
    from shares_jp import SHARES_JP
except Exception:
    SHARES_JP = {}
try:
    from linkage import build_linkage
except Exception:
    build_linkage = None
try:
    from process_map import PROCESS_MAP, ALIAS
except Exception:
    PROCESS_MAP, ALIAS = [], {}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
KEEP_DAYS = 280   # 保持する日足本数(約13ヶ月。1Y表示に余裕を持たせる)


def stooq_symbol(sym, market):
    return f"{sym.lower()}.jp" if market == "jp" else f"{sym.lower()}.us"


def yahoo_symbol(sym, market):
    # 日本株は ****.T 形式。米国株はそのまま。
    return f"{sym}.T" if market == "jp" else sym


def _http_get(url, timeout=30):
    headers = {
        "User-Agent": UA,
        "Accept": "text/csv,application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_stooq(sym, market):
    ssym = stooq_symbol(sym, market)
    for host in ["stooq.com", "stooq.pl"]:
        url = f"https://{host}/q/d/l/?s={ssym}&i=d"
        for _ in range(2):
            try:
                text = _http_get(url)
            except Exception:
                time.sleep(0.8); continue
            rows = list(csv.reader(io.StringIO(text)))
            if len(rows) < 2 or "Date" not in rows[0][0]:
                time.sleep(0.8); continue
            out = []
            for row in rows[1:]:
                if len(row) < 6:
                    continue
                try:
                    out.append((row[0], float(row[4]), float(row[5])))
                except ValueError:
                    continue
            out.sort(key=lambda x: x[0])
            if out:
                return out
    return None


def fetch_yahoo(sym, market):
    ysym = yahoo_symbol(sym, market)
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}"
           f"?range=1y&interval=1d&includeAdjustedClose=true")
    try:
        text = _http_get(url)
        data = json.loads(text)
        res = data["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
        closes = q["close"]
        vols = q.get("volume") or [None] * len(ts)
        # 調整後終値があれば優先(分割調整済み=Stooqと整合)
        adj = None
        try:
            adj = res["indicators"]["adjclose"][0]["adjclose"]
        except Exception:
            adj = None
        out = []
        for i, t in enumerate(ts):
            c = None
            if adj and i < len(adj) and adj[i] is not None:
                c = adj[i]
            elif i < len(closes) and closes[i] is not None:
                c = closes[i]
            if c is None:
                continue  # 未確定/欠損日はスキップ
            v = vols[i] if (i < len(vols) and vols[i] is not None) else 0.0
            d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
            out.append((d, float(c), float(v)))
        out.sort(key=lambda x: x[0])
        # 末尾が「出来高0」の未確定バーなら除く(場中の不完全データ対策)
        while len(out) >= 2 and out[-1][2] == 0:
            out.pop()
        return out or None
    except Exception:
        return None


def _latest_date(series):
    return series[-1][0] if series else None


def fetch_daily(sym, market):
    # 1) Stooq を試す
    out = fetch_stooq(sym, market)
    src = "stooq"
    # 2) Stooqが取れない、または最新日付が古い(土日除き5日以上前)ならYahooで取り直す
    stale = False
    if out:
        try:
            from datetime import date
            ld = datetime.strptime(_latest_date(out), "%Y-%m-%d").date()
            gap = (date.today() - ld).days
            if gap > 6:   # 直近データが1週間以上前 = 古い
                stale = True
        except Exception:
            pass
    if (not out) or stale:
        y = fetch_yahoo(sym, market)
        if y:
            # Stooqより新しければ採用
            if (not out) or (_latest_date(y) > _latest_date(out)):
                out = y
                src = "yahoo"
    if not out:
        print(f"  !! {sym}: Stooq/Yahoo 両方失敗", file=sys.stderr)
        return None
    # 鮮度ログ(古いままなら警告)
    note = " [STALE?]" if stale and src != "yahoo" else ""
    if src == "yahoo":
        print(f"  .. {sym}: Yahooで取得 (最新 {_latest_date(out)})", file=sys.stderr)
    elif stale:
        print(f"  ?? {sym}: データ古い 最新{_latest_date(out)}{note}", file=sys.stderr)
    return out


def _build_flow(symbols):
    """flow_map.pyのフロー定義を銘柄解決してdata.jsonに載せる"""
    try:
        from flow_map import resolve_flow
        return resolve_flow(symbols)
    except Exception as e:
        print("flow build skipped:", e)
        return []


def main():
    symbols = all_symbols()
    quotes, failed = {}, []
    for i, (sym, (name, market)) in enumerate(symbols.items()):
        print(f"[{i+1}/{len(symbols)}] {sym} {name}")
        daily = fetch_daily(sym, market)
        time.sleep(1.1)
        if not daily:
            failed.append(sym); continue
        daily = daily[-KEEP_DAYS:]
        closes = [c for _, c, _ in daily]
        vols = [v for _, _, v in daily]
        # 出来高率(相対) = 本日出来高 ÷ 過去20日平均出来高
        vol_today = vols[-1]
        past = vols[-21:-1] if len(vols) > 21 else vols[:-1]
        vol_avg = (sum(past) / len(past)) if past else 0
        vol_ratio = round(vol_today / vol_avg, 2) if vol_avg else None
        # 出来高急増レベル(25日平均比): 2倍/3倍/5倍で段階分け
        vol_surge = 0
        if vol_ratio is not None:
            if vol_ratio >= 5.0: vol_surge = 3   # 5倍以上=異常な急増
            elif vol_ratio >= 3.0: vol_surge = 2  # 3倍以上=強い急増
            elif vol_ratio >= 2.0: vol_surge = 1  # 2倍以上=急増
        # === ボリンジャーバンド(25日・2σ)とバンドウォーク判定 ===
        bb_walk = None  # "up"=上昇バンドウォーク, "down"=下降バンドウォーク
        bb_pctb = None  # %B(バンド内の位置。1.0=+2σ, 0=-2σ)
        if len(closes) >= 25:
            import statistics as _st
            win = closes[-25:]
            bb_mid = sum(win) / 25
            bb_sd = _st.pstdev(win)
            if bb_sd > 0:
                upper = bb_mid + 2 * bb_sd
                lower = bb_mid - 2 * bb_sd
                bb_pctb = round((closes[-1] - lower) / (upper - lower), 2)
                # バンドウォーク: 直近数日、株価が+2σ付近(%B>=0.8)に張り付いて上昇
                recent5 = closes[-5:]
                pctbs = []
                for i in range(-5, 0):
                    w = closes[i-24:i+1] if len(closes) >= 25 - i else None
                    if w and len(w) >= 25:
                        m = sum(w) / len(w); s = _st.pstdev(w)
                        if s > 0: pctbs.append((closes[i] - (m - 2*s)) / (4*s))
                if pctbs:
                    # 直近5日の大半が+2σ付近(%B>=0.8)＝上昇バンドウォーク
                    hi_cnt = sum(1 for p in pctbs if p >= 0.8)
                    lo_cnt = sum(1 for p in pctbs if p <= 0.2)
                    if hi_cnt >= 3 and closes[-1] > closes[-5]:
                        bb_walk = "up"
                    elif lo_cnt >= 3 and closes[-1] < closes[-5]:
                        bb_walk = "down"
        # 前日比(%)
        prev = closes[-2] if len(closes) >= 2 else None
        chg = round((closes[-1] / prev - 1) * 100, 2) if prev else None
        # 売買代金(終値×出来高) 本日 / 1週平均 / 1月平均（百万通貨単位）
        def turnover(c_list, v_list):
            return sum(c * v for c, v in zip(c_list, v_list)) / max(1, len(c_list))
        to_today = closes[-1] * vols[-1] / 1e6
        to_1w = turnover(closes[-5:], vols[-5:]) / 1e6
        to_1m = turnover(closes[-21:], vols[-21:]) / 1e6
        to_1w_ratio = round((to_today / to_1w - 1) * 100, 1) if to_1w else None
        to_1m_ratio = round((to_today / to_1m - 1) * 100, 1) if to_1m else None
        # 回転率(絶対) = 本日出来高 ÷ 発行済株式数（日本株のみ、%）
        turn = None
        mcap = None
        if market == "jp":
            sh = SHARES_JP.get(sym)  # 百万株
            if sh:
                turn = round(vol_today / (sh * 1e6) * 100, 2)  # %
                mcap = round(closes[-1] * sh / 1e3, 1)  # 億円 (株価×百万株/1000... 円×百万株=百万円→億は/100)
                mcap = round(closes[-1] * sh / 100, 0)  # 億円
        # === テクニカル指標(売り場/買い場判定用) ===
        # 25日移動平均と乖離率
        ma25 = sum(closes[-25:]) / min(25, len(closes)) if closes else None
        dev25 = round((closes[-1] / ma25 - 1) * 100, 1) if ma25 else None  # +なら上、-なら押し目
        ma75v = sum(closes[-75:]) / min(75, len(closes)) if closes else None
        # === パーフェクトオーダー(日足): 5日線>25日線>75日線 で全部上向き ===
        ma5v = sum(closes[-5:]) / min(5, len(closes)) if closes else None
        perfect_order = False       # 強気パーフェクトオーダー
        perfect_order_bear = False  # 弱気(下向き)パーフェクトオーダー
        if len(closes) >= 80 and ma5v and ma25 and ma75v:
            # 各線が上向きか(5日前と比べて上昇)
            ma5_prev = sum(closes[-10:-5]) / 5
            ma25_prev = sum(closes[-30:-5]) / 25
            ma75_prev = sum(closes[-80:-5]) / 75
            up_aligned = (ma5v > ma25 > ma75v)
            up_sloping = (ma5v > ma5_prev and ma25 > ma25_prev and ma75v > ma75_prev)
            if up_aligned and up_sloping:
                perfect_order = True
            down_aligned = (ma5v < ma25 < ma75v)
            down_sloping = (ma5v < ma5_prev and ma25 < ma25_prev and ma75v < ma75_prev)
            if down_aligned and down_sloping:
                perfect_order_bear = True
        # === 押し目(日足): 上昇基調で、各移動平均線に接近/軽く割った=買い場候補 ===
        # 5日線/25日線/75日線それぞれにバッファ(±約3%)を持たせて判定
        dev5v = round((closes[-1] / ma5v - 1) * 100, 1) if ma5v else None   # 5日線乖離
        dev75 = round((closes[-1] / ma75v - 1) * 100, 1) if ma75v else None  # 75日線乖離
        pullback = None
        pullback_ma = None  # どの線の押し目か(5/25/75)
        # 上昇基調の条件(中期上向き=25日線が75日線より上、または1ヶ月プラス)
        uptrend_base = (ma25 and ma75v and ma25 >= ma75v * 0.99)
        if uptrend_base:
            # 25日線の押し目を最優先(バッファ: -4%〜+2.5%で「線の近く」)
            if dev25 is not None and -4 <= dev25 <= 2.5:
                pullback = "25日線の押し目"
                pullback_ma = 25
            # 75日線の押し目(深い押し・バッファ -4%〜+3%)
            elif dev75 is not None and -4 <= dev75 <= 3:
                pullback = "75日線の押し目(深い)"
                pullback_ma = 75
            # 5日線の押し目(浅い押し・短期・バッファ -3%〜+1.5%)
            elif dev5v is not None and -3 <= dev5v <= 1.5:
                pullback = "5日線の押し目(浅い)"
                pullback_ma = 5
        # 1ヶ月(約21営業日)騰落率
        ret1m = round((closes[-1] / closes[-22] - 1) * 100, 1) if len(closes) >= 22 else None
        # 3ヶ月騰落率
        ret3m = round((closes[-1] / closes[-64] - 1) * 100, 1) if len(closes) >= 64 else None
        # RSI(14日)
        rsi = None
        if len(closes) >= 15:
            gains, losses = [], []
            for i in range(-14, 0):
                diff = closes[i] - closes[i-1]
                gains.append(max(diff, 0)); losses.append(max(-diff, 0))
            ag = sum(gains)/14; al = sum(losses)/14
            rsi = round(100 - 100/(1 + ag/al), 0) if al else 100
        # 52週高値からの位置(%)
        hi52 = max(closes[-250:]) if closes else None
        lo52 = min(closes[-250:]) if closes else None
        posPct = round((closes[-1] - lo52) / (hi52 - lo52) * 100, 0) if (hi52 and hi52 != lo52) else None
        # === デイトレ用 超短期指標 ===
        # 5日移動平均と乖離率(短期トレンド)
        ma5 = sum(closes[-5:]) / min(5, len(closes)) if closes else None
        dev5 = round((closes[-1] / ma5 - 1) * 100, 1) if ma5 else None
        # 直近3日・5日リターン
        ret3d = round((closes[-1] / closes[-4] - 1) * 100, 1) if len(closes) >= 4 else None
        ret5d = round((closes[-1] / closes[-6] - 1) * 100, 1) if len(closes) >= 6 else None
        # 連続上昇/下落日数(+n=n日連続上昇, -n=n日連続下落)
        streak = 0
        for i in range(len(closes)-1, 0, -1):
            if closes[i] > closes[i-1]:
                if streak >= 0: streak += 1
                else: break
            elif closes[i] < closes[i-1]:
                if streak <= 0: streak -= 1
                else: break
            else: break
        # 当日の値幅(高安レンジは無いので前日比で代用済み)
        # デイトレ妙味スコア: 出来高急増×当日上昇×短期過熱でない
        daytrade = None
        if vol_ratio is not None and chg is not None:
            if vol_ratio >= 2.0 and chg >= 2.0:
                daytrade = "資金流入急増"   # 今日出来高2倍以上＋上昇
            elif vol_ratio >= 1.5 and 0 < chg < 2.0:
                daytrade = "初動の兆し"
            elif vol_ratio >= 2.0 and chg <= -2.0:
                daytrade = "急落・リバ狙い"
        # シグナル判定
        signal = None
        if dev25 is not None and rsi is not None:
            if dev25 <= -8 and rsi <= 35:
                signal = "押し目"      # 25日線から大きく下＋売られすぎ
            elif dev25 <= -3 and rsi < 45:
                signal = "調整中"
            elif dev25 >= 8 and rsi >= 70:
                signal = "過熱"
            elif abs(dev25) <= 3:
                signal = "25日線付近"
        # === チャートパターン判定 ===
        pattern = None
        if len(closes) >= 60:
            cur = closes[-1]
            ma25v = ma25
            ma75 = sum(closes[-75:]) / min(75, len(closes))
            recent20_hi = max(closes[-20:])
            recent20_lo = min(closes[-20:])
            recent60_hi = max(closes[-60:])
            prior_hi = max(closes[-60:-5]) if len(closes) >= 65 else recent60_hi
            vol_up = (vol_ratio is not None and vol_ratio >= 1.3)

            # 局所ピーク・谷を検出するヘルパー(前後3日より高い/低い点)
            def local_peaks(arr, lo=False):
                pts = []
                for i in range(3, len(arr) - 3):
                    win = arr[i-3:i+4]
                    if (not lo and arr[i] == max(win)) or (lo and arr[i] == min(win)):
                        pts.append((i, arr[i]))
                return pts

            seg = closes[-60:]  # 直近60日でパターンを探す
            peaks = local_peaks(seg, lo=False)
            troughs = local_peaks(seg, lo=True)

            # 三尊(ヘッド&ショルダー天井): 高値3つで真ん中が一番高い→弱気転換
            santen = False
            if len(peaks) >= 3:
                last3 = peaks[-3:]
                l, m, r = last3[0][1], last3[1][1], last3[2][1]
                if m > l and m > r and abs(l - r) / m < 0.06:  # 両肩がほぼ同水準
                    santen = True
            # 逆三尊(ヘッド&ショルダー底): 安値3つで真ん中が一番安い→強気転換
            gyaku = False
            if len(troughs) >= 3:
                last3 = troughs[-3:]
                l, m, r = last3[0][1], last3[1][1], last3[2][1]
                if m < l and m < r and abs(l - r) / m < 0.06 and cur > m * 1.03:
                    gyaku = True
            # トリプルトップ(高値3つがほぼ同水準=横ばいレンジ天井→弱気)
            triple_top = False
            if len(peaks) >= 3 and not santen:
                last3 = [p[1] for p in peaks[-3:]]
                avg = sum(last3) / 3
                if all(abs(v - avg) / avg < 0.04 for v in last3) and cur < avg * 0.98:
                    triple_top = True
            # トリプルボトム(安値3つがほぼ同水準=横ばいレンジ底→強気)
            triple_bottom = False
            if len(troughs) >= 3 and not gyaku:
                last3 = [t[1] for t in troughs[-3:]]
                avg = sum(last3) / 3
                if all(abs(v - avg) / avg < 0.04 for v in last3) and cur > avg * 1.02:
                    triple_bottom = True
            # ダブルトップ(高値2つが同水準=M字→弱気)
            double_top = False
            if len(peaks) >= 2 and not santen and not triple_top:
                last2 = [p[1] for p in peaks[-2:]]
                if abs(last2[0] - last2[1]) / max(last2) < 0.04 and cur < min(last2) * 0.97:
                    # 2つの山の間に明確な谷があるか
                    pi = peaks[-2][0]
                    valley = min(seg[pi:]) if pi < len(seg) else cur
                    if valley < min(last2) * 0.94:
                        double_top = True
            # ダブルボトム(安値2つが同水準=W字→強気)
            double_bottom = False
            if len(troughs) >= 2 and not gyaku and not triple_bottom:
                last2 = [t[1] for t in troughs[-2:]]
                if abs(last2[0] - last2[1]) / max(last2) < 0.04 and cur > max(last2) * 1.03:
                    ti = troughs[-2][0]
                    peak_between = max(seg[ti:]) if ti < len(seg) else cur
                    if peak_between > max(last2) * 1.06:
                        double_bottom = True
            # フラッグ(急騰後の小さい横ばい/微調整→上抜け): 直近で一段上げ→数日レンジ→ブレイク
            flag = False
            if len(seg) >= 25:
                # 少し前(10〜20日前あたり)に急騰したか
                run_up = max(
                    (seg[-15] / seg[-25] - 1) if seg[-25] else 0,
                    (seg[-10] / seg[-20] - 1) if seg[-20] else 0,
                )
                box = seg[-10:]  # 直近10日のレンジ(調整局面)
                box_range = (max(box) - min(box)) / max(box) if max(box) else 1
                # 急騰8%以上 → その後レンジ12%以内に収まる → 今レンジ上限付近を上抜け
                if run_up >= 0.08 and box_range <= 0.12 and cur >= max(box[:-1]) * 0.985:
                    flag = True

            # 高値更新中(52週高値の98.5%以上)
            if hi52 and cur >= hi52 * 0.985:
                pattern = "52週高値更新(1年)"
            elif flag:
                pattern = "フラッグブレイク(1ヶ月)"
            elif gyaku:
                pattern = "逆三尊・底打ち(3ヶ月)"
            elif santen:
                pattern = "三尊・天井注意(3ヶ月)"
            elif triple_bottom:
                pattern = "トリプルボトム(3ヶ月)"
            elif triple_top:
                pattern = "トリプルトップ(3ヶ月)"
            elif double_bottom:
                pattern = "ダブルボトム(3ヶ月)"
            elif double_top:
                pattern = "ダブルトップ(3ヶ月)"
            # ブレイクアウト(直近60日高値を上抜け＋出来高増)
            elif cur >= prior_hi and vol_up and cur > recent20_hi * 0.99:
                pattern = "ブレイクアウト(3ヶ月)"
            # CWH(カップウィズハンドル): 深い谷から回復した形。2段階で判定
            elif len(closes) >= 40:
                cup_zone = closes[-40:-8]  # カップの底を探す区間
                cup_bottom = min(cup_zone) if cup_zone else None
                left_peak = max(closes[-60:-40]) if len(closes) >= 60 else recent60_hi  # カップ左の高値
                neckline = left_peak  # ネックライン=カップ左右の高値
                if (cup_bottom and cup_bottom <= recent60_hi * 0.82  # 深いカップがある
                        and ma25v):
                    pos = (cur - cup_bottom) / (neckline - cup_bottom) if neckline > cup_bottom else 0
                    # pos: カップ底=0, ネックライン=1.0
                    # 買い場(ハンドルの押し目 or ネックライン上抜け)
                    if cur >= neckline * 0.985 or (pos >= 0.72 and dev5 is not None and -6 <= dev5 <= 1):
                        pattern = "CWH押し目/うわ抜け"
                    elif pos >= 0.5:
                        pattern = "CWH形成中"
            # 新安値
            elif lo52 and cur <= lo52 * 1.02:
                pattern = "52週安値圏(1年)"
            # 上昇トレンド
            elif ma25v and cur > ma25v > ma75:
                pattern = "上昇トレンド(3ヶ月)"
            # 下降トレンド
            elif ma25v and cur < ma25v < ma75:
                pattern = "下降トレンド(3ヶ月)"
        quotes[sym] = {
            "name": name, "market": market,
            "last": daily[-1][1], "lastDate": daily[-1][0],
            "chg": chg,
            "volRatio": vol_ratio,
            "volSurge": vol_surge,  # 出来高急増レベル(0/1=2倍/2=3倍/3=5倍)
            "bbWalk": bb_walk,      # ボリンジャーバンドウォーク(up/down)
            "bbPctB": bb_pctb,      # %B(1.0=+2σ, 0=-2σ)
            "dev25": dev25,         # 25日線乖離率(%)
            "dev5": dev5,           # 5日線乖離率(%)
            "ret3d": ret3d,         # 3日リターン(%)
            "ret5d": ret5d,         # 5日リターン(%)
            "streak": streak,       # 連続上昇(+)/下落(-)日数
            "daytrade": daytrade,   # デイトレ妙味(資金流入急増/初動/急落リバ)
            "ret1m": ret1m,         # 1ヶ月騰落率(%)
            "ret3m": ret3m,         # 3ヶ月騰落率(%)
            "rsi": rsi,             # RSI(14)
            "posPct": posPct,       # 52週レンジ内の位置(%)
            "signal": signal,       # 押し目/調整中/過熱/25日線付近
            "pattern": pattern,     # 52週高値更新/ブレイクアウト/カップ&ハンドル/上昇トレンド等
            "po": perfect_order,    # パーフェクトオーダー(日足・強気)
            "poBear": perfect_order_bear,  # 逆パーフェクトオーダー(弱気)
            "pullback": pullback,   # 押し目(5日線/25日線/75日線)
            "pullbackMa": pullback_ma,  # どの線の押し目か(5/25/75)
            "daily": [[d, round(c, 3)] for d, c, _ in daily],
        }

    # 連動分析(テーマ+2%→翌日日本株)
    linkage = {}
    if build_linkage:
        try:
            linkage = build_linkage(MACRO, quotes)
        except Exception as e:
            print(f"  !! 連動分析失敗: {e}", file=sys.stderr)

    # 工程マップ(存在する銘柄だけ・名前解決)
    def resolve(codes):
        out = []
        seen = set()
        for c in codes:
            c = ALIAS.get(c, c)
            if c in quotes and c not in seen:
                out.append({"code": c, "name": quotes[c]["name"], "market": quotes[c]["market"]})
                seen.add(c)
        return out
    proc = []
    for step in PROCESS_MAP:
        item = {"stage": step["stage"], "name": step["name"], "desc": step.get("desc", ""), "icon": step.get("icon", "")}
        if "groups" in step:
            item["groups"] = [{"label": g["label"], "stocks": resolve(g["stocks"])} for g in step["groups"]]
        else:
            item["equip"] = resolve(step.get("equip", []))
            item["material"] = resolve(step.get("material", []))
        proc.append(item)

    out = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "failed": failed, "macro": MACRO, "quotes": quotes,
        "linkage": linkage,
        "process": proc,
        "flow": _build_flow(symbols),
    }
    os.makedirs("docs", exist_ok=True)
    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\nOK: {len(quotes)}銘柄 / 失敗 {len(failed)}件 {failed if failed else ''}")


if __name__ == "__main__":
    main()
