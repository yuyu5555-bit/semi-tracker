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
from datetime import datetime, timedelta, timezone
from themes import MACRO, all_symbols
try:
    from tags import TAGS as STOCK_TAGS
except Exception:
    STOCK_TAGS = {}
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
KEEP_DAYS = 460   # 保持する日足本数(約22ヶ月。1Y表示+200日MAのウォームアップ分)


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
                    # (date, close, vol, open, high, low)
                    out.append((row[0], float(row[4]), float(row[5]),
                                float(row[1]), float(row[2]), float(row[3])))
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
        out = []
        opens = q.get("open") or [None] * len(ts)
        highs = q.get("high") or [None] * len(ts)
        lows = q.get("low") or [None] * len(ts)
        for i, t in enumerate(ts):
            # 実株価(証券会社チャートと一致)を使う。adjcloseは配当遡及で
            # 実株価とズレるため終値には使わない。
            c = closes[i] if (i < len(closes) and closes[i] is not None) else None
            if c is None:
                continue  # 未確定/欠損日はスキップ
            v = vols[i] if (i < len(vols) and vols[i] is not None) else 0.0
            o = opens[i] if (i < len(opens) and opens[i] is not None) else c
            h = highs[i] if (i < len(highs) and highs[i] is not None) else c
            l = lows[i] if (i < len(lows) and lows[i] is not None) else c
            d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
            out.append((d, float(c), float(v), float(o), float(h), float(l)))
        out.sort(key=lambda x: x[0])
        # 末尾が「出来高0」の未確定バーなら除く(場中の不完全データ対策)
        while len(out) >= 2 and out[-1][2] == 0:
            out.pop()
        return out or None
    except Exception:
        return None


def detect_cwh(closes, highs, lows, vols):
    """カップウィズハンドル判定(形状優先・最小条件版)。
    見るのは「上昇→窪み→回復」の形だけ。日数・出来高・移動平均の縛りは撤廃。
    ① 上昇: カップ左端への上昇15%以上
    ② カップ: 左端から8〜45%の窪み→回復中 = "forming"
    ③ ハンドル: 右側が左端の90%以上まで戻った後、3〜20%の小さい押し = "handle"
    ④ 上抜け: 左端(ネックライン)超え = "breakout"
    返り値: None / "forming" / "handle" / "breakout" """
    n = len(closes)
    if n < 60:
        return None
    N = min(200, n - 1)
    cur = closes[-1]

    def H(b): return highs[n - 1 - b]
    def L(b): return lows[n - 1 - b]

    def max_high(b_from, b_to):
        best_p, best_b = -1.0, -1
        for b in range(b_from, min(b_to, N) + 1):
            if H(b) > best_p:
                best_p, best_b = H(b), b
        return best_p, best_b

    def min_low(b_from, b_to):
        best_p, best_b = float("inf"), -1
        for b in range(b_from, min(b_to, N) + 1):
            if L(b) < best_p:
                best_p, best_b = L(b), b
        return best_p, best_b

    # カップ左端 = 15〜200日前の最高値
    p_cl, b_cl = max_high(15, N)
    if b_cl < 0 or p_cl <= 0:
        return None
    # ① そこへの上昇15%以上(左端より前90日の最安値から)
    p_st, b_st = min_low(b_cl + 1, b_cl + 90)
    if b_st < 0 or p_st <= 0 or (p_cl / p_st - 1) * 100 < 15:
        return None
    # ② カップの底 = 左端より後の最安値。窪み8〜45%
    p_cb, b_cb = min_low(1, b_cl - 1)
    if b_cb < 0:
        return None
    depth = (p_cb / p_cl - 1) * 100
    if not (-45 <= depth <= -8):
        return None
    # 右側の回復高値(底より後の最高値)
    p_r, b_r = max_high(1, b_cb) if b_cb >= 1 else (-1.0, -1)

    # ④ 上抜け: 現値or当日高値がネックライン(左端)以上
    if b_r >= 0 and (cur >= p_cl * 0.995 or highs[-1] >= p_cl):
        return "breakout"
    # ③ ハンドル: 右側が左端の90%以上まで戻り、そこから3〜20%押してる
    if b_r >= 1 and p_r >= p_cl * 0.90:
        p_hb, _ = min_low(1, b_r - 1) if b_r >= 2 else (cur, 0)
        p_hb = min(p_hb, cur)
        pull = (p_hb / p_r - 1) * 100
        if -20 <= pull <= -3 and p_hb > p_cb:
            return "handle"
        # 右壁の上の方に張り付いてる(押し目未発生)のはまだ形成中扱い
    # ② カップ形成中: 底(3日以上前)から反発してるがネックライン未回復
    if b_cb >= 3 and cur >= p_cb * 1.05 and cur < p_cl * 0.98:
        return "forming"
    return None


