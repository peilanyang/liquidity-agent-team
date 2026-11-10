"""
美元流動性看板：資料抓取與計算
--------------------------------
用法：
    python liquidity.py              # 用真實資料
    python liquidity.py tests/sample # 離線測試（以 2026-11-10 為執行日）
結果：
    1. 在螢幕印出上週 / 本週對照表
    2. 產生 liquidity.json，給看板讀取

指標依「危機發生的順序」分成四類：
    蓄水池（水還有多少）→ 管線流量（每天流多少）→ 壓力計（水不夠時價格先反應）→ 滅火器（聯準會的救援工具）

只用 Python 內建模組，不需要另外安裝套件。
資料來源：聖路易聯儲 FRED 公開 CSV（不需要 API 金鑰）。
"""

import csv
import io
import json
import sys
import time
import urllib.request
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

# ========== 1. 設定區：要改門檻，只改這裡 ==========

THRESHOLDS = {
    "spread_bp": 7,            # SOFR-EFFR ≥ 7bp → 心絞痛
    "reserves_ample": 3.0,     # 準備金 < 3 兆 → 適度區
    "reserves_scarce": 2.3,    # 準備金 < 2.3 兆 → 稀缺區
    "rrp_scarce": 3750,        # ON RRP < 3,750 億 → 稀缺區（億美元）
    "tga_low": 5000,           # TGA < 5,000 億 → 偏低，留意之後回補抽水（億美元）
    "tga_jump": 1000,          # TGA 單週增加 > 1,000 億 → 抽水偏快（報稅週除外）
    "ceiling_near_bp": -5,     # 距天花板 ≥ −5bp → 接近天花板；≥ 0 → 撞到天花板
    "iorb_near_bp": -5,        # EFFR−IORB ≥ −5bp → 接近 IORB；≥ 0 → 準備金不足訊號
    "cp_amber_bp": 30,         # 商業本票−國庫券 ≥ 30bp → 偏高
    "cp_red_bp": 60,           # ≥ 60bp → 明顯緊張
    "swap_amber": 50,          # 央行換匯 > 50 億 → 海外動用增加（平常只有數億的例行操作）
}

# 要從 FRED 下載的序列：(代號, 說明)
SERIES = {
    # 壓力計
    "SOFR": "SOFR (%)",
    "EFFR": "有效聯邦基金利率 (%)",
    "SOFR99": "SOFR 99 百分位 (%)",
    "IORB": "準備金利率 (%)",
    "DFEDTARU": "政策區間上限 = SRF 利率，走廊天花板 (%)",
    "DFEDTARL": "政策區間下限 = ON RRP 利率，走廊地板 (%)",
    "DCPF3M": "3 個月 AA 級金融商業本票利率 (%)",
    "DTB3": "3 個月國庫券利率 (%)",
    # 管線流量
    "SOFRVOL": "SOFR 交易量 (十億美元)",
    "EFFRVOL": "聯邦基金交易量 (十億美元)",
    # 蓄水池
    "WRBWFRBL": "準備金 (百萬美元，週三)",
    "RRPONTSYD": "ON RRP (十億美元，每日)",
    "WDTGAL": "TGA (百萬美元，週三)",
    "WALCL": "聯準會總資產 (百萬美元，週三)",
    "TREAST": "聯準會持有公債 (百萬美元，週三)",
    "WSHOBL": "其中：短期國庫券 (百萬美元，週三)",
    "WSHOMCB": "聯準會持有 MBS (百萬美元，週三)",
    "WLRRAFOIAL": "FIMA 逆回購池：外國央行存在聯準會的美元 (百萬美元，週三)",
    "WRMFNS": "零售型貨幣市場基金 (十億美元，每週)",
    # 滅火器
    "RPONTTLD": "SRF 常設回購成交 (十億美元，每日)",
    "WLCFLPCL": "貼現窗口一級信貸 (百萬美元，週三)",
    "H41RESPPALGTRFNWW": "FIMA 回購：外國央行向聯準會借美元 (百萬美元，週三)",
    "SWPT": "央行換匯額度 (百萬美元，週三)",
}

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}&cosd={}"
OFFLINE_TODAY = date(2026, 11, 10)


