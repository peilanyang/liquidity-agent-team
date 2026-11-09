"""
美元流動性看板：資料抓取與計算
--------------------------------
用法：
    python liquidity.py
結果：
    1. 在螢幕印出上週 / 本週對照表
    2. 產生 liquidity.json，給看板讀取

只用 Python 內建模組，不需要另外安裝套件。
資料來源：聖路易聯儲 FRED 公開 CSV（不需要 API 金鑰）。
"""

import csv
import io
import json
import sys
import time
import urllib.request
from datetime import date

# ========== 1. 設定區：要改數字或門檻，只改這裡 ==========

# 暫時用固定數字的指標（之後改成自動抓取）
FIXED = {
    "reserves": {"last": 2.90, "this": 2.96},                 # 準備金，兆美元
    "rrp": {"last": {"avg": 11, "min": 2}, "this": {"avg": 12, "min": 1}},  # ON RRP，十億美元
}

# 警示門檻
THRESHOLDS = {
    "spread_bp": 7,          # SOFR-EFFR ≥ 7bp → 心絞痛
    "reserves_ample": 3.0,   # 準備金 < 3 兆 → 適度區
    "reserves_scarce": 2.3,  # 準備金 < 2.3 兆 → 稀缺區
    "rrp_scarce": 375,       # ON RRP < 3,750 億 → 稀缺區（十億美元）
    "tga_scarce": 500_000,   # TGA < 5,000 億 → 稀缺區（百萬美元）
}

# 要從 FRED 下載的序列
SERIES = {
    "SOFR": "SOFR 利率 (%)",
    "EFFR": "有效聯邦基金利率 (%)",
    "SOFR99": "SOFR 99 百分位 (%)",
    "SOFRVOL": "SOFR 交易量 (十億美元)",
    "EFFRVOL": "聯邦基金交易量 (十億美元)",
    "WLCFLPCL": "貼現窗口一級信貸 (百萬美元，週三)",
    "WTREGEN": "TGA 財政部帳戶 (百萬美元，週均)",
    "RPONTTLD": "常設回購 SRF 合計：公債+MBS+機構債 (十億美元)",
}

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}&cosd={}"


# ========== 2. 抓資料：直接下載原始 CSV，不經過 AI ==========

def fetch(series_id, start="2026-09-01", csv_dir=None):
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


def split_weeks(data, this_week, last_week):
    this = {d: v for d, v in data.items() if week_key(d) == this_week}
    last = {d: v for d, v in data.items() if week_key(d) == last_week}
    return last, this


def prev_week(wk):
    """上一週的 (年, 週)"""
    y, w = wk
    monday = date.fromisocalendar(y, w, 1)
    return week_key(date.fromordinal(monday.toordinal() - 7))


# ========== 4. 計算與警示 ==========

def trend(a, b, tol=0.02):
    if a == 0:
        return "基本持平" if b == 0 else "上升"
    r = (b - a) / abs(a)
    if abs(r) < tol:
        return "基本持平"
    if r > 0:
        return "明顯上升" if r > 0.1 else "小幅上升"
    return "明顯下降" if r < -0.1 else "小幅下降"


