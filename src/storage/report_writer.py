# src/storage/report_writer.py
"""
Report Writer – periodic export of trading reports to disk.

Design rules (DSD §4.7):
  • Directory layout: data/reports/YYYY-MM/
  • Exports trades and orders as CSV files daily.
  • Runs on a background thread (non-blocking).
  • Uses CSVExporter for formatting consistency.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.storage.excel_writer import CSVExporter

logger = logging.getLogger(__name__)


class ReportWriter:
    """
    Periodically writes trade/order report CSV files to disk.

    Layout:
        data/reports/YYYY-MM/trades-YYYY-MM-DD.csv
        data/reports/YYYY-MM/orders-YYYY-MM-DD.csv
    """

    def __init__(
        self,
        base_dir: str | Path = "data/reports",
        interval_s: float = 3600.0,  # default: once per hour
        trades_provider: Callable[[], list[dict[str, Any]]] | None = None,
        orders_provider: Callable[[], list[dict[str, Any]]] | None = None,
    ):
        self._base_dir = Path(base_dir)
        self._interval_s = interval_s
        self._trades_provider = trades_provider
        self._orders_provider = orders_provider

        self._running = False
        self._thread: threading.Thread | None = None
        self._last_export: float = 0.0

    # ── Public API ───────────────────────────────────────────

    def export_now(self) -> dict[str, Path | None]:
        """
        Export trades and orders CSV to disk immediately.
        Returns dict with paths written (or None if no data / provider).
        """
        now = datetime.now(timezone.utc)
        month_dir = now.strftime("%Y-%m")
        date_str = now.strftime("%Y-%m-%d")
        out_dir = self._base_dir / month_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        result: dict[str, Path | None] = {"trades": None, "orders": None}

        # Export trades
        if self._trades_provider:
            try:
                trades = self._trades_provider()
                if trades:
                    path = out_dir / f"trades-{date_str}.csv"
                    csv_text = CSVExporter.export_trades(trades)
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(csv_text)
                    result["trades"] = path
                    logger.info("Exported %d trades → %s", len(trades), path)
            except Exception as exc:
                logger.error("Failed to export trades: %s", exc)

        # Export orders
        if self._orders_provider:
            try:
                orders = self._orders_provider()
                if orders:
                    path = out_dir / f"orders-{date_str}.csv"
                    csv_text = CSVExporter.export_orders(orders)
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(csv_text)
                    result["orders"] = path
                    logger.info("Exported %d orders → %s", len(orders), path)
            except Exception as exc:
                logger.error("Failed to export orders: %s", exc)

        self._last_export = time.monotonic()
        return result

    def start_background(self) -> None:
        """Start periodic background exports."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(
            "ReportWriter started (interval=%.0fs, dir=%s)",
            self._interval_s,
            self._base_dir,
        )

    def stop(self) -> None:
        """Stop background exports and do a final export."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        # Final export on shutdown
        self.export_now()

    # ── Internals ────────────────────────────────────────────

    def _run_loop(self) -> None:
        while self._running:
            time.sleep(self._interval_s)
            if self._running:
                self.export_now()