def _aggregate_ohlc(daily, unit):
    """日足リスト[(date,close,vol[,open,high,low]),...]を週足/月足に集計。
    unit='W'(月曜起点週足) / 'M'(月足)。返り値: [[date,close,vol,open,high,low],...]"""
    import datetime as _dt
    out = []
    key = None
    cur = None
    for r in daily:
        d, c = r[0], r[1]
        v = r[2] if len(r) > 2 else 0
        o = r[3] if len(r) >= 6 else c
        h = r[4] if len(r) >= 6 else c
        l = r[5] if len(r) >= 6 else c
        if unit == "M":
            k = d[:7]
        else:
            y, m, dd_ = (int(x) for x in d.split("-"))
            dt = _dt.date(y, m, dd_)
            mon = dt - _dt.timedelta(days=dt.weekday())  # その週の月曜
            k = mon.isoformat()
        if k != key:
            if cur:
                out.append(cur)
            key = k
            cur = [d, round(c, 3), int(v) if v else 0, round(o, 3), round(h, 3), round(l, 3)]
        else:
            cur[1] = round(c, 3)                    # 終値=期間最後
            cur[2] += int(v) if v else 0            # 出来高=合計
            cur[3] = cur[3]                         # 始値=期間最初(据え置き)
            cur[4] = round(max(cur[4], h), 3)       # 高値=期間最大
            cur[5] = round(min(cur[5], l), 3)       # 安値=期間最小
            cur[0] = d                              # ラベル=期間最終日
    if cur:
        out.append(cur)
    return out


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
        # トリム前の全取得履歴の高値(参考値hiAllとして保持)
        hi_all = max((r[1] for r in daily), default=None)
        hist_days = len(daily)
        daily = daily[-KEEP_DAYS:]
        closes = [r[1] for r in daily]
        vols = [r[2] for r in daily]
        highs = [r[4] if len(r) >= 6 else r[1] for r in daily]
        lows = [r[5] if len(r) >= 6 else r[1] for r in daily]
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
        # === ボリンジャーバンド(25日)σゾーン判定 ===
        bb_pctb = None   # %B(バンド内の位置。1.0=+2σ, 0.5=中心, 0=-2σ)
        bb_zone = None   # "3σ+" / "2σ+" / "2σ-" / "3σ-" (バンド外への到達度)
        if len(closes) >= 25:
            import statistics as _st
            win = closes[-25:]
            bb_mid = sum(win) / 25
            bb_sd = _st.pstdev(win)
            if bb_sd > 0:
                cur_c = closes[-1]
                # 中心からの乖離を標準偏差の何倍か(z)で表す
                z = (cur_c - bb_mid) / bb_sd
                upper2, lower2 = bb_mid + 2 * bb_sd, bb_mid - 2 * bb_sd
                bb_pctb = round((cur_c - lower2) / (upper2 - lower2), 2)
                # ±2σ/±3σの外側到達を段階判定(3σが最優先)
                if z >= 3:
                    bb_zone = "3σ+"
                elif z >= 2:
                    bb_zone = "2σ+"
                elif z <= -3:
                    bb_zone = "3σ-"
                elif z <= -2:
                    bb_zone = "2σ-"
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
        ma50v = sum(closes[-50:]) / min(50, len(closes)) if closes else None
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
        dev50 = round((closes[-1] / ma50v - 1) * 100, 1) if ma50v else None  # 50日線乖離
        dev75 = round((closes[-1] / ma75v - 1) * 100, 1) if ma75v else None  # 75日線乖離
        pullback = None
        pullback_ma = None  # どの線の押し目か(5/25/50/75)

        # === 押し目判定(定義: 1つ上の速い線から落ちてきて、対象の遅い線に到達) ===
        # 図の定義どおり「25日線押し目=5日線の上にいた株価が下げて25日線に到達」。
        # 各線の階層: 5→25→50→75。lookback日前は上の線の上/近くにいて、今は対象線に到達。
        def ma_at(period, back):
            """back日前時点のperiod日移動平均(0=当日)"""
            end = len(closes) - back
            if end < period or end <= 0:
                return None
            return sum(closes[end - period:end]) / period

        def price_at(back):
            i = len(closes) - 1 - back
            return closes[i] if 0 <= i < len(closes) else None

        def touched_from_above(target_period, upper_period, lookback=7):
            """upper_period線の上(or近傍)にいた株価が下げてきて、今target_period線に到達したか。
            条件: ①今、株価がtarget線の近傍(-3.5%〜+1.5%)
                  ②過去lookback日内のどこかで、株価がupper線以上に十分離れていた(=上から落ちてきた)
                  ③その後、株価が下向きに推移して今の位置に来た"""
            ma_t = sum(closes[-target_period:]) / target_period if len(closes) >= target_period else None
            if ma_t is None:
                return False
            dev_now = (closes[-1] / ma_t - 1) * 100
            if not (-3.5 <= dev_now <= 1.5):        # ①対象線に到達(近傍)
                return False
            # ②過去lookback日でupper線から十分上(+2%以上)にいた瞬間があるか
            was_above = False
            for b in range(2, lookback + 1):
                mu = ma_at(upper_period, b)
                pv = price_at(b)
                if mu and pv and (pv / mu - 1) * 100 >= 1.5:
                    was_above = True
                    break
            if not was_above:
                return False
            # ③直近が下降(数日前の株価 > 今の株価 = 落ちてきた)
            p_prev = price_at(3)
            return p_prev is not None and p_prev > closes[-1]

        uptrend_base = (ma25 and ma75v and ma25 >= ma75v * 0.99)
        if uptrend_base:
            # 5日線の上→25日線に到達(最も基本的な押し目。図の定義)
            if touched_from_above(25, 5):
                pullback = "25日線の押し目"
                pullback_ma = 25
            # 25日線の上→50日線に到達
            elif touched_from_above(50, 25):
                pullback = "50日線の押し目"
                pullback_ma = 50
            # 50日線の上→75日線に到達(深い押し)
            elif touched_from_above(75, 50):
                pullback = "75日線の押し目(深い)"
                pullback_ma = 75
            # 株価の上→5日線に到達(浅い押し。直近高値から落ちてきたか)
            elif dev5v is not None and -3 <= dev5v <= 1.5:
                recent_hi5 = max(closes[-6:-1]) if len(closes) >= 6 else closes[-1]
                if recent_hi5 > closes[-1] * 1.02:
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
            # ＋プロミネンス: 直近の谷(または山)から min_prom 以上離れた「明確な」山谷だけ採用
            def local_peaks(arr, lo=False, min_prom=0.02):
                raw = []
                for i in range(3, len(arr) - 3):
                    win = arr[i-3:i+4]
                    if (not lo and arr[i] == max(win)) or (lo and arr[i] == min(win)):
                        raw.append((i, arr[i]))
                pts = []
                for i, v in raw:
                    left = arr[max(0, i-10):i]
                    right = arr[i+1:i+11]
                    if not left or not right:
                        continue
                    if lo:
                        prom = min(max(left), max(right)) / v - 1  # 谷: 両側の戻り幅
                    else:
                        prom = 1 - max(min(left), min(right)) / v  # 山: 両側の押し幅
                    if prom >= min_prom:
                        pts.append((i, v))
                return pts

            seg = closes[-60:]  # 直近60日でパターンを探す
            seg_hi, seg_lo = max(seg), min(seg)
            peaks = local_peaks(seg, lo=False)
            troughs = local_peaks(seg, lo=True)
            # トレンド判定(パターンの前提条件に使う)
            uptrend = ma25v is not None and cur > ma25v > ma75      # 上昇中
            downtrend = ma25v is not None and cur < ma25v < ma75    # 下降中

            # 三尊(ヘッド&ショルダー天井): 高値3つで真ん中が一番高い→弱気転換
            # 前提: 上昇して天井を付けた形。ヘッドはレンジ上限付近＆下降中には出さない
            santen = False
            if len(peaks) >= 3 and not downtrend:
                last3 = peaks[-3:]
                l, m, r = last3[0][1], last3[1][1], last3[2][1]
                if (m > l and m > r and abs(l - r) / m < 0.06        # 両肩がほぼ同水準
                        and m >= seg_hi * 0.97                        # ヘッド=レンジ天井(上昇の頂点)
                        and seg[0] < m * 0.94):                       # 上昇して入ってきた
                    # ネックライン(両肩間の谷)割れで確定、それ以外は不成立
                    neck_lo = min(seg[last3[0][0]:last3[2][0]+1]) if last3[2][0] > last3[0][0] else None
                    if neck_lo and cur < neck_lo * 1.01:
                        santen = True
            # 逆三尊(ヘッド&ショルダー底): 安値3つで真ん中が一番安い→強気転換
            # 前提: 下落して底を付けた形。ヘッドはレンジ下限付近＆上昇中には出さない
            gyaku = False
            if len(troughs) >= 3 and not uptrend:
                last3 = troughs[-3:]
                l, m, r = last3[0][1], last3[1][1], last3[2][1]
                if (m < l and m < r and abs(l - r) / m < 0.06
                        and m <= seg_lo * 1.03                        # ヘッド=レンジ底(下落の底)
                        and seg[0] > m * 1.06                         # 下落して入ってきた
                        and cur > m * 1.03):                          # 底から反発済み
                    gyaku = True
            # トリプルトップ(高値3つがほぼ同水準=レンジ天井→弱気): レンジ上部でのみ成立
            triple_top = False
            if len(peaks) >= 3 and not santen and not downtrend:
                last3 = [p[1] for p in peaks[-3:]]
                avg = sum(last3) / 3
                if (all(abs(v - avg) / avg < 0.04 for v in last3)
                        and avg >= seg_hi * 0.95 and cur < avg * 0.98):
                    triple_top = True
            # トリプルボトム(安値3つがほぼ同水準=レンジ底→強気): レンジ下部でのみ成立
            triple_bottom = False
            if len(troughs) >= 3 and not gyaku and not uptrend:
                last3 = [t[1] for t in troughs[-3:]]
                avg = sum(last3) / 3
                if (all(abs(v - avg) / avg < 0.04 for v in last3)
                        and avg <= seg_lo * 1.05 and cur > avg * 1.02):
                    triple_bottom = True
            # ダブルトップ(高値2つが同水準=M字→弱気): レンジ上部でのみ成立
            double_top = False
            if len(peaks) >= 2 and not santen and not triple_top and not downtrend:
                last2 = [p[1] for p in peaks[-2:]]
                if (abs(last2[0] - last2[1]) / max(last2) < 0.04
                        and max(last2) >= seg_hi * 0.96 and cur < min(last2) * 0.97):
                    # 2つの山の間に明確な谷があるか
                    pi = peaks[-2][0]
                    valley = min(seg[pi:]) if pi < len(seg) else cur
                    if valley < min(last2) * 0.94:
                        double_top = True
            # ダブルボトム(安値2つが同水準=W字→強気): レンジ下部でのみ成立
            double_bottom = False
            if len(troughs) >= 2 and not gyaku and not triple_bottom and not uptrend:
                last2 = [t[1] for t in troughs[-2:]]
                if (abs(last2[0] - last2[1]) / max(last2) < 0.04
                        and min(last2) <= seg_lo * 1.04 and cur > max(last2) * 1.03):
                    ti = troughs[-2][0]
                    peak_between = max(seg[ti:]) if ti < len(seg) else cur
                    if peak_between > max(last2) * 1.06:
                        double_bottom = True
            # フラッグ(継続型パターン統合): 急騰→保ち合い→上抜け。
            # 上昇フラッグ/ペナント/上昇トライアングル/レクタングル/ウェッジ等の
            # 「上昇後の保ち合い」を全部ここに集約(カップ&ハンドルと下降系は別扱い)
            flag = False
            if len(seg) >= 25:
                # 少し前(10〜20日前あたり)に急騰したか
                run_up = max(
                    (seg[-15] / seg[-25] - 1) if seg[-25] else 0,
                    (seg[-10] / seg[-20] - 1) if seg[-20] else 0,
                )
                box = seg[-12:]  # 直近12日の保ち合い区間
                box_range = (max(box) - min(box)) / max(box) if max(box) else 1
                # 収縮判定(三角/ペナント/ウェッジ): 前半のレンジ > 後半のレンジ×1.25
                h1, h2 = box[:6], box[6:]
                r1 = (max(h1) - min(h1)) / max(h1) if max(h1) else 0
                r2 = (max(h2) - min(h2)) / max(h2) if max(h2) else 1
                contracting = r1 > r2 * 1.25
                # フラット保ち合い(フラッグ/レクタングル): レンジ12%以内
                boxy = box_range <= 0.12
                # 急騰8%以上 → 保ち合い(フラット or 収縮) → 保ち合い上限を上抜け
                if run_up >= 0.08 and (boxy or (contracting and box_range <= 0.16)) \
                        and cur >= max(box[:-1]) * 0.985:
                    flag = True

            # 三角持ち合い(収束)判定: 直近の高値群が切り下がり・安値群が切り上がる収束形。
            # 直近ウィンドウ(15〜30日)の高値ライン/安値ラインの傾きで判定。
            tri_pre = False
            tri_post = False
            if len(seg) >= 18:
                win = seg[-24:] if len(seg) >= 24 else seg[-18:]
                m = len(win)
                # ローカル高値・安値(前後2本より高い/低い点)を集める
                hi_pts = [(i, win[i]) for i in range(2, m - 2)
                          if win[i] == max(win[i-2:i+3])]
                lo_pts = [(i, win[i]) for i in range(2, m - 2)
                          if win[i] == min(win[i-2:i+3])]
                # 端点も候補に含める(収束の頂点付近を捉えるため)
                def slope(points):
                    if len(points) < 2:
                        return None, None
                    xs = [p[0] for p in points]; ys = [p[1] for p in points]
                    xm = sum(xs) / len(xs); ym = sum(ys) / len(ys)
                    den = sum((x - xm) ** 2 for x in xs)
                    if den == 0:
                        return 0.0, ym
                    sl = sum((xs[i] - xm) * (ys[i] - ym) for i in range(len(xs))) / den
                    return sl, ym
                hs, _ = slope(hi_pts[-3:] if len(hi_pts) >= 2 else hi_pts)
                ls, _ = slope(lo_pts[-3:] if len(lo_pts) >= 2 else lo_pts)
                hi_max = max(win); lo_min = min(win)
                rng_first = (max(win[:m//2]) - min(win[:m//2]))
                rng_last = (max(win[m//2:]) - min(win[m//2:]))
                base = win[-1] if win[-1] else 1
                if hs is not None and ls is not None:
                    # 収束条件: 高値ラインが下向き(or横ばい弱)、安値ラインが上向き、
                    # かつ後半のレンジが前半より縮小(ペナント/対称三角)
                    hs_pct = hs / base * 100   # 1本あたり%傾き
                    ls_pct = ls / base * 100
                    converging = (hs_pct <= 0.15 and ls_pct >= -0.15
                                  and (hs_pct < -0.05 or ls_pct > 0.05)
                                  and rng_last < rng_first * 0.85)
                    if converging:
                        # 収束上限(直近高値ラインの現在値付近)= 当日を除く直近の高値
                        upper = max(win[-6:-1]) if len(win) >= 7 else hi_max
                        if cur > upper * 1.003:
                            tri_post = True   # 三角ブレイク(上放れ済み)
                        else:
                            tri_pre = True    # 三角ブレイクリーチ(収束中・上放れ前)

            # ===== パターン判定(2026-07 修正) =====
            # 旧実装は「52週高値更新」が最優先のelif連鎖で、直近高値ブレイクが
            # ほぼ拾えていなかった(実データで1件のみ)。かつ vol_up 必須が厳しすぎた。
            # → 高値ブレイク系を先に判定し、vol_up は必須にしない(出来高増は付帯情報に)。
            #    複数該当を patterns(配列)にも保持し、フィルタの取りこぼしを無くす。
            hi3m = (max(closes[-64:-1]) if len(closes) >= 64 else max(closes[:-1]))
            patterns = []

            # --- 高値ブレイク系(最優先・出来高は問わない) ---
            if hi3m and cur > hi3m:
                patterns.append("直近高値ブレイク")
            elif hi3m and hi3m * 0.98 <= cur <= hi3m:
                patterns.append("直近高値ブレイクリーチ")
            if hi52 and cur >= hi52 * 0.985:
                patterns.append("52週高値更新")
            # --- チャート形状系 ---
            if flag: patterns.append("フラッグブレイク")
            if gyaku: patterns.append("逆三尊・底打ち")
            if santen: patterns.append("三尊・天井注意")
            if triple_bottom: patterns.append("トリプルボトム")
            if triple_top: patterns.append("トリプルトップ")
            if double_bottom: patterns.append("ダブルボトム")
            if double_top: patterns.append("ダブルトップ")
            if tri_post: patterns.append("三角ブレイク")
            elif tri_pre: patterns.append("三角ブレイクリーチ")
            cwh_state = detect_cwh(closes, highs, lows, vols)
            if cwh_state in ("handle", "breakout"):
                patterns.append("CWH押し目/抜け")
            elif cwh_state == "forming":
                patterns.append("CWH形成中")
            if lo52 and cur <= lo52 * 1.02:
                patterns.append("52週安値圏")

            # 代表パターン(単一表示用)= 優先順で先頭を選ぶ
            _prio = ["直近高値ブレイク", "52週高値更新", "フラッグブレイク",
                     "逆三尊・底打ち", "三尊・天井注意", "トリプルボトム",
                     "トリプルトップ", "ダブルボトム", "ダブルトップ",
                     "三角ブレイク", "直近高値ブレイクリーチ", "三角ブレイクリーチ",
                     "CWH押し目/抜け", "CWH形成中", "52週安値圏"]
            pattern = next((p for p in _prio if p in patterns), None)
        quotes[sym] = {
            "name": name, "market": market,
            "last": daily[-1][1], "lastDate": daily[-1][0],
            "chg": chg,
            "volRatio": vol_ratio,
            "volSurge": vol_surge,  # 出来高急増レベル(0/1=2倍/2=3倍/3=5倍)
            "bbZone": bb_zone,      # σゾーン(3σ+/2σ+/2σ-/3σ-)
            "bbPctB": bb_pctb,      # %B(1.0=+2σ, 0=-2σ)
            "dev25": dev25,         # 25日線乖離率(%)
            "dev50": dev50,         # 50日線乖離率(%)
            "dev5": dev5,           # 5日線乖離率(%)
            "ret3d": ret3d,         # 3日リターン(%)
            "ret5d": ret5d,         # 5日リターン(%)
            "streak": streak,       # 連続上昇(+)/下落(-)日数
            "daytrade": daytrade,   # デイトレ妙味(資金流入急増/初動/急落リバ)
            "ret1m": ret1m,         # 1ヶ月騰落率(%)
            "ret3m": ret3m,         # 3ヶ月騰落率(%)
            "rsi": rsi,             # RSI(14)
            "posPct": posPct,       # 52週レンジ内の位置(%)
            "hi52": round(hi52, 2) if hi52 else None,  # 52週高値(終値ベース)
            "lo52": round(lo52, 2) if lo52 else None,  # 52週安値(終値ベース)
            "hiAll": round(hi_all, 2) if hi_all else None,  # 取得全期間の高値(参考値)
            "signal": signal,       # 押し目/調整中/過熱/25日線付近
            "pattern": pattern,     # 代表パターン(単一表示用)
            "patterns": patterns,   # 該当パターン全て(フィルタ取りこぼし防止)
            "po": perfect_order,    # パーフェクトオーダー(日足・強気)
            "poBear": perfect_order_bear,  # 逆パーフェクトオーダー(弱気)
            "pullback": pullback,   # 押し目(5日線/25日線/75日線)
            "pullbackMa": pullback_ma,  # どの線の押し目か(5/25/50/75)
            "tags": STOCK_TAGS.get(sym, []),  # タグ(tags.py由来。属するテーマ/タグ表示+タグ検索対象)
            # daily: [日付, 終値, 出来高, 始値, 高値, 安値]
            # data.jsonには直近250本だけ載せる(日足チャートは80本表示。長期は週足/月足で見る)。
            # 直近130本はOHLC付き(ローソク)、それ以前は終値+出来高(容量削減)。
            # ※MA計算・パターン判定はKEEP_DAYS(460本)の全履歴で済ませてある。
            "daily": [
                ([r[0], round(r[1], 3), int(r[2]) if r[2] else 0,
                  round(r[3], 3) if len(r) > 3 else round(r[1], 3),
                  round(r[4], 3) if len(r) > 4 else round(r[1], 3),
                  round(r[5], 3) if len(r) > 5 else round(r[1], 3)]
                 if i >= len(daily) - 130 else
                 [r[0], round(r[1], 3), int(r[2]) if r[2] else 0])
                for i, r in enumerate(daily)
                if i >= len(daily) - 250
            ],
            "weekly": _aggregate_ohlc(daily, "W"),
            "monthly": _aggregate_ohlc(daily, "M"),
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

    try:
        from fetch_headlines import fetch_headlines as _fetch_hl
        _headlines = _fetch_hl()
        print(f"headlines: {len(_headlines)}件取得")
    except Exception as _e:
        print(f"headlines skip: {_e}")
        _headlines = []

    try:
        import site_content as _sc
        _content = {
            "weekly": getattr(_sc, "WEEKLY", {}),
            "events": getattr(_sc, "EVENTS", []),
            "news": getattr(_sc, "NEWS", []),
            "macro": getattr(_sc, "MACRO", {}),
        }
        print("content: site_content.py 反映")
    except Exception as _e:
        print(f"content skip: {_e}")
        _content = {}

    try:
        from fetch_disclosures import fetch_disclosures as _fetch_ds
        _disclosures = _fetch_ds()
        print(f"disclosures: {len(_disclosures)}件取得(適時開示)")
    except Exception as _e:
        print(f"disclosures skip: {_e}")
        _disclosures = []

    try:
        from generate_ai_analysis import generate_daily_analysis as _gen_ai
        _target_hour = os.environ.get("AI_ANALYSIS_HOUR_JST")
        _jst_hour = datetime.now(timezone.utc).astimezone(
            timezone(timedelta(hours=9))
        ).hour
        if _target_hour is None or str(_jst_hour) == str(_target_hour):
            _ai_analysis = _gen_ai(quotes, datetime.now(timezone.utc).isoformat(timespec="seconds"))
            print("AI分析:", "生成OK" if _ai_analysis else "スキップ")
        else:
            _ai_analysis = {}
            try:
                with open("docs/data.json", encoding="utf-8") as _f:
                    _ai_analysis = json.load(_f).get("ai_analysis", {}) or {}
            except Exception:
                pass
            print(f"AI分析: 対象時間外(JST {_jst_hour}時、対象は{_target_hour}時)のためスキップ — 前回の分析を維持")
    except Exception as _e:
        print(f"AI分析 skip: {_e}")
        _ai_analysis = {}

    try:
        from fetch_market_indicators import (
            fetch_tsmc_monthly_revenue, fetch_sox_index, fetch_us10y_yield,
        )
        _tsmc_monthly = fetch_tsmc_monthly_revenue()
        _sox = fetch_sox_index()
        _us10y = fetch_us10y_yield()
        print(f"市況指標: TSMC月次{len(_tsmc_monthly)}件 / SOX {'取得OK' if _sox else '取得失敗'} / 米10年金利 {'取得OK' if _us10y else '取得失敗'}")
    except Exception as _e:
        print(f"市況指標 skip: {_e}")
        _tsmc_monthly, _sox, _us10y = [], {}, {}

    out = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "failed": failed, "macro": MACRO, "quotes": quotes,
        "linkage": linkage,
        "process": proc,
        "flow": _build_flow(symbols),
        "headlines": _headlines,
        "content": _content,
        "disclosures": _disclosures,
        "ai_analysis": _ai_analysis,
        "tsmc_monthly": _tsmc_monthly,
        "sox": _sox,
        "us10y": _us10y,
    }
    os.makedirs("docs", exist_ok=True)
    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\nOK: {len(quotes)}銘柄 / 失敗 {len(failed)}件 {failed if failed else ''}")


if __name__ == "__main__":
    main()
