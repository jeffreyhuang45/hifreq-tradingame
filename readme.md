# Hi-Freq TradingGame 📈

**簡易證券交易下單與撮合模擬遊戲**

A full-stack securities trading simulator built with **FastAPI + Vanilla JS**, featuring real-time WebSocket market data, dual matching engine modes, and event-driven architecture.

> Designed as an educational platform for learning stock trading mechanics — order placement, matching, settlement, and portfolio management.

---

## Features

- **Real-time Market Data** — Live quotes with bid/ask prices, volume, and candlestick/KD charts (via TWSE API or simulated adapter)
- **Dual Matching Engine**
  - **Engine A** — Order vs Market: matches user orders against real-time market bid/ask prices
  - **Engine B** — Order vs Order: classic price-time priority matching between user orders
- **Order Management** — Limit & Market orders, partial fills, cancel, full order lifecycle (PENDING → ACCEPTED → ROUTED → FILLED)
- **Portfolio Dashboard** — Cash balance, locked funds, holdings with P&L, ROI, cash flow history
- **Leaderboard** — Rank users by total assets and performance
- **Admin Panel** — User CRUD, password reset, system monitoring, circuit breaker status
- **WebSocket Push** — Real-time order status, trade notifications, and market updates
- **Event Sourcing** — Append-only event log, deterministic replay, cold-start recovery
- **JWT Auth** — PBKDF2-SHA256 password hashing, role-based access (user / admin)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, Uvicorn |
| Frontend | Vanilla JavaScript, HTML/CSS, Chart.js |
| Auth | JWT (HS256), PBKDF2-SHA256 |
| Market Data | TWSE/TPEx OpenAPI, SimulatedAdapter |
| Storage | File-based event log (JSONL), Excel/Google Sheets export |
| Testing | Pytest, Playwright (E2E) |

---

## Quick Start

### Prerequisites

- Python 3.11 or higher

### Install & Run

```bash
# Clone the repository
git clone https://github.com/jeffreyhuang45/hifreq-tradingame.git
cd hifreq-tradingame

# Install dependencies
pip install -e .

# Start the server
uvicorn src.app:create_app --factory --reload
```

Open **http://127.0.0.1:8000** in your browser.

A default admin account is created automatically on first startup.

---

## Architecture

```
┌──────────────────────────────────┐
│  Frontend (Vanilla JS + CSS)     │
└──────────────┬───────────────────┘
               │ REST / WebSocket
┌──────────────▼───────────────────┐
│  API Gateway (FastAPI)           │
│  Auth · Rate Limit · CORS       │
└──────────────┬───────────────────┘
               │ In-Process Event Bus
┌──────────────▼───────────────────┐
│  OMS · Matching Engine · Account │
│  Market Data Service             │
└──────────────┬───────────────────┘
               │ Async Data Pump
┌──────────────▼───────────────────┐
│  Storage (Event Log · Snapshot)  │
└──────────────────────────────────┘
```

### Modules

| Module | Directory | Responsibility |
|--------|-----------|---------------|
| Event Model | `src/events/` | Canonical event definitions (OrderPlaced, TradeExecuted, etc.) |
| OMS | `src/oms/` | Order lifecycle state machine, fund validation |
| Matching Engine | `src/matching_engine/` | Price-time priority order book, Engine A/B matching |
| Account | `src/account/` | Cash/position management, event projection, settlement |
| Market Data | `src/market_data/` | TWSE adapter, simulated adapter, quote polling |
| Storage | `src/storage/` | Event writer/loader, snapshots, Excel/Sheets export |
| API | `src/api/` | REST endpoints, WebSocket pub/sub |
| Frontend | `src/frontend/` | Single-page application |

---

## Matching Engines

### Engine A — Order vs Market (大盤買賣價 vs 委託)

Orders match against real-time market bid/ask prices:

| Rule | Condition | Trade Price |
|------|-----------|-------------|
| BUY fill | Order price ≥ Market ask price | Market ask price |
| SELL fill | Order price ≤ Market bid price | Market bid price |

When market data updates, all resting orders are re-evaluated for potential fills.

### Engine B — Order vs Order (委託 vs 委託)

Classic order book matching with price-time priority:

- **Price priority** — Better price gets filled first
- **Time priority** — Same price → FIFO
- **Trade price** — Maker (resting order) price
- **Partial fills** — Allowed; remainder stays on book

Switch between engines via **Settings → System Settings** in the UI.

---

## API Endpoints

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/login` | Login, returns JWT |
| POST | `/api/v1/auth/register` | Register new user |
| PUT | `/api/v1/auth/password` | Change password |

### Trading
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/orders` | Place order (LIMIT/MARKET, BUY/SELL) |
| GET | `/api/v1/orders` | List user's orders |
| DELETE | `/api/v1/orders/{id}` | Cancel order |
| GET | `/api/v1/portfolio` | Portfolio summary |
| GET | `/api/v1/trades` | Trade history |
| GET | `/api/v1/cashflow` | Cash flow records |

### Market Data
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/market` | All stock quotes |
| GET | `/api/v1/market/{symbol}` | Single stock quote |
| GET | `/api/v1/market/{symbol}/history` | K-line history |
| GET | `/api/v1/orderbook/{symbol}` | Order book snapshot |

### Settings
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/settings/engine-mode` | Get current engine mode |
| PUT | `/api/v1/settings/engine-mode` | Switch engine mode (A/B) |

### Admin
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/admin/system` | System status |
| GET | `/api/v1/admin/metrics` | Performance metrics |
| POST | `/api/v1/admin/users` | Create user |
| DELETE | `/api/v1/admin/users/{id}` | Delete user |

---

## Project Structure

```
hifreq-tradingame/
├── pyproject.toml
├── readme.md
├── docs/
│   └── design-spec.md          # Full design specification (v1.1)
├── data/
│   ├── users.json              # User store
│   └── events/                 # Append-only event log (JSONL)
├── src/
│   ├── app.py                  # Bootstrap & wiring
│   ├── common/                 # Shared types, errors, clock
│   ├── events/                 # Event canonical models
│   ├── oms/                    # Order management system
│   ├── matching_engine/        # Order book & matching logic
│   ├── account/                # Account, positions, settlement
│   ├── market_data/            # TWSE & simulated adapters
│   ├── storage/                # Event persistence & export
│   ├── auth/                   # JWT auth & user model
│   ├── api/                    # REST & WebSocket endpoints
│   └── frontend/static/        # HTML, CSS, JS
└── tests/
    ├── unit/                   # Unit tests
    ├── integration/            # Order-to-trade flow tests
    ├── replay/                 # Deterministic replay tests
    └── e2e/                    # Playwright E2E tests
```

---

## Order Lifecycle

```
PENDING → ACCEPTED → ROUTED → PARTIALLY_FILLED → FILLED
                         │                           ↑
                         └───────────────────────────┘
                         └→ CANCELED
         └→ REJECTED (insufficient funds/position)
```

---

## License

MIT
