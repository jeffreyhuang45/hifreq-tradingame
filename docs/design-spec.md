# 模擬遊戲系統 - 簡易證券交易下單與撮合模擬遊戲設計規格說明書

## 0. 文件資訊（Document Control）

| 欄位 | 內容 |
| --- | --- |
| 文檔標題 | 模擬遊戲系統 - 簡易證券交易下單與撮合模擬遊戲設計規格說明書 |
| 文件版本 | V1.1（Agent-Executable 修訂版） |
| 適用版本 | V1 – 簡易版（Event-Driven Modular Monolith） |
| 最後更新日期 | 2026-03-03 |
| 目標讀者 | Backend / Frontend / QA / DevOps / 架構師 / 使用者 |
| 編製單位 | 系統架構設計小組 |
| 審查狀態 | 草稿階段（Design Review 待進行） |
| 設計原則 | Event-Driven、In-Memory、Deterministic、Replayable |

## 1. 名詞表（Glossary）

- **OMS**：Order Management System，訂單管理系統，負責訂單接收、驗證、風控與狀態管理。
- **ME**：Matching Engine，撮合引擎，依價格優先、時間優先規則撮合訂單。
- **MD**：Market Data Engine，行情引擎，接收並標準化外部行情。
- **EDA**：Event-Driven Architecture，事件驅動架構，採用非同步事件通訊。
- **FIFO**：First-In First-Out，先到先服務原則。
- **Critical Path**：關鍵路徑，下單→收單確認→進入撮合佇列的最小延遲路徑（不含檔案落盤）。
- **SoT**：Source of Truth，系統權威事件日誌。
- **In-Memory**：記憶體內運算，訂單簿與撮合狀態駐留於 RAM。
- **Data Pump**：數據泵，將事件異步批次寫入檔案的機制。
- **DSD**：Design Specification Document，本文件。
- **ADR**：Architecture Decision Record，架構決策紀錄。
- **RTO/RPO**：Recovery Time/Point Objective，災難復原目標。
- **TWSE / TPEx**：台灣證券交易所 / 櫃買中心。

## 2. 簡介與設計目標

### 2.1 專案背景與目標

一套為 10–12 歲孩童打造的即時行情同步 × 虛擬貨幣 × 公平撮合的證券交易模擬遊戲系統：

1. **專案背景**：以沉浸式體驗與可愛 Web UI 介面，協助青少年理解股票交易、風險管理與資產配置。
2. **專案目的**：透過虛擬貨幣帳戶與真實 TWSE/TPEx 行情，讓使用者在 on-premise In-Memory 撮合環境中進行下單與撮合練習，系統需處理 5 orders/s 的常態流量，操作介面必須極簡。
3. **系統目標**：
   - 教學導向：理解價格、時間、風險、資產配置。
   - 技術導向：呈現真實交易系統的最小正確架構。
   - 工程導向：行為可重放、結果可驗證、流程具決 determinism。

### 2.2 利益相關者

| 角色 | 需求 | 系統支援 |
| --- | --- | --- |
| 使用者（交易員 / 遊戲者） | 直觀交易 UI、即時行情、成交確認、投組查詢、虛擬資金管理 | 下單/撤單、行情查詢、帳戶管理、成交推播、排行榜 |
| 管理者 / 運維 | 穩定性、監控、告警、快速恢復 | Metrics、告警、事件重放、冷啟動流程 |
| 開發 / 測試 | POC/Prod 雙環境、重現性、測試數據 | Deterministic 撮合、事件重放、測試資料生成 |
| 架構師 | 標準化 API、明確模組邊界、低耦合 | 公開 API、事件驅動框架、清晰數據流 |

### 2.3 核心價值 / 必要功能

1. **即時真實行情**：透過 TWSE/TPEx Open API 攝取行情，內部時間序列與真實市場一致。
2. **高效能記憶體撮合**：In-Memory ME，平均撮合延遲 < 50 ms。
3. **公平透明撮合**：嚴格價格優先、時間優先（FIFO）。
4. **虛擬證券帳戶**：現金與持股帳戶、資金鎖定機制。
5. **可愛遊戲化 UI**：降低恐懼、提升參與度。

### 2.4 範圍定義

- **In Scope**：OMS、ME、MD、帳戶資金、文件儲存、冷啟動恢復、事件驅動通訊。
- **Out of Scope**：實體交割、與正式券商對接、進階資訊安全、衍生品、國際市場、融資融券。

### 2.5 設計目標與 KPI