def build(csv_dir=None):
    raw = {sid: fetch(sid, csv_dir=csv_dir) for sid in SERIES}

    # 以 SOFR 最新一筆所在的週為「本週」
    this_wk = week_key(max(raw["SOFR"]))
    last_wk = prev_week(this_wk)
    W = {sid: split_weeks(d, this_wk, last_wk) for sid, d in raw.items()}

    def daily_spread(sofr, effr):
        return {d: round((sofr[d] - effr[d]) * 100) for d in sofr if d in effr}

    sp_last = daily_spread(*[W[s][0] for s in ("SOFR", "EFFR")])
    sp_this = daily_spread(*[W[s][1] for s in ("SOFR", "EFFR")])

    avg = lambda d: sum(d.values()) / len(d) if d else None
    mx = lambda d: max(d.values()) if d else None
    total = lambda d: sum(d.values()) if d else 0.0

    # 週資料（貼現窗口、TGA）：取最新兩筆
    def last_two(sid):
        ds = sorted(raw[sid])
        return raw[sid][ds[-2]], raw[sid][ds[-1]], ds[-2], ds[-1]

    dw_l, dw_t, *_ = last_two("WLCFLPCL")
    tga_l, tga_t, *_ = last_two("WTREGEN")

    T = THRESHOLDS
    rows = []

    def clean(v):  # 9538.0 → 9538，其他取到小數 3 位
        if isinstance(v, float):
            return int(v) if v.is_integer() else round(v, 3)
        return v

    def add(name, last, this, alert=None, note=None):
        last, this = clean(last), clean(this)
        rows.append({
            "name": name, "last": last, "this": this,
            "trend": trend(last, this) if None not in (last, this) else "資料不足",
            "alert": alert, "note": note,
        })

    s_l, s_t = mx(sp_last), mx(sp_this)
    add("SOFR-EFFR 利差最高 (bp)", s_l, s_t,
        alert="心絞痛" if s_t is not None and s_t >= T["spread_bp"] else None,
        note=f"警示：≥ {T['spread_bp']}bp")
    add("EFFR 最高 (%)", mx(W["EFFR"][0]), mx(W["EFFR"][1]))
    add("貼現窗口 (百萬美元)", dw_l, dw_t)
    add("聯邦基金交易量均值 (十億)", avg(W["EFFRVOL"][0]), avg(W["EFFRVOL"][1]))
    add("SOFR 交易量均值 (十億)", avg(W["SOFRVOL"][0]), avg(W["SOFRVOL"][1]))
    add("SOFR99 最高 (%)", mx(W["SOFR99"][0]), mx(W["SOFR99"][1]))

    r = FIXED["reserves"]
    add("準備金餘額 (兆，固定)", r["last"], r["this"],
        alert="稀缺區" if r["this"] < T["reserves_scarce"]
        else "適度區" if r["this"] < T["reserves_ample"] else None)

    q = FIXED["rrp"]
    add("ON RRP 均值 (十億，固定)", q["last"]["avg"], q["this"]["avg"],
        alert="稀缺區" if q["this"]["avg"] < T["rrp_scarce"] else None,
        note=f"最低 {q['last']['min']} → {q['this']['min']}")

    add("TGA (百萬美元)", tga_l, tga_t,
        alert="稀缺區" if tga_t < T["tga_scarce"] else None)

    srf_t = total(W["RPONTTLD"][1])
    add("SRF 週合計 (十億)", total(W["RPONTTLD"][0]), srf_t,
        alert="本週有成交" if srf_t > 0 else None)

    return {
        "this_week": f"{date.fromisocalendar(*this_wk, 1)} 起",
        "last_week": f"{date.fromisocalendar(*last_wk, 1)} 起",
        "latest_data": str(max(raw["SOFR"])),
        "rows": rows,
        "daily": {  # 每日明細，給看板展開用（順序對應 rows）
            name: {"last": {str(k)[5:]: clean(v) for k, v in l.items()},
                   "this": {str(k)[5:]: clean(v) for k, v in t.items()}}
            for name, (l, t) in {
                "SOFR-EFFR 利差最高 (bp)": (sp_last, sp_this),
                "EFFR 最高 (%)": W["EFFR"],
                "聯邦基金交易量均值 (十億)": W["EFFRVOL"],
                "SOFR 交易量均值 (十億)": W["SOFRVOL"],
                "SOFR99 最高 (%)": W["SOFR99"],
                "SRF 週合計 (十億)": W["RPONTTLD"],
            }.items()
        },
    }


# ========== 5. 輸出 ==========

def show(result):
    print(f"\n本週：{result['this_week']}　上週：{result['last_week']}　"
          f"最新資料日：{result['latest_data']}\n")
    print(f"{'指標':<26}{'上週':>14}{'本週':>14}  判讀")
    print("-" * 72)
    for r in result["rows"]:
        f = lambda v: "—" if v is None else f"{v:,.3f}".rstrip("0").rstrip(".")
        flag = f"  ⚠ {r['alert']}" if r["alert"] else ""
        print(f"{r['name']:<26}{f(r['last']):>14}{f(r['this']):>14}  {r['trend']}{flag}")


if __name__ == "__main__":
    csv_dir = sys.argv[1] if len(sys.argv) > 1 else None
    result = build(csv_dir)
    show(result)
    with open("liquidity.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n已輸出 liquidity.json")
