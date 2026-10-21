"""Data access for the MCP server.

Read-only DuckDB over local parquet. The server never holds a connection string
and never writes to the warehouse; the only mutation is an append-only task log
written to a separate file.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

TABLES = ("members", "orders", "order_items", "products", "campaigns", "campaign_assignments")

SENSITIVE = ("phone", "id_card")


def mask(column: str, value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    if column == "phone":
        digits = "".join(c for c in text if c.isdigit())
        return f"{digits[:3]}****{digits[-4:]}" if len(digits) >= 7 else "***"
    if column == "id_card":
        return f"{text[:4]}{'*' * max(4, len(text) - 8)}{text[-4:]}"
    return value


class Store:
    def __init__(self, data_dir: str | Path, task_log: str | Path) -> None:
        self.data_dir = Path(data_dir)
        missing = [t for t in TABLES if not (self.data_dir / f"{t}.parquet").exists()]
        if missing:
            raise FileNotFoundError(f"missing fixtures: {missing}")
        self._con = duckdb.connect(database=":memory:", config={"memory_limit": "512MB"})
        for t in TABLES:
            path = (self.data_dir / f"{t}.parquet").as_posix()
            self._con.execute(f"CREATE OR REPLACE VIEW {t} AS SELECT * FROM read_parquet('{path}')")
        self.task_log = Path(task_log)
        self.task_log.parent.mkdir(parents=True, exist_ok=True)

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict]:
        cur = self._con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchmany(500)]

    def member_profile(self, member_id: str) -> dict | None:
        rows = self.query(
            """
            SELECT m.member_id, m.member_level, m.city_tier, m.age_band, m.register_channel,
                   m.phone, m.id_card,
                   COUNT(o.order_id) AS orders_90d,
                   COALESCE(SUM(o.paid_amount), 0) AS gmv_90d
            FROM members m
            LEFT JOIN orders o ON o.member_id = m.member_id AND o.status <> 'refunded'
            WHERE m.member_id = ?
            GROUP BY 1, 2, 3, 4, 5, 6, 7
            """,
            [member_id],
        )
        if not rows:
            return None
        row = rows[0]
        for col in SENSITIVE:
            row[col] = mask(col, row[col])
        row["masked_columns"] = list(SENSITIVE)
        return row

    def segment_summary(self, dimension: str, month: str) -> list[dict]:
        allowed = {"member_level", "city_tier", "age_band", "register_channel"}
        if dimension not in allowed:
            raise ValueError(f"不支持的维度 {dimension!r}，可选 {sorted(allowed)}")
        return self.query(
            f"""
            SELECT m.{dimension} AS segment,
                   COUNT(DISTINCT m.member_id) AS members,
                   COUNT(o.order_id) AS orders,
                   ROUND(COALESCE(SUM(o.paid_amount), 0), 2) AS gmv
            FROM members m
            LEFT JOIN orders o ON o.member_id = m.member_id
              AND o.status <> 'refunded'
              AND STRFTIME(CAST(o.order_time AS DATE), '%Y-%m') = ?
            GROUP BY 1 ORDER BY 4 DESC
            """,
            [month],
        )

    def campaigns(self) -> list[dict]:
        return self.query(
            "SELECT campaign_id, name, start_date, end_date, channel, coupon_value FROM campaigns ORDER BY start_date"
        )

    def append_task(self, payload: dict) -> dict:
        record = {"created_at": datetime.now().astimezone().isoformat(timespec="seconds"), **payload}
        with self.task_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def close(self) -> None:
        self._con.close()