**功能性需求**：下單、撤單、撮合、成交回報、行情查詢、帳戶管理、訂單查詢、排行榜。

**非功能性需求**：

- 訂單吞吐：常態 2–5 orders/s，尖峰 burst 20 orders/s（3 秒內）。
- API 延遲：平均 < 300 ms，P95 < 800 ms，P99 < 1 s。
- 撮合延遲：平均 < 50 ms。
- 持久化延遲：P95 < 3 s（異步）。
- 可用性：99.9%（非尖峰）；尖峰可降級。
- 不丟單、不重複撮合。
- RTO ≤ 15 分鐘；災難復原 ≤ 30 分鐘。
- 強一致性：事件日誌為 SoT，OMS ACK 前必須先落事件。
- Observability：orders_received_total、orders_rejected_total{reason}、match_latency_ms、api_latency_ms{endpoint}、queue_depth{queue}、data_pump_flush_duration_ms；可選 tracing；結構化 JSON logs。

### 2.6 約束與不妥協原則

1. Definition of Done：任務獨立、可 replay、無跨模組 side effect。
2. 帳戶現金 / 交易紀錄必須為 File-Based（Local Excel + Google Sheets），禁止 SQL DB。
3. 模組邊界：OMS、ME、Account 不可混雜；OMS 不碰 Storage；ME 不看 Account；Account 仰賴事件重建。
4. 事件模型不可省略；不得直接改 Account/Order 狀態，必須透過 Event。
5. Critical Path 禁做 File I/O；禁止引入資料庫。
6. Matching Engine 不處理資金邏輯。
7. 保證不丟單、不重複撮合；維持價格時間優先；SoT 為 append-only event log。

### 2.7 技術假設

- 單機、單行程、單撮合執行緒。
- In-Memory 為主；事件為唯一真相來源。

### 2.8 ADR 摘要

| ADR | 決策 |
| --- | --- |
| ADR-001 | In-Memory + 單執行緒序列化處理確保公平與可重現 |
| ADR-002 | 交易事件寫入持久化佇列確保不丟單 |
| ADR-003 | Data Pump 批次落盤，移出 critical path |
| ADR-004 | 採事件溯源（Event Log）確保一致性 |

## 3. 系統架構概覽

### 3.1 架構選擇

- **V1：Event-Driven Modular Monolith**（本版本主題）
  - In-process event bus 連結 Gateway、OMS、ME、MD、Account。
  - Data Pump Worker 於獨立 thread 處理持久化。
- **V2：Event-Driven Microservices**（預留升級）
  - 各模組容器化，透過 MQ/PubSub（RabbitMQ/NATS/Redis Streams）溝通；部署於 Docker + Kubernetes (EKS)。

### 3.2 模組化單體優劣

- 優點：開發簡單、無網路開銷、快速迭代，適合教育場景。
- 缺點：擴展受限、故障隔離較弱。

### 3.3 設計原則

1. 高內聚低耦合；Market Data 變更不得影響 ME。
2. 任務完成定義同 2.6。
3. 採 CQRS；交易資料存 In-Memory；持久化透過 Data Pump。
4. 持久化佇列保障 FIFO；PubSub 廣播行情。
5. 定義冷啟動讀檔 → 重建 → 健檢流程。
6. 降級策略：行情不可用時拒絕市價單；Storage backlog 過大只保證事件日誌；過載時 Gateway 回 `SYSTEM_OVERLOAD`。

### 3.4 七大模組（高階）

```
┌────────────────────────────────────────────┐
│ 展示層：Frontend (React, Cute Game UI)                              │
└────────────┬─────────────────────────────┘
             │ REST / WebSocket
┌────────────▼─────────────────────────────┐
│ 網關層：FastAPI Gateway（驗證、路由、限流）             │
└────────────┬─────────────────────────────┘
             │ In-Process Event Bus / MQ
┌────────────▼─────────────────────────────┐
│ 應用層：OMS / ME / MD / Account                                     │
└────────────┬─────────────────────────────┘
             │ Async Event / Data Channel
┌────────────▼─────────────────────────────┐
│ 資料層：Storage Engine（Data Pump → File-Based）          │
└──────────────────────────────────────────┘
```

### 3.5 核心事件流（Happy Path）

1. `PlaceOrderRequest`
2. `OrderAcceptedEvent`（SoT）
3. `OrderRoutedToME`
4. `TradeExecutedEvent`
5. `AccountUpdatedEvent`
6. Frontend WebSocket 推播

## 4. 模組詳細設計規格

### 4.1 模組 1：Frontend（Web UI/UX）

