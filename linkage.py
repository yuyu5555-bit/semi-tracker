# -*- coding: utf-8 -*-
"""
連動分析（①検証機能・シンプル版＋米国側情報）

linkage[テーマ名] = {
  "us": [[sym,name,latestChg], ...],   # 構成米国株と直近前日比(%)
  "usAvg": 直近の米国テーマ平均リターン(%),
  "triggered": bool,                   # 直近がTHRESHOLD%以上か
  "rows": [ {code,name,rate,avg,n}, ... ]  # 連動日本株(連動率降順)
}
"""

THRESHOLD = 2.0
MIN_SAMPLE = 5


def _daily_returns(daily):
    rets = {}
    for i in range(1, len(daily)):
        d0, c0 = daily[i - 1][0], daily[i - 1][1]
        d1, c1 = daily[i][0], daily[i][1]
        if c0:
            rets[d1] = (c1 / c0 - 1) * 100
    return rets


def _latest_chg(daily):
    if not daily or len(daily) < 2:
        return None
    c0, c1 = daily[-2][1], daily[-1][1]
    return (c1 / c0 - 1) * 100 if c0 else None


def _theme_daily_avg_return(us_syms, quotes):
    series = []
    for s in us_syms:
        q = quotes.get(s)
        if q and q.get("daily"):
            series.append(_daily_returns(q["daily"]))
    if not series:
        return {}
    dates = set()
    for r in series:
        dates |= set(r.keys())
    out = {}
    for d in dates:
        vals = [r[d] for r in series if d in r]
        if vals:
            out[d] = sum(vals) / len(vals)
    return out


def _next_day_map(daily):
    ds = [row[0] for row in daily]
    return {ds[i]: ds[i + 1] for i in range(len(ds) - 1)}


def build_linkage(macro, quotes):
    linkage = {}
    for m in macro:
        for sub in m["subs"]:
            us_list = sub["us"]
            us_syms = [s for s, _ in us_list]
            if not us_syms:
                continue
            tret = _theme_daily_avg_return(us_syms, quotes)
            if not tret:
                continue
            trig = {d for d, v in tret.items() if v >= THRESHOLD}
            # 直近の米国側平均リターン
            latest_date = max(tret.keys()) if tret else None
            us_avg = round(tret[latest_date], 2) if latest_date else None
            # 構成米国株と直近前日比
            us_info = []
            for s, nm in us_list:
                q = quotes.get(s)
                ch = _latest_chg(q["daily"]) if (q and q.get("daily")) else None
                us_info.append([s, nm, round(ch, 2) if ch is not None else None])
            # 連動日本株
            jp_rows = [(c, n) for c, n, *_ in sub["jp"]] + [(c, n) for c, n, *_ in sub["solo"]]
            results = []
            for code, name in jp_rows:
                q = quotes.get(code)
                if not q or not q.get("daily"):
                    continue
                jp_ret = _daily_returns(q["daily"])
                nxt = _next_day_map(q["daily"])
                ups, acc = 0, []
                for td in trig:
                    nd = nxt.get(td)
                    if nd and nd in jp_ret:
                        r = jp_ret[nd]
                        acc.append(r)
                        if r > 0:
                            ups += 1
                if not acc:
                    continue
                results.append({
                    "code": code, "name": name,
                    "rate": round(ups / len(acc) * 100, 0),
                    "avg": round(sum(acc) / len(acc), 2),
                    "n": len(acc),
                })
            results.sort(key=lambda x: (x["rate"], x["avg"]), reverse=True)
            if results:
                # トリガー段階: 米国構成銘柄の前日比平均で判定
                trig_level = 0
                if us_avg is not None:
                    if us_avg >= 10: trig_level = 3    # 🔥🔥🔥 急騰(10%↑)
                    elif us_avg >= 5: trig_level = 2   # 🔥🔥 大幅高(5%↑)
                    elif us_avg >= 2: trig_level = 1   # 🔥 上昇(2%↑)
                linkage[f'{m["name"]} > {sub["name"]}'] = {
                    "us": us_info,
                    "usAvg": us_avg,
                    "triggered": bool(trig_level >= 1),
                    "trigLevel": trig_level,
                    "rows": results,
                }
    return linkage
