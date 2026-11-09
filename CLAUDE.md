# 美元流動性 Agent 團隊

每週產出「美元流動性看板」的 agent 團隊。使用者以繁體中文溝通，回覆一律用繁體中文。

## 團隊分工
| 角色 | 由誰擔任 | 檔案 |
|---|---|---|
| 主管 | 雲端排程任務 | agents/coordinator.md |
| 資料員 | Python 程式 | liquidity.py |
| 分析師 | 子 agent | agents/analyst.md |
| 審核員 | 另一個子 agent | agents/reviewer.md |

產出順序：liquidity.py → liquidity.json → 分析師 → analysis.json → 審核員 → review.json → build_data.py → data.json → 看板

## 最重要的原則
- **數字只能來自程式**。任何 AI 角色都不得讀網頁抄數字、不得手改數字。
- 分析師與審核員必須是兩個不同的子 agent。
- 新增指標時：先在 liquidity.py 加程式計算，再更新兩份角色說明，最後更新 dashboard.html。

## 常用指令
- 用真實資料跑：`python3 liquidity.py`
- 離線測試（不連網）：`python3 liquidity.py tests/sample`
- 合併產出：`python3 build_data.py`（需要 liquidity.json、analysis.json、review.json、run_meta.json）

## 資料來源
- FRED 公開 CSV：`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<序列代號>`
- FRED 會擋不認識的 User-Agent，程式固定用 `curl/8.5.0`
- SRF 用 RPONTTLD（公債＋MBS＋機構債合計），不是 RPONTSYD
- 準備金、ON RRP 目前是 liquidity.py 設定區的固定數字

## 外部位置
- 看板：https://claude.ai/artifact/NXicQXTd3rBct5kBBY46pA （頁面讀取同目錄的 data.json）
- Google Drive「美元流動性 Agent 團隊」：角色說明的正式版本、每週週報紀錄
  - 改了 agents/ 裡的角色說明，要同步更新 Drive 上的同名文件，排程讀的是 Drive 版本