- **功能**：登入、帳戶儀表板、行情看盤、下單/撤單、今日訂單追蹤、排行榜。
- **設計原則**：
  - 可愛遊戲風格，遵循對比/重複/對齊/親密性。
  - 重要按鈕（下單、撤單）具高可發現性與安全確認。
  - 使用最新 React + Vite，狀態管理建議 Redux Toolkit。
  - 實時通訊 via WebSocket；HTTP via REST。
  - **技術註記**：Web 端 BFF 若需 Node/Express，必須使用最新 Express.js，進行功能/非功能問題檢測並加入例外處理（作為前端 BFF 參考實作要求）。
- **子功能**：
  1. 帳戶儀表板（現金/持股/收益），含密碼管理、虛擬貨幣流表、歷史持股。
  2. 看盤（大盤/個股），展示圖表與行情。
  3. 交易介面（下單、今日訂單追蹤）。
- **邊界上下文**：只透過公開 REST / WebSocket 與後端互動，不接觸內部儲存或佇列。

### 4.2 模組 2：API Gateway（FastAPI Tiny Web Server）

- **目標**：唯一入口，輕量路由。
- **功能**：身份驗證、請求驗證、轉發至 OMS/Account/MD、統一錯誤、限流。
- **設計原則**：
  - Tiny Web Service，業務邏輯下沉。
  - 全異步 I/O（FastAPI + uvicorn）。
  - 必備 API：POST `/orders`、DELETE `/orders/{id}`、GET `/orders`，WebSocket 推播。
- **邊界上下文**：無狀態，只負責傳輸。
- **驗收**：WebSocket 延遲 < 1 s。

### 4.3 模組 3：Account / Client-Side Backend Services

- **目標**：帳戶狀態完全由事件重建。
- **功能**：帳戶 CRUD、密碼管理、虛擬資金查詢與鎖定、持股管理、行情唯讀查詢、交易歷史分析。
- **技術**：Python FastAPI 或 Node.js（Express）；內存快取 + 檔案讀取；需提供操作/系統日誌與效能指標。
- **設計原則**：讀寫分離、權限檢查、事件驅動更新。
- **輸入**：`TradeExecutedEvent`、`OrderCanceledEvent`。
- **輸出**：`account_projection`（In-Memory 狀態），可供查詢 API。
- **驗收**：Event Replay 可還原帳戶狀態；不得直接修改帳戶。

### 4.4 模組 4：Order Management System（OMS）

- **目標**：訂單唯一權威，狀態機；負責風控與持久化。
- **功能**：訂單接收、風險檢查、資金/持股鎖定、訂單狀態管理、撤單處理、事件發佈。
- **設計**：
  - 狀態機：PENDING → ACCEPTED → ROUTED → {PARTIALLY_FILLED | FILLED | CANCELED | REJECTED}。
  - 風險檢查：BUY 驗現金、SELL 驗持股；不足則 `INSUFFICIENT_CASH/INSUFFICIENT_POSITION`。
  - 熔斷：行情不可用時拒絕市價單。
  - Mediator + Orchestration 模式；Order ID 冪等。
- **輸入/輸出**：
  - Input：Order Model、Event Model。
  - Output：`order_state_machine`、`order_repository`（In-Memory）、`OrderAccepted`、`OrderRejected`、`OrderCanceled`。
- **驗收**：非法狀態轉移應報錯；重複事件不影響狀態；OMS ACK 前必寫事件。

### 4.5 模組 5：Market Data Processing Engine

- **職責**：接收 TWSE/TPEx 行情、正規化、發布。
- **功能**：外部 API 取數、資料標準化、發布 `MarketDataUpdatedEvent`、提供降級策略（使用最後成功行情或標記過期）。
- **設計**：Pub/Sub Producer、Adapter + Observer 模式；行情 SoT。
- **輸出格式**：NDJSON（含 symbol、bid/ask、last、volume、trades_count）。
- **降級策略**：API 無回應 → 使用快取；OMS 接獲「行情不可用」→ 禁市價單。

### 4.6 模組 6：Trading Matching Engine

- **目標**：純撮合、無副作用；維持價格/時間優先。
- **功能**：維護訂單簿、執行撮合、產生成交事件、處理部分成交與留單、補償事件。
- **設計**：
  - In-Memory 單執行緒；FIFO 排程；order book per symbol。
  - BUY：MaxHeap（price DESC, time ASC）；SELL：MinHeap（price ASC, time ASC）。
  - 成交條件：BUY price ≥ SELL price；成交價 = Maker 價格；允許部分成交。
  - 產生 `TradeExecutedEvent`、`OrderRemainOnBookEvent`、`CancelEvent`。
  - 補償：無法成交則通知 OMS 解鎖。