# ========== 2. 抓資料：直接下載原始 CSV，不經過 AI ==========

def fetch(series_id, start, csv_dir=None):
    """回傳 {日期: 數值}，跳過缺值（FRED 用 '.' 表示缺值）"""
    if csv_dir:  # 離線測試用：從資料夾讀 CSV
        with open(f"{csv_dir}/{series_id}.csv", encoding="utf-8") as f:
            text = f.read()
    else:
        req = urllib.request.Request(
            FRED_CSV.format(series_id, start),
            # FRED 會擋掉部分程式的連線，用 curl 的識別名稱才穩定
            headers={"User-Agent": "curl/8.5.0"},
        )
        for attempt in range(3):  # 下載偶爾很慢，最多重試 3 次
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    text = resp.read().decode("utf-8")
                break
            except (TimeoutError, OSError) as e:
                if attempt == 2:
                    raise RuntimeError(f"{series_id} 下載失敗：{e}")
                time.sleep(3)

    data = {}
    reader = csv.reader(io.StringIO(text))
    next(reader)  # 跳過標題列
    for row in reader:
        if len(row) < 2 or row[1] in (".", ""):
            continue
        data[date.fromisoformat(row[0])] = float(row[1])
    if not data:
        raise ValueError(f"{series_id} 沒有抓到任何資料")
    return data


# ========== 3. 按週切分 ==========

def week_key(d):
    """回傳 (年, 第幾週)，週一到週日算同一週"""
    y, w, _ = d.isocalendar()
    return (y, w)


def monday(wk):
    return date.fromisocalendar(wk[0], wk[1], 1)


def prev_week(wk, n=1):
    return week_key(monday(wk) - timedelta(days=7 * n))


def in_week(data, wk):
    return {d: v for d, v in data.items() if week_key(d) == wk}


def week_end_value(data, wk):
    """該週最後一筆（週三水位類資料，一週只有一筆）；沒有就回傳 None"""
    vals = in_week(data, wk)
    return vals[max(vals)] if vals else None


def value_at(data, wk):
    """該週或之前最近的一筆（給更新頻率較低的資料用）"""
    end = monday(wk) + timedelta(days=6)
    ds = [d for d in data if d <= end]
    return data[max(ds)] if ds else None


# ========== 4. 計算工具 ==========

def trend(a, b, tol=0.02):
    if a is None or b is None:
        return "資料不足"
    if a == 0:
        return "基本持平" if b == 0 else ("上升" if b > 0 else "下降")
    r = (b - a) / abs(a)
    if abs(r) < tol:
        return "基本持平"
    if r > 0:
        return "明顯上升" if r > 0.1 else "小幅上升"
    return "明顯下降" if r < -0.1 else "小幅下降"


def trend_bp(a, b):
    """基點類指標用絕對變化判斷：1bp 以內持平，超過 5bp 為明顯"""
    if a is None or b is None:
        return "資料不足"
    d = b - a
    if abs(d) <= 1:
        return "基本持平"
    if d > 0:
        return "明顯上升" if d > 5 else "小幅上升"
    return "明顯下降" if d < -5 else "小幅下降"


def r1(v):
    """四捨五入到 1 位小數（1.25 → 1.3，避免浮點數把它捨成 1.2）"""
    if v is None:
        return None
    return float(Decimal(str(round(v, 6))).quantize(Decimal("0.1"), ROUND_HALF_UP))


def clean(v):  # 9538.0 → 9538，其他取到小數 3 位
    if isinstance(v, float):
        return int(v) if v.is_integer() else round(v, 3)
    return v


avg = lambda d: sum(d.values()) / len(d) if d else None
mx = lambda d: max(d.values()) if d else None
mn = lambda d: min(d.values()) if d else None
total = lambda d: sum(d.values()) if d else 0.0
to_yi = lambda m: None if m is None else m / 100          # 百萬美元 → 億美元
to_zhao = lambda m: None if m is None else round(m / 1e6, 2)  # 百萬美元 → 兆美元


def is_tax_week(wk):
    """含 1/15、4/15、6/15、9/15、12/15 報稅日的週（TGA 季節性大增）"""
    days = [monday(wk) + timedelta(days=i) for i in range(7)]
    return any((d.month, d.day) in {(1, 15), (4, 15), (6, 15), (9, 15), (12, 15)} for d in days)


