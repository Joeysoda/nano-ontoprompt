"""关系型数据库 Connector — MySQL / PostgreSQL"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import create_engine, inspect, text

from app.services.connection.base import ConnectorBase

# 合法 SQL 标识符：字母、数字、下划线、点（schema.table）；禁止空格/分号/引号/注释等
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _validate_identifier(name: str, field: str = "resource") -> str:
    """校验表名/列名等 SQL 标识符，防止 SQL 注入。"""
    if not name or not _IDENT_RE.match(name):
        raise ValueError(f"Invalid {field}: {name!r}")
    return name


class SQLConnector(ConnectorBase):
    """
    基于 SQLAlchemy 的关系型数据库 Connector。
    config 示例:
      {"connection_string": "postgresql://user:pass@host:5432/db"}

    SQL text is deliberately never accepted from a browser.  The API supplies
    a validated table name, optional validated columns, and structured filters.
    """

    def __init__(self, config: dict):
        self._config = config
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            self._engine = create_engine(
                self._config["connection_string"],
                pool_pre_ping=True,
                connect_args={"connect_timeout": 10},
            )
        return self._engine

    def test_connection(self) -> bool:
        try:
            with self._get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def list_resources(self) -> list[str]:
        """返回数据库中的表列表"""
        inspector = inspect(self._get_engine())
        return inspector.get_table_names()

    def describe_resource(self, resource: str) -> list[dict[str, str]]:
        _validate_identifier(resource)
        schema, _, table = resource.rpartition(".")
        inspector = inspect(self._get_engine())
        return [{"name": str(item["name"]), "type": str(item.get("type") or "unknown"), "nullable": bool(item.get("nullable", True))} for item in inspector.get_columns(table, schema=schema or None)]

    def query_rows(
        self,
        resource: str,
        *,
        columns: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        _validate_identifier(resource)
        selected = columns or ["*"]
        if selected != ["*"]:
            selected = [_validate_identifier(str(column), field="column") for column in selected]
        clauses: list[str] = []
        params: dict[str, Any] = {}
        supported = {"eq": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        for index, item in enumerate(filters or []):
            column = _validate_identifier(str(item.get("column") or ""), field="filter column")
            op = str(item.get("op") or "eq").lower()
            if op not in supported:
                raise ValueError(f"Unsupported filter operator: {op}")
            param = f"filter_{index}"
            clauses.append(f"{column} {supported[op]} :{param}")
            params[param] = item.get("value")
        statement = f"SELECT {', '.join(selected)} FROM {resource}"
        if clauses:
            statement += " WHERE " + " AND ".join(clauses)
        if limit is not None:
            statement += " LIMIT :limit"
            params["limit"] = max(1, min(int(limit), 1_000_000))
        with self._get_engine().connect() as conn:
            result = conn.execute(text(statement), params)
            columns_out = list(result.keys())
            return [dict(zip(columns_out, row)) for row in result]

    def pull_sample(self, resource: str, limit: int = 100) -> list[dict]:
        """从表中查询样本数据"""
        return self.query_rows(resource, limit=limit)

    def estimate_rows(self, resource: str, *, filters: list[dict[str, Any]] | None = None) -> int:
        """Return a bounded, server-side row estimate for the resource.

        This deliberately accepts the same validated structured filters as
        ``query_rows`` and never accepts raw SQL from the browser.
        """
        _validate_identifier(resource)
        clauses: list[str] = []
        params: dict[str, Any] = {}
        supported = {"eq": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        for index, item in enumerate(filters or []):
            column = _validate_identifier(str(item.get("column") or ""), field="filter column")
            op = str(item.get("op") or "eq").lower()
            if op not in supported:
                raise ValueError(f"Unsupported filter operator: {op}")
            param = f"filter_{index}"
            clauses.append(f"{column} {supported[op]} :{param}")
            params[param] = item.get("value")
        statement = f"SELECT COUNT(*) FROM {resource}"
        if clauses:
            statement += " WHERE " + " AND ".join(clauses)
        with self._get_engine().connect() as conn:
            value = conn.execute(text(statement), params).scalar()
        return int(value or 0)

    def pull_full(self, resource: str) -> list[dict]:
        """查询表全量数据"""
        return self.query_rows(resource)

    def pull_delta(self, resource: str, since: str | None = None) -> list[dict]:
        """增量数据查询 (基于 watermark_column)"""
        _validate_identifier(resource)
        watermark_col = self._config.get("watermark_column")
        if not watermark_col or not since:
            return self.pull_full(resource)

        _validate_identifier(watermark_col, field="watermark_column")
        return self.query_rows(resource, filters=[{"column": watermark_col, "op": "gt", "value": since}])
