"""合併資料員、分析師、審核員的產出，成為看板讀取的 data.json"""
import json
from datetime import datetime, timezone, timedelta

liq = json.load(open("liquidity.json", encoding="utf-8"))
ana = json.load(open("analysis.json", encoding="utf-8"))
rev = json.load(open("review.json", encoding="utf-8"))
meta = json.load(open("run_meta.json", encoding="utf-8"))

comments = {i["name"]: i["comment"] for i in ana["items"]}
missing = [r["name"] for r in liq["rows"] if r["name"] not in comments]
if missing:
    raise SystemExit(f"分析師漏寫：{missing}")

for r in liq["rows"]:
    r["comment"] = comments[r["name"]]

liq["summary"] = ana["summary"]
liq["review"] = {"verdict": rev["verdict"], "issues": rev["issues"],
                 "rounds": meta["rounds"], "first_round_issues": meta["first_round_issues"]}
liq["generated_at"] = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M 台北時間")

json.dump(liq, open("data.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("data.json 已產生：", liq["review"]["verdict"], "，", len(liq["rows"]), "項指標")
