"""合併資料員、分析師、審核員的產出，成為看板讀取的 data.json

需要：liquidity.json、volatility.json、analysis.json、review.json、run_meta.json
"""
import json
from datetime import datetime, timezone, timedelta

load = lambda f: json.load(open(f, encoding="utf-8"))
liq = load("liquidity.json")
vol = load("volatility.json")
ana = load("analysis.json")
rev = load("review.json")
meta = load("run_meta.json")

comments = {i["name"]: i["comment"] for i in ana["items"]}
all_rows = liq["rows"] + vol["rows"]
missing = [r["name"] for r in all_rows if r["name"] not in comments]
if missing:
    raise SystemExit(f"分析師漏寫：{missing}")
if liq["this_week"] != vol["this_week"]:
    raise SystemExit(f"兩張表的週不一致：流動性 {liq['this_week']}，波動率 {vol['this_week']}")

for r in all_rows:
    r["comment"] = comments[r["name"]]

out = liq  # 流動性表的欄位維持在最上層，舊版看板也能讀
out["volatility"] = vol
out["summary"] = ana["summary"]
out["review"] = {"verdict": rev["verdict"], "issues": rev["issues"],
                 "rounds": meta["rounds"], "first_round_issues": meta["first_round_issues"]}
out["generated_at"] = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M 台北時間")

json.dump(out, open("data.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("data.json 已產生：", rev["verdict"], "，流動性", len(liq["rows"]), "項、波動率", len(vol["rows"]), "項")
