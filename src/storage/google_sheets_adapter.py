# src/storage/google_sheets_adapter.py
"""
Google Sheets Adapter – DSD §1.6 Constraint 5.

Syncs per-user funding_records and trading_records to Google Sheets.

Architecture:
  • Adapter pattern – swappable between real gspread and a NullAdapter.
  • NullAdapter is used when Google credentials are not configured.
  • All operations are non-blocking and failure-tolerant
    (Google Sheets is a secondary copy; Local Excel is primary).
  • No SQL/NoSQL – file-based only (DSD §1.6 #10).

Credential setup (for production):
  1. Create a Google Cloud service account
  2. Download the JSON key file
  3. Set env var GOOGLE_SHEETS_CREDENTIALS=path/to/key.json
  4. Share the target spreadsheet with the service account email
"""
from __future__ import annotations

import logging
import os
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class GoogleSheetsPort(Protocol):
    """Port for Google Sheets operations (Adapter pattern)."""

    def sync_funding(self, user_id: str, transactions: list[dict[str, Any]]) -> bool:
        """Sync cash flow records to Google Sheets."""
        ...

    def sync_trading(self, user_id: str, trades: list[dict[str, Any]]) -> bool:
        """Sync trade records to Google Sheets."""
        ...

    def sync_batch(self, jobs: list[dict[str, Any]]) -> int:
        """Sync a batch of queued jobs. Returns number of successful syncs."""
        ...


class NullGoogleSheetsAdapter:
    """
    Null-object adapter: logs but does not connect to Google Sheets.
    Used when credentials are not configured.
    """

    def sync_funding(self, user_id: str, transactions: list[dict[str, Any]]) -> bool:
        logger.debug(
            "GoogleSheets(null): would sync %d funding records for %s",
            len(transactions), user_id,
        )
        return True

    def sync_trading(self, user_id: str, trades: list[dict[str, Any]]) -> bool:
        logger.debug(
            "GoogleSheets(null): would sync %d trading records for %s",
            len(trades), user_id,
        )
        return True

    def sync_batch(self, jobs: list[dict[str, Any]]) -> int:
        for job in jobs:
            if job["type"] == "funding":
                self.sync_funding(job["user_id"], job["data"])
            elif job["type"] == "trading":
                self.sync_trading(job["user_id"], job["data"])
        return len(jobs)


class GSpreadGoogleSheetsAdapter:
    """
    Real Google Sheets adapter using gspread library.

    Requires:
      - pip install gspread
      - GOOGLE_SHEETS_CREDENTIALS env var pointing to service account JSON
      - GOOGLE_SHEETS_SPREADSHEET_ID env var (optional, uses default name otherwise)

    Each user gets two worksheets:
      - "{user_id}_funding" for cash flow records
      - "{user_id}_trading" for trade records
    """

    def __init__(self) -> None:
        self._client = None
        self._spreadsheet = None
        self._initialized = False

    def _ensure_init(self) -> bool:
        """Lazy initialization to avoid startup failures."""
        if self._initialized:
            return self._client is not None

        self._initialized = True
        creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
        if not creds_path:
            logger.warning(
                "GOOGLE_SHEETS_CREDENTIALS not set – Google Sheets sync disabled"
            )
            return False

        try:
            import gspread

            self._client = gspread.service_account(filename=creds_path)
            sheet_id = os.environ.get(
                "GOOGLE_SHEETS_SPREADSHEET_ID", "TradingSim_Records"
            )
            try:
                self._spreadsheet = self._client.open(sheet_id)
            except gspread.SpreadsheetNotFound:
                self._spreadsheet = self._client.create(sheet_id)
                logger.info("Created Google Spreadsheet: %s", sheet_id)

            logger.info("Google Sheets adapter initialized: %s", sheet_id)
            return True

        except ImportError:
            logger.warning("gspread not installed – Google Sheets sync disabled")
            return False
        except Exception as exc:
            logger.error("Google Sheets init failed: %s", exc)
            return False

    def _get_or_create_worksheet(self, title: str, headers: list[str]):
        """Get or create a worksheet by title."""
        try:
            ws = self._spreadsheet.worksheet(title)
        except Exception:
            ws = self._spreadsheet.add_worksheet(
                title=title, rows=1000, cols=len(headers)
            )
            ws.append_row(headers)
        return ws

    def sync_funding(self, user_id: str, transactions: list[dict[str, Any]]) -> bool:
        if not self._ensure_init():
            return False

        try:
            ws_title = f"{user_id}_funding"
            headers = ["timestamp", "tx_type", "amount", "description"]
            ws = self._get_or_create_worksheet(ws_title, headers)

            # Clear and re-write (simple approach for demo-scale data)
            ws.clear()
            ws.append_row(headers)
            for tx in transactions:
                ws.append_row([
                    str(tx.get("timestamp", "")),
                    str(tx.get("tx_type", "")),
                    str(tx.get("amount", "")),
                    str(tx.get("description", "")),
                ])
            logger.info("Synced %d funding records to GSheets for %s", len(transactions), user_id)
            return True
        except Exception as exc:
            logger.error("GSheets funding sync failed for %s: %s", user_id, exc)
            return False

    def sync_trading(self, user_id: str, trades: list[dict[str, Any]]) -> bool:
        if not self._ensure_init():
            return False

        try:
            ws_title = f"{user_id}_trading"
            headers = ["trade_id", "order_id", "symbol", "side", "qty", "price", "trade_date"]
            ws = self._get_or_create_worksheet(ws_title, headers)

            ws.clear()
            ws.append_row(headers)
            for t in trades:
                ws.append_row([
                    str(t.get("trade_id", "")),
                    str(t.get("order_id", "")),
                    str(t.get("symbol", "")),
                    str(t.get("side", "")),
                    str(t.get("qty", "")),
                    str(t.get("price", "")),
                    str(t.get("trade_date", "")),
                ])
            logger.info("Synced %d trading records to GSheets for %s", len(trades), user_id)
            return True
        except Exception as exc:
            logger.error("GSheets trading sync failed for %s: %s", user_id, exc)
            return False

    def sync_batch(self, jobs: list[dict[str, Any]]) -> int:
        count = 0
        for job in jobs:
            ok = False
            if job["type"] == "funding":
                ok = self.sync_funding(job["user_id"], job["data"])
            elif job["type"] == "trading":
                ok = self.sync_trading(job["user_id"], job["data"])
            if ok:
                count += 1
        return count


def create_google_sheets_adapter() -> GoogleSheetsPort:
    """
    Factory: returns real adapter if credentials are configured,
    otherwise returns NullAdapter.
    """
    creds = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if creds and os.path.exists(creds):
        return GSpreadGoogleSheetsAdapter()
    return NullGoogleSheetsAdapter()
