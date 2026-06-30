# Trading Dashboard

A local SPY candlestick dashboard powered by FastAPI, TypeScript, and Alpaca market data.

The backend pulls Alpaca stock bars from the `iex` feed, caches them in SQLite, and exposes a clean `/api/candles` endpoint. The frontend renders a timeline candlestick chart inside a compact shell card inspired by the provided screenshot.

## Stack

- Backend: Python, FastAPI, httpx
- Frontend: TypeScript, Vite, Canvas
- Database: SQLite with WAL mode for local durability and a schema that can migrate cleanly to Postgres or TimescaleDB later

## Setup

Create `backend/.env` from `backend/.env.example`:

```bash
APCA_API_KEY_ID=your_key
APCA_API_SECRET_KEY=your_secret
ALPACA_DATA_BASE_URL=https://data.alpaca.markets/v2
ALPACA_TRADING_BASE_URL=https://paper-api.alpaca.markets/v2
DATABASE_URL=sqlite:///./data/trading.db
```

Install and run the backend:

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Install and run the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL, normally [http://localhost:5173](http://localhost:5173).

## Older Bars

The chart has `Start` and `End` datetime controls in the top toolbar. Pick a window and press `Load` to request older Alpaca bars. Press `>|` to return to the latest bars.

You can also call the API directly:

```bash
http://127.0.0.1:8000/api/candles?symbol=SPY&feed=iex&timeframe=1Min&limit=500&start=2026-06-17T13:30:00Z&end=2026-06-17T20:00:00Z
```

The frontend auto-refresh selector defaults to `10s` and supports `5s`, `10s`, `15s`, or `off`.

You can drag the chart area horizontally. Drag right to move into older candles and drag left to return toward the latest candles. When the chart reaches the oldest loaded candle, it requests the next older batch from Alpaca and prepends it to the timeline.

If Alpaca credentials are missing, the API returns a clearly marked demo candle set so the chart remains usable while you configure keys. If SQLite file I/O is blocked by a local sandbox or sync-folder lock, the backend falls back to an in-memory SQLite cache so the dashboard can still run.