- **驗收**：無重複撮合；成交量正確；重啟後 replay 一致。

### 4.7 模組 7：Storage Engine（Data Pump）

- **目標**：Event 非同步寫檔，供 replay；移除 Critical Path I/O。
- **功能**：append-only event log、可選 snapshot、報表匯出、冷啟動重建、災難備援。
- **設計**：
  - Data Pump（半同步/半非同步）；buffer + batch flush（ex: 每 100 筆或 1 秒）。
  - 目錄：
    - `data/events/YYYY-MM/events-YYYY-MM-DD.log`
    - `data/snapshots/YYYY-MM/snapshot-YYYY-MM-DD-HHMM.json`
    - `data/reports/YYYY-MM/trades-YYYY-MM-DD.csv`
    - `data/reports/YYYY-MM/orders-YYYY-MM-DD.csv`
  - 冷啟動：讀 snapshot → replay event log → 重建 order book / order status / account。
  - 災難復原：data/ 同步至雲（Google Drive / S3）；RTO ≤ 30 分鐘，RPO ≤ 1 分鐘。
- **驗收**：冷啟動可完整 replay；事件寫檔不可阻塞 OMS/ME。

## 5. Canonical Models（Domain Models）

### 5.1 Account

```json
{
  "account_id": "uuid",
  "cash_available": 100000,
  "cash_locked": 20000,
  "positions": [
    { "symbol": "2330", "qty_available": 100, "qty_locked": 0 }
  ]
}
```

### 5.2 Order

```json
{
  "order_id": "uuid",
  "account_id": "uuid",
  "symbol": "2330",
  "side": "BUY | SELL",
  "type": "LIMIT | MARKET",
  "price": 501.0,
  "qty": 1000,
  "status": "PENDING | ACCEPTED | ROUTED | PARTIALLY_FILLED | FILLED | CANCELED | REJECTED",
  "created_at": "ISO8601"
}
```

### 5.3 Trade

```json
{
  "trade_id": "uuid",
  "symbol": "2330",
  "price": 501.0,
  "qty": 1000,
  "buy_order_id": "oid-1",
  "sell_order_id": "oid-2",
  "matched_at": "ISO8601"
}
```

## 6. Canonical Event Model

### 6.1 共同欄位

| 欄位 | 說明 |
| --- | --- |
| `event_id` | UUID，去重依據 |
| `event_type` | 事件類型（字串枚舉） |
| `occurred_at` | ISO8601 時間 |
| `correlation_id` | 追蹤同一流程 |
| `producer` | 事件產生模組 |
| `payload_version` | 版本控制 |

### 6.2 核心事件清單

- `OrderPlacedEvent`
- `OrderAcceptedEvent`
- `OrderRejectedEvent`
- `OrderCanceledEvent`
- `OrderRoutedEvent`
- `TradeExecutedEvent`
- `AccountUpdatedEvent`
- `MarketDataUpdatedEvent`

### 6.3 Topics / Queues（V1 命名建議）

| 通道 | 說明 |
| --- | --- |
| `orders.in` | Gateway → OMS（durable queue） |
| `orders.match.{symbol}` | OMS → ME（per symbol FIFO） |
| `trades.out` | ME → OMS/Storage/WebSocket（topic） |
| `marketdata` | MD → 所有訂閱者（topic） |
| `storage.write` | 所有事件 → Data Pump（durable queue） |

### 6.4 傳遞語意

- Delivery：至少一次（at-least-once）。
- Consumer：必須以 `event_id` 去重。
- `payload_version` 用於向後相容。

## 7. 通訊協定與 API 設計

### 7.1 外部介面（Frontend ↔ Gateway）

- REST：
  - `POST /api/v1/orders` → {symbol, side, price, qty, type} → 回傳 {order_id, status="PENDING"}。
  - `GET /api/v1/market-data/{symbol}` → 即時報價。
  - `GET /api/v1/portfolio` → 帳戶餘額與庫存。
- WebSocket `/ws`：
  - Channel `orders.{account_id}`：訂單狀態。
  - Channel `trades.{account_id}`：成交回報。
  - Channel `market.{symbol}`：行情推播。

### 7.2 內部介面

