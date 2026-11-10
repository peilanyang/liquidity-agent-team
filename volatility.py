"""
波動率看板：資料抓取與計算
--------------------------------
用法：
    python volatility.py              # 用真實資料
    python volatility.py tests/sample # 離線測試
結果：
    產生 volatility.json，給看板讀取

每一項都和自己過去 13 週的平均比較：
- 價格類（美元指數、歐元、日元、原油、黃金、白銀、比特幣）：
  每週波幅 = 該週最高價 − 該週最低價，和 13 週平均波幅比較
- 隱含波動率類（原油 OVX、黃金 GVZ）：
  該週收盤值，和 13 週平均收盤值比較

資料來源：Yahoo Finance 每日資料（含最高、最低價），程式自行按週彙整。
只用 Python 內建模組。
"""

import json
import sys
import time
import urllib.request
from datetime import date, datetime, timezone

# ========== 1. 設定區 ==========

AVG_WEEKS = 13  # 和幾週平均比較

# (顯示名稱, Yahoo 代號, 類型, 小數位)
# 類型 range：比較每週波幅；level：比較每週收盤值（隱含波動率指數）
ITEMS = [
    ("美元指數波幅", "DX-Y.NYB", "range", 2),
    ("歐元波幅", "EURUSD=X", "range", 5),
    ("日元波幅（美元兌日元）", "JPY=X", "range", 2),
    ("原油波幅（WTI）", "CL=F", "range", 2),
    ("黃金波幅", "GC=F", "range", 1),
    ("白銀波幅", "SI=F", "range", 2),
    ("比特幣波幅", "BTC-USD", "range", 0),
    ("原油隱含波動率（OVX）", "^OVX", "level", 2),
    ("黃金隱含波動率（GVZ）", "^GVZ", "level", 2),
]

YAHOO = "https://{host}/v8/finance/chart/{sym}?range=1y&interval=1d"
HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]


# ========== 2. 抓資料：直接下載原始資料，不經過 AI ==========

def fetch(sym, csv_dir=None):
    """回傳 {日期: (最高, 最低, 收盤)}，跳過缺值"""
    if csv_dir:  # 離線測試：讀存好的 JSON
        safe = sym.replace("^", "_").replace("=", "_")
        with open(f"{csv_dir}/yahoo_{safe}.json", encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload, err = None, None
        for attempt in range(4):  # 兩個主機輪流試，共 4 次
            host = HOSTS[attempt % 2]
            req = urllib.request.Request(
                YAHOO.format(host=host, sym=urllib.request.quote(sym)),
                headers={"User-Agent": "Mozilla/5.0"},
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                break
            except (TimeoutError, OSError, ValueError) as e:
                err = e
                time.sleep(3)
        if payload is None:
            raise RuntimeError(f"{sym} 下載失敗：{err}")

    res = payload["chart"]["result"][0]
    tz = res["meta"].get("gmtoffset", 0)
    q = res["indicators"]["quote"][0]
    data = {}
    for ts, h, l, c in zip(res["timestamp"], q["high"], q["low"], q["close"]):
        if None in (h, l, c):
            continue
        d = datetime.fromtimestamp(ts + tz, tz=timezone.utc).date()  # 當地交易日
        data[d] = (h, l, c)
    if not data:
        raise ValueError(f"{sym} 沒有抓到任何資料")
    return data


# ========== 3. 按週彙整 ==========

def week_key(d):
    y, w, _ = d.isocalendar()
    return (y, w)


def monday(wk):
    return date.fromisocalendar(wk[0], wk[1], 1)


def weekly(data):
    """回傳 {週: {"range": 最高−最低, "close": 週收盤, "high", "low"}}，依週排序"""
    out = {}
    for d in sorted(data):
        h, l, c = data[d]
        w = out.setdefault(week_key(d), {"high": h, "low": l, "close": c})
        w["high"], w["low"], w["close"] = max(w["high"], h), min(w["low"], l), c
    for w in out.values():
        w["range"] = w["high"] - w["low"]
    return dict(sorted(out.items()))


def target_week(today):
    """要報告的週：週六、日執行就用本週，平日執行就用上一週（上一週已完整結束）"""
    wk = week_key(today)
    if today.weekday() >= 5:
        return wk
    return week_key(date.fromordinal(monday(wk).toordinal() - 7))


# ========== 4. 計算 ==========

def trend(a, b, tol=0.02):
    if a == 0:
        return "基本持平" if b == 0 else "上升"
    r = (b - a) / abs(a)
    if abs(r) < tol:
        return "基本持平"
    if r > 0:
        return "明顯上升" if r > 0.1 else "小幅上升"
    return "明顯下降" if r < -0.1 else "小幅下降"


def build(csv_dir=None, today=None):
    today = today or date.today()
    this_wk = target_week(today)
    rows = []

    for name, sym, kind, dp in ITEMS:
        wk = weekly(fetch(sym, csv_dir))
        weeks = [w for w in wk if w <= this_wk]
        if this_wk not in wk or len(weeks) < AVG_WEEKS + 1:
            raise ValueError(f"{name}（{sym}）資料不足：本週或過去 {AVG_WEEKS} 週有缺")
        recent = weeks[-AVG_WEEKS:]              # 含本週在內的 13 週
        field = "range" if kind == "range" else "close"
        rd = lambda v: round(v, dp) if dp else round(v)
        # 先四捨五入到顯示的小數位再計算，讓看板上的數字可以直接驗算
        series = [rd(wk[w][field]) for w in recent]
        this_v, last_v = series[-1], series[-2]
        avg13 = rd(sum(series) / len(series))
        ratio = this_v / avg13 if avg13 else None
        above = this_v > avg13

        rows.append({
            "name": name,
            "symbol": sym,
            "kind": "每週波幅（最高−最低）" if kind == "range" else "每週收盤值",
            "last": rd(last_v),
            "this": rd(this_v),
            "avg13": rd(avg13),
            "ratio": round(ratio, 2) if ratio else None,
            "trend": trend(last_v, this_v),
            "alert": "大於13週均值" if above else None,
            "note": ("大於" if above else "小於") + f"13週均值（本週為均值的 {ratio:.2f} 倍）",
            "close": rd(wk[this_wk]["close"]) if kind == "range" else None,
            "weeks": {str(monday(w))[5:]: rd(wk[w][field]) for w in recent},
        })

    return {
        "this_week": f"{monday(this_wk)} 起",
        "avg_weeks": AVG_WEEKS,
        "source": "Yahoo Finance 每日資料，按週彙整",
        "rows": rows,
    }


# ========== 5. 輸出 ==========

def show(result):
    print(f"\n本週：{result['this_week']}　比較基準：{result['avg_weeks']} 週平均\n")
    print(f"{'指標':<20}{'上週':>12}{'本週':>12}{'13週均值':>12}{'倍數':>7}  狀態")
    print("-" * 80)
    for r in result["rows"]:
        print(f"{r['name']:<20}{r['last']:>12,}{r['this']:>12,}{r['avg13']:>12,}"
              f"{r['ratio']:>7}  {'⚠ ' + r['alert'] if r['alert'] else '小於13週均值'}")


if __name__ == "__main__":
    csv_dir = sys.argv[1] if len(sys.argv) > 1 else None
    # 離線測試資料固定以 2026-11-10 為執行日
    result = build(csv_dir, date(2026, 11, 10) if csv_dir else None)
    show(result)
    with open("volatility.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n已輸出 volatility.json")