def key_dates(wk):
    """本週與下週容易讓資金利率跳升的日子"""
    out = []
    for i in range(14):
        d = monday(wk) + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        tags = []
        nxt = d + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        if nxt.month != d.month:  # 該月最後一個營業日
            tags.append("季底" if d.month in (3, 6, 9, 12) else "月底")
        if d.day == 15 or (d.day in (16, 17) and d.weekday() == 0):
            tags.append("月中國債結算")
        if (d.month, d.day) in {(1, 15), (4, 15), (6, 15), (9, 15), (12, 15)}:
            tags.append("報稅日")
        if tags:
            out.append({"date": d.isoformat(), "label": "、".join(tags),
                        "week": "本週" if i < 7 else "下週"})
    return out


# ========== 5. 組出看板資料 ==========

CATEGORIES = [
    ("reservoir", "蓄水池", "水還有多少：銀行體系的現金存量，以及進水、抽水的力量"),
    ("flow", "管線流量", "每天有多少水在流：短期資金市場的成交量"),
    ("pressure", "壓力計", "水不夠時價格先反應：借錢的利率離天花板還有多遠"),
    ("extinguisher", "滅火器", "撐不住時聯準會的救援工具：有使用量就代表有人缺錢"),
]


def build(csv_dir=None, today=None):
    today = today or (OFFLINE_TODAY if csv_dir else date.today())
    start = (today - timedelta(days=200)).isoformat()
    raw = {sid: fetch(sid, start, csv_dir) for sid in SERIES}

    # 本週 = SOFR 最新一筆所在的週；若那一週還沒結束，就改用上一週
    this_wk = week_key(max(raw["SOFR"]))
    if this_wk == week_key(today):
        this_wk = prev_week(this_wk)
    last_wk = prev_week(this_wk)
    W = {sid: (in_week(d, last_wk), in_week(d, this_wk)) for sid, d in raw.items()}
    T = THRESHOLDS
    rows, daily = [], {}

    def add(cat, name, last, this, alert=None, level=None, note=None, tr=None, days=None):
        last, this = clean(last), clean(this)
        rows.append({
            "category": cat, "name": name, "last": last, "this": this,
            "trend": tr or trend(last, this),
            "alert": alert,
            "level": (level or "amber") if alert else None,  # red / amber / None（只有警示時才有）
            "note": note,
        })
        if days:
            daily[name] = {
                "last": {str(k)[5:]: clean(round(v, 3)) for k, v in sorted(days[0].items())},
                "this": {str(k)[5:]: clean(round(v, 3)) for k, v in sorted(days[1].items())},
            }

    def wed(sid, wk):
        return week_end_value(raw[sid], wk)

    # ---------- 蓄水池 ----------
    res_l, res_t = to_zhao(wed("WRBWFRBL", last_wk)), to_zhao(wed("WRBWFRBL", this_wk))
    add("reservoir", "準備金餘額 (兆)", res_l, res_t,
        alert="稀缺區" if res_t < T["reserves_scarce"] else "適度區" if res_t < T["reserves_ample"] else None,
        level="red" if res_t < T["reserves_scarce"] else None,
        note="週三水位 · 適度 < 3 兆 · 稀缺 < 2.3 兆")

    rrp_l = {d: v * 10 for d, v in W["RRPONTSYD"][0].items()}  # 十億 → 億
    rrp_t = {d: v * 10 for d, v in W["RRPONTSYD"][1].items()}
    rrp_avg_t = r1(avg(rrp_t))
    add("reservoir", "ON RRP 均值 (億)", r1(avg(rrp_l)), rrp_avg_t,
        alert="稀缺區" if rrp_avg_t is not None and rrp_avg_t < T["rrp_scarce"] else None, level="red",
        note=f"最低 {r1(mn(rrp_l))} → {r1(mn(rrp_t))} · 稀缺 < 3,750 億", days=(rrp_l, rrp_t))

    tga_l, tga_t = to_yi(wed("WDTGAL", last_wk)), to_yi(wed("WDTGAL", this_wk))
    tga_chg = tga_t - tga_l
    tax = is_tax_week(this_wk)
    tga_alert = None
    if tga_t < T["tga_low"]:
        tga_alert = "偏低：留意之後回補抽水"
    elif tga_chg > T["tga_jump"] and not tax:
        tga_alert = "抽水偏快"
    tga_note = f"週三水位 · 單週 {tga_chg:+,.0f} 億"
    if tax and tga_chg > T["tga_jump"]:
        tga_note += " · 報稅週，屬季節性"
    add("reservoir", "TGA (億)", round(tga_l), round(tga_t), alert=tga_alert, note=tga_note)

    def net_liq(wk):
        """淨流動性 = 聯準會總資產 − TGA − ON RRP（都取週三，先四捨五入到億再相減，看板數字可直接驗算）"""
        a, t = wed("WALCL", wk), wed("WDTGAL", wk)
        wed_day = monday(wk) + timedelta(days=2)
        rrp = raw["RRPONTSYD"].get(wed_day)
        if rrp is None:
            rrp = week_end_value(raw["RRPONTSYD"], wk) or 0
        if None in (a, t):
            return None
        parts = (round(a / 100), round(t / 100), round(rrp * 10))
        return parts[0] - parts[1] - parts[2], parts
    (nl_l, _), (nl_t, parts) = net_liq(last_wk), net_liq(this_wk)
    nl_13 = net_liq(prev_week(this_wk, 13))
    nl_chg = nl_t - nl_l

    def trend_yi(d):  # 億美元的絕對變化：300 億以內持平，超過 1,500 億為明顯
        if abs(d) <= 300:
            return "基本持平"
        return ("明顯" if abs(d) > 1500 else "小幅") + ("上升" if d > 0 else "下降")
    add("reservoir", "淨流動性 (億)", nl_l, nl_t, tr=trend_yi(nl_chg),
        note=f"本週 = 總資產 {parts[0]:,} − TGA {parts[1]:,} − ON RRP {parts[2]:,}（週三）"
             + (f" · 近 13 週 {nl_t - nl_13[0]:+,} 億" if nl_13 else ""))

    def chg(sid, wk, n):  # n 週變化（億）
        a, b = wed(sid, prev_week(wk, n)), wed(sid, wk)
        return None if None in (a, b) else (b - a) / 100
    ta_l, ta_t = round(wed("WALCL", last_wk) / 100), round(wed("WALCL", this_wk) / 100)
    add("reservoir", "聯準會總資產 (億)", ta_l, ta_t, tr=trend_yi(ta_t - ta_l),
        note=f"週三水位 · 近 13 週 {chg('WALCL', this_wk, 13):+,.0f} 億")
    qt_l, qt_t = chg("WALCL", last_wk, 4), chg("WALCL", this_wk, 4)
    add("reservoir", "每月縮表速度 (億)", round(qt_l), round(qt_t),
        note="總資產近 4 週變化 · 負數 = 縮表、正數 = 擴表")
    rows[-1]["trend"] = "縮表" if qt_t < -50 else "擴表" if qt_t > 50 else "規模持平"
    add("reservoir", "持有公債 4 週變化 (億)", round(chg("TREAST", last_wk, 4)), round(chg("TREAST", this_wk, 4)),
        note=f"其中短期國庫券 {chg('WSHOBL', this_wk, 4):+,.0f} 億 · 每週買債金額固定交替，連兩週相同屬正常")
    rows[-1]["trend"] = "增加" if rows[-1]["this"] > 0 else "減少" if rows[-1]["this"] < 0 else "不變"
    add("reservoir", "持有 MBS 4 週變化 (億)", round(chg("WSHOMCB", last_wk, 4)), round(chg("WSHOMCB", this_wk, 4)),
        note="MBS 每月固定時間還本，4 週內各含一次，連兩週相同屬正常")
    rows[-1]["trend"] = "增加" if rows[-1]["this"] > 0 else "減少" if rows[-1]["this"] < 0 else "不變"

    add("reservoir", "FIMA 逆回購池 (億)", round(to_yi(wed("WLRRAFOIAL", last_wk))), round(to_yi(wed("WLRRAFOIAL", this_wk))),
        note="外國央行停在聯準會的美元，必要時可流回市場")
    mmf_l, mmf_t = value_at(raw["WRMFNS"], last_wk), value_at(raw["WRMFNS"], this_wk)
    mmf_dates = [d for d in raw["WRMFNS"] if d <= monday(this_wk) + timedelta(days=6)]
    mmf_date = max(mmf_dates)
    add("reservoir", "零售型貨幣基金 (兆)", round(mmf_l / 1000, 3), round(mmf_t / 1000, 3),
        note=f"資料日 {mmf_date.isoformat()[5:]}（聯準會每月更新一次）· 只含零售型，約佔貨幣基金總規模四成")
    if mmf_l == mmf_t and value_at(raw["WRMFNS"], last_wk) is not None:
        rows[-1]["trend"] = "資料未更新"

    # ---------- 管線流量 ----------
    add("flow", "SOFR 交易量均值 (十億)", avg(W["SOFRVOL"][0]), avg(W["SOFRVOL"][1]), days=W["SOFRVOL"])
    add("flow", "聯邦基金交易量均值 (十億)", avg(W["EFFRVOL"][0]), avg(W["EFFRVOL"][1]), days=W["EFFRVOL"])

    # ---------- 壓力計 ----------
    def spread_days(a, b, wk_idx):  # 每日 a − b（bp），兩邊都有資料的日子
        x, y = W[a][wk_idx], raw[b]
        return {d: round((x[d] - y[d]) * 100) for d in x if d in y}

    add("pressure", "EFFR 最高 (%)", mx(W["EFFR"][0]), mx(W["EFFR"][1]), days=W["EFFR"])
    ei = (spread_days("EFFR", "IORB", 0), spread_days("EFFR", "IORB", 1))
    ei_t = mx(ei[1])
    add("pressure", "EFFR − IORB 最高 (bp)", mx(ei[0]), ei_t, tr=trend_bp(mx(ei[0]), ei_t),
        alert="準備金不足訊號" if ei_t >= 0 else "接近 IORB" if ei_t >= T["iorb_near_bp"] else None,
        level="red" if ei_t >= 0 else None,
        note=f"IORB {raw['IORB'][max(raw['IORB'])]:.2f}% · 低於 0 代表準備金仍充足", days=ei)
    se = (spread_days("SOFR", "EFFR", 0), spread_days("SOFR", "EFFR", 1))
    se_t = mx(se[1])
    add("pressure", "SOFR − EFFR 利差最高 (bp)", mx(se[0]), se_t, tr=trend_bp(mx(se[0]), se_t),
        alert="心絞痛" if se_t >= T["spread_bp"] else None, level="red",
        note=f"警示 ≥ {T['spread_bp']}bp", days=se)
    add("pressure", "SOFR99 最高 (%)", mx(W["SOFR99"][0]), mx(W["SOFR99"][1]), days=W["SOFR99"])

    top = raw["DFEDTARU"][max(raw["DFEDTARU"])]
    floor = raw["DFEDTARL"][max(raw["DFEDTARL"])]
    for sid, label in (("SOFR", "SOFR"), ("SOFR99", "SOFR99")):
        c = (spread_days(sid, "DFEDTARU", 0), spread_days(sid, "DFEDTARU", 1))
        c_t = mx(c[1])
        add("pressure", f"{label} 距天花板最近 (bp)", mx(c[0]), c_t, tr=trend_bp(mx(c[0]), c_t),
            alert="撞到天花板" if c_t >= 0 else "接近天花板" if c_t >= T["ceiling_near_bp"] else None,
            level="red" if c_t >= 0 else None,
            note=f"天花板 = SRF 利率 {top:.2f}%（地板 ON RRP {floor:.2f}%）· 0 以上代表比找聯準會借還貴", days=c)

    cp = ({d: round((W["DCPF3M"][0][d] - raw["DTB3"][d]) * 100) for d in W["DCPF3M"][0] if d in raw["DTB3"]},
          {d: round((W["DCPF3M"][1][d] - raw["DTB3"][d]) * 100) for d in W["DCPF3M"][1] if d in raw["DTB3"]})
    cp_l, cp_t = r1(avg(cp[0])), r1(avg(cp[1]))
    add("pressure", "商業本票 − 國庫券 均值 (bp)", cp_l, cp_t, tr=trend_bp(cp_l, cp_t),
        alert="明顯緊張" if cp_t >= T["cp_red_bp"] else "偏高" if cp_t >= T["cp_amber_bp"] else None,
        level="red" if cp_t >= T["cp_red_bp"] else None,
        note="3 個月期 · 企業短期借錢比政府貴多少 · 偏高 ≥ 30bp", days=cp)

    # ---------- 滅火器 ----------
    srf_t = total(W["RPONTTLD"][1])
    add("extinguisher", "SRF 週合計 (十億)", total(W["RPONTTLD"][0]), srf_t,
        alert="本週有成交" if srf_t > 0 else None, note="洩壓閥：回購市場整體缺錢時使用", days=W["RPONTTLD"])
    add("extinguisher", "貼現窗口 (億)", r1(to_yi(wed("WLCFLPCL", last_wk))), r1(to_yi(wed("WLCFLPCL", this_wk))),
        note="急診室：個別銀行出事時使用 · 一級信貸週三水位")
    fima_l, fima_t = to_yi(wed("H41RESPPALGTRFNWW", last_wk)), to_yi(wed("H41RESPPALGTRFNWW", this_wk))
    add("extinguisher", "FIMA 回購 (億)", r1(fima_l), r1(fima_t),
        alert="有外國央行使用" if fima_t > 0 else None, note="外國央行拿美債向聯準會換美元 · 週三水位")
    sw_l, sw_t = to_yi(wed("SWPT", last_wk)), to_yi(wed("SWPT", this_wk))
    add("extinguisher", "央行換匯額度 (億)", r1(sw_l), r1(sw_t),
        alert="海外動用增加" if sw_t > T["swap_amber"] else None,
        note="聯準會與主要央行的美元換匯 · 平常只有數億的例行操作")

    # 原本有、但沒有「每日明細」的週資料 → 用最近幾週的週三水位當明細
    for name, sid, conv in (("準備金餘額 (兆)", "WRBWFRBL", lambda v: round(v / 1e6, 3)),
                            ("TGA (億)", "WDTGAL", lambda v: round(v / 100))):
        wk_vals = {str(monday(prev_week(this_wk, n)) + timedelta(days=2))[5:]:
                   conv(wed(sid, prev_week(this_wk, n))) for n in range(7, -1, -1)
                   if wed(sid, prev_week(this_wk, n)) is not None}
        daily[name] = {"weeks": wk_vals}

    return {
        "this_week": f"{monday(this_wk)} 起",
        "last_week": f"{monday(last_wk)} 起",
        "latest_data": str(max(raw["SOFR"])),
        "corridor": {"ceiling": top, "iorb": raw["IORB"][max(raw["IORB"])], "floor": floor},
        "categories": [{"id": c, "name": n, "desc": d} for c, n, d in CATEGORIES],
        "key_dates": key_dates(prev_week(this_wk, -1)),  # 下一週起算：報告發出後的兩週
        "rows": rows,
        "daily": daily,
    }


# ========== 6. 輸出 ==========

def show(result):
    print(f"\n本週：{result['this_week']}　上週：{result['last_week']}　"
          f"最新資料日：{result['latest_data']}")
    cats = {c["id"]: c["name"] for c in result["categories"]}
    cur = None
    for r in result["rows"]:
        if r["category"] != cur:
            cur = r["category"]
            print(f"\n【{cats[cur]}】")
        f = lambda v: "—" if v is None else f"{v:,.3f}".rstrip("0").rstrip(".")
        flag = f"  ⚠ {r['alert']}" if r["alert"] else ""
        print(f"  {r['name']:<24}{f(r['last']):>12}{f(r['this']):>12}  {r['trend']}{flag}")
    print("\n關鍵日期：", "；".join(f"{k['date'][5:]} {k['label']}" for k in result["key_dates"]) or "無")


if __name__ == "__main__":
    csv_dir = sys.argv[1] if len(sys.argv) > 1 else None
    result = build(csv_dir)
    show(result)
    with open("liquidity.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n已輸出 liquidity.json")
