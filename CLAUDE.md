# 全球金融風險看板 Agent 團隊

每週產出「全球金融風險看板」的 agent 團隊，目前兩張表：美元流動性、波動率。使用者以繁體中文溝通，回覆一律用繁體中文。

## 團隊分工
| 角色 | 由誰擔任 | 檔案 |
|---|---|---|
| 主管 | 雲端排程任務 | agents/coordinator.md |
| 資料員 | Python 程式 | liquidity.py（流動性）、volatility.py（波動率） |
| 分析師 | 子 agent | agents/analyst.md |
| 審核員 | 另一個子 agent | agents/reviewer.md |

產出順序：liquidity.py + volatility.py → liquidity.json + volatility.json → 分析師 → analysis.json → 審核員 → review.json → build_data.py → data.json → 看板

## 最重要的原則
- **數字只能來自程式**。任何 AI 角色都不得讀網頁抄數字、不得手改數字。
- 分析師與審核員必須是兩個不同的子 agent。
- 新增指標時：先在 liquidity.py 加程式計算，再更新兩份角色說明，最後更新 dashboard.html。

## 常用指令
- 用真實資料跑：`python3 liquidity.py`、`python3 volatility.py`
- 離線測試（不連網）：`python3 liquidity.py tests/sample`、`python3 volatility.py tests/sample`
- 合併產出：`python3 build_data.py`（需要 liquidity.json、volatility.json、analysis.json、review.json、run_meta.json）

## 資料來源
- FRED 公開 CSV：`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<序列代號>`
- FRED 會擋不認識的 User-Agent，程式固定用 `curl/8.5.0`
- SRF 用 RPONTTLD（公債＋MBS＋機構債合計），不是 RPONTSYD
- 準備金用 WRBWFRBL（週三水位，百萬美元，程式換算成兆）
- ON RRP 用 RRPONTSYD（十億美元，程式乘 10 換算成億美元，和報表單位一致）
- 目前所有指標都自動抓取，沒有固定數字
- 波動率用 Yahoo Finance 每日資料（含最高、最低價），程式自行按週彙整：價格類比較每週波幅（最高−最低），OVX、GVZ 比較每週收盤；都和含本週在內的 13 週平均比較
- Yahoo 的連線設定寫在 volatility.py，query1、query2 兩個主機輪流重試
- 數字先四捨五入到顯示位數再算均值與倍數，看板上的數字才能直接驗算（審核員會驗）

## 外部位置
- 看板：https://claude.ai/artifact/NXicQXTd3rBct5kBBY46pA （頁面讀取同目錄的 data.json）
- Google Drive「美元流動性 Agent 團隊」：角色說明的正式版本、每週週報紀錄
  - 改了 agents/ 裡的角色說明，要同步更新 Drive 上的同名文件，排程讀的是 Drive 版本
