# liquidity-agent-team

watch 各國資金流動性的每周看板

## 美元流動性 Agent 團隊

每週自動產出美元流動性看板：程式抓 FRED 資料並計算，AI 分析師撰寫判讀，另一個 AI 審核員逐句核對，通過後發布。

## 快速開始
需要 Python 3.9 以上，不用安裝任何套件。

```bash
# 用真實資料
python3 liquidity.py
python3 volatility.py

# 離線測試
python3 liquidity.py tests/sample
python3 volatility.py tests/sample
```

## 檔案
- `liquidity.py`：資料員。下載 FRED、按週切分、計算、套用警示門檻
- `volatility.py`：資料員。下載 Yahoo Finance、按週彙整、和 13 週均值比較
- `build_data.py`：合併三個角色的產出成 `data.json`
- `agents/`：主管、分析師、審核員的角色說明
- `dashboard.html`：看板頁面，讀取同目錄的 `data.json`
- `tests/sample/`：離線測試用的 CSV

## 在 VS Code 開發
安裝 Claude Code 擴充套件後打開這個資料夾，Claude 會先讀 `CLAUDE.md` 了解專案規則。
