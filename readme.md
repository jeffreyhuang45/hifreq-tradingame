GitHub Repository 結構藍圖

- Repo 結構是「AI Agent 友善」的 :
	-  結構 = 規格 = 任務邊界  :  每一個資料夾 一對一對應 Copilot 任務：
Task #
Repo 目錄
AI Agent 任務
Task #1
src/events/
Event Canonical Model , 定義 Canonical Event Model
Task #2
src/oms/
OMS 狀態機 , 實作訂單狀態機（唯一權威）
Task #3
src/matching_engine/
撮合引擎 , 實作撮合引擎（純函式邏輯）
Task #4
src/account/
Event Replay , 實作帳戶 Event Projection
Task #5
src/storage/
Data Pump , 實作 Event Data Pump
Task #6
src/api/
REST / WebSocket , 實作 REST / WebSocket
Task #7
tests/replay/
驗證 Deterministic Replay

1. Repo Root 結構（總覽）

```
trading-sim/
├─ README.md
├─ .gitignore
├─ pyproject.toml          # 或 package.json / go.mod（依語言）
├─ Makefile                # optional：常用指令封裝
├─ docs/
│   ├─ design-spec.md      # Design Specification Document（完整版）
│   ├─ copilot-agent.md    # Copilot Agent 任務規格檔
│   └─ architecture.md     # 架構與事件流程圖（可選）
│
├─ src/
│   ├─ common/
│   │   ├─ __init__
│   │   ├─ types.py        # UUID, Money, Qty, Timestamp
│   │   ├─ errors.py       # Domain errors
│   │   └─ clock.py        # 可 mock 的時間來源
│   │
│   ├─ events/
│   │   ├─ __init__
│   │   ├─ base_event.py
│   │   ├─ order_events.py
│   │   ├─ trade_events.py
│   │   ├─ account_events.py
│   │   └─ market_events.py
│   │
│   ├─ oms/
│   │   ├─ __init__
│   │   ├─ order.py
│   │   ├─ order_state_machine.py
│   │   ├─ order_repository.py
│   │   └─ oms_service.py
│   │
│   ├─ matching_engine/
│   │   ├─ __init__
│   │   ├─ order_book.py
│   │   ├─ matcher.py
│   │   └─ price_time_priority.py
│   │
│   ├─ account/
│   │   ├─ __init__
│   │   ├─ account.py
│   │   └─ account_projection.py
│   │
│   ├─ market_data/
│   │   ├─ __init__
│   │   ├─ md_adapter.py
│   │   └─ md_normalizer.py
│   │
│   ├─ storage/
│   │   ├─ __init__
│   │   ├─ event_writer.py
│   │   ├─ event_loader.py
│   │   └─ snapshot.py
│   │
│   ├─ api/
│   │   ├─ __init__
│   │   ├─ rest.py
│   │   ├─ websocket.py
│   │   └─ schemas.py
│   │
│   └─ app.py              # 系統組裝（bootstrap & wiring）
│
├─ tests/
│   ├─ unit/
│   │   ├─ test_oms.py
│   │   ├─ test_matching_engine.py
│   │   ├─ test_account_projection.py
│   │   └─ test_events.py
│   │
│   ├─ integration/
│   │   ├─ test_order_to_trade_flow.py
│   │   └─ test_cancel_flow.py
│   │
│   └─ replay/
│       ├─ test_replay_deterministic.py
│       └─ sample_event_log.jsonl
│
└─ scripts/
    ├─ run_dev.sh
    ├─ replay_events.sh
    └─ seed_demo_data.py
```