| 模組 | 協定 | 說明 |
| --- | --- | --- |
| Gateway ↔ OMS | REST / gRPC（可升級） | 低延遲下單、撤單 |
| OMS ↔ Account | 同步查詢（REST/gRPC） + 事件驅動 | 餘額檢查、鎖定 |
| OMS ↔ ME | 持久化 FIFO Queue | 保證順序與公平 |
| MD ↔ 其他 | Pub/Sub（WebSocket 或 MQ） | 行情廣播 |
| ME ↔ Storage | 事件佇列 → Data Pump | 批次寫檔 |

## 8. 資料流詳細規範

### 8.1 序列流程

1. **用戶下單**：User → FastAPI → `orders.in` queue → OMS。
2. **驗證與入簿**：OMS → Account（餘額鎖定）→ `OrderAcceptedEvent` → `orders.match.{symbol}`。
3. **行情觸發**：外部 API → MD Adapter → `marketdata` topic → ME / UI / OMS。
4. **撮合與回報**：ME → `TradeExecutedEvent` → OMS（更新狀態+WS 推播）與 Data Pump。
5. **非同步持久化**：Data Pump buffer → Excel/Google Sheets/CSV。

### 8.2 Half-Sync / Half-Async 模式

- Sync Layer：Gateway / OMS / ME 接單與撮合。
- Queueing Layer：`orders.in`、`orders.match.*`、`storage.write`。
- Async Layer：Data Pump。

## 9. 資料拓撲與儲存

- 文件式存儲（Local Excel + Google Sheets + CSV/JSON）。
- 分片方式：按月/日分檔；append-only。
- 讀寫優先：高寫入優先，透過 Data Pump 緩衝。
- 一致性：強一致；避免因文件鎖造成阻塞，採批次寫入 + 檔案鎖協定。
- 風險：Excel/GoogleSheets I/O 慢 → 以緩衝/批次/半同步解決。
- DR：data/ 目錄備份至雲，RTO ≤ 30 min，RPO ≤ 1 min。

## 10. 測試與驗收標準

### 10.1 單模組測試

1. **Frontend**：UI 流程、登入/密碼管理、虛擬資金流表、持股查詢、看盤、交易介面、今日訂單追蹤、排行榜；BFF 需測試 Express.js 例外處理。
2. **Gateway**：API 驗證、限流、WS < 1 s。
3. **Account Services**：帳戶 CRUD、事件重播、權限檢查、操作/系統日誌、Metrics。
4. **OMS**：狀態機轉移、風險檢查、資金鎖定、熔斷、冪等事件。
5. **Market Data Engine**：API 攝取、標準化、發布、降級。
6. **Matching Engine**：價格/時間優先、部分成交、撤單、重啟重播、補償事件。
7. **Storage Engine**：Data Pump 緩衝、批次寫入、冷啟動 replay、DR 演練。

### 10.2 關鍵工作流程測試

- 下單→撮合→成交回報→帳戶更新。
- 撤單流程（含資金解鎖）。
- 行情故障降級（市價單禁用）。

### 10.3 壓力測試

- 輸入 5 orders/s（常態）與 20 orders/s（burst 3 秒）；驗證 API P95 < 800 ms、撮合平均 < 50 ms、佇列無無限成長。

### 10.4 一致性測試

- 任意時間 crash → 冷啟動 → 狀態一致。
- 確保無重複成交、無負餘額或負持股。

## 11. Agent 任務指引

| Task # | 目錄 | 任務 |
| --- | --- | --- |
| 1 | `src/events/` | 定義 Canonical Event Model |
| 2 | `src/oms/` | 實作 OMS 狀態機（唯一權威） |
| 3 | `src/matching_engine/` | 撮合引擎（純函式邏輯） |
| 4 | `src/account/` | Event Replay（帳戶 Projection） |
| 5 | `src/storage/` | Data Pump（Event Writer / Loader） |
| 6 | `src/api/` | REST / WebSocket 服務 |
| 7 | `tests/replay/` | Deterministic Replay 測試 |

- **執行順序**：嚴格依序完成，禁止跳步或跨模組寫狀態。
- **app.py 責任**：只允許初始化、訂閱事件、啟動 API/WS；嚴禁商業邏輯。

## 12. README 提示（摘要）

- Folder boundary = Module boundary = Agent boundary。
- 專案目的：教育 + 工程示範 + AI Agent 友善。
- 設計原則（Non-Negotiables）：事件 SoT、價格/時間優先、不丟單、不重複撮合、事件 Replay、Critical Path 無 File I/O。
- 單元測試範本需對應各模組。

---

本文件為 V1.1（Agent-Executable）版設計規格，供後續模組實作、測試與審查使用。