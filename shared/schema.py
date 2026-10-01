"""Describe the database schema (tables, columns, types, keys, sample values) for prompts and docs.

Table and column descriptions are read from the `_schema_docs` table, which the database
build script fills in. Tables whose names start with "_" are internal and are not described.

Databases can come from users, so reading the schema has a time limit and the prompt text has
an optional size limit. All limits are defined below.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from shared.db import QueryError, QueryTimeoutError, connect_read_only

DOCS_TABLE = "_schema_docs"
_MAX_SAMPLE_CHARS = 40

# --- Limits --------------------------------------------------------------------------------
# Maximum time to read the schema (row counts and sample values included).
DESCRIBE_TIMEOUT_S = 10.0
# Size budget for the schema text in the agent prompt (about 3,000 tokens; Olist uses ~5,600).
SCHEMA_MAX_CHARS = 12_000
# Columns kept per table when the schema text is too long, tried in this order.
SCHEMA_COLUMN_CAPS = (30, 20, 10)
# Names listed in the "... more tables not shown" line.
SCHEMA_OMITTED_TABLE_NAMES = 20

_PROGRESS_STEPS = 10_000  # SQLite VM steps between time-limit checks


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    is_pk: bool
    fk_ref: str | None = None  # "table.column"
    description: str | None = None
    sample_values: list[object] = field(default_factory=list)


@dataclass
class TableInfo:
    name: str
    row_count: int
    columns: list[ColumnInfo]
    description: str | None = None

    def column(self, name: str) -> ColumnInfo:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(name)


@dataclass
class SchemaInfo:
    tables: list[TableInfo]

    def table(self, name: str) -> TableInfo:
        for table in self.tables:
            if table.name == name:
                return table
        raise KeyError(name)

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _short(value: object) -> object:
    if isinstance(value, str) and len(value) > _MAX_SAMPLE_CHARS:
        return value[: _MAX_SAMPLE_CHARS - 3] + "..."
    return value


def describe_schema(
    db_path: str | Path | None = None,
    *,
    sample_values: int = 3,
    tables: list[str] | None = None,
    timeout_s: float = DESCRIBE_TIMEOUT_S,
) -> SchemaInfo:
    """Inspect the database and return its tables, columns, keys, descriptions and samples.

    Args:
        db_path: database file; defaults to the configured DB_PATH.
        sample_values: number of distinct non-null sample values per column (0 to skip).
        tables: restrict to these table names; defaults to all non-internal tables.
        timeout_s: time limit for the whole inspection.

    Raises FileNotFoundError if the file is missing, QueryTimeoutError if the time limit is
    exceeded, and QueryError if the file is not a readable SQLite database.
    """
    conn = connect_read_only(db_path)
    deadline = time.monotonic() + timeout_s
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), _PROGRESS_STEPS)
    try:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '\\_%' ESCAPE '\\' "
                "ORDER BY name"
            )
        ]
        if tables is not None:
            unknown = set(tables) - set(names)
            if unknown:
                raise KeyError(f"Unknown tables: {sorted(unknown)}")
            names = [n for n in names if n in tables]

        docs: dict[tuple[str, str], str] = {}
        has_docs = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (DOCS_TABLE,)
        ).fetchone()
        if has_docs:
            for table_name, column_name, description in conn.execute(
                f"SELECT table_name, column_name, description FROM {DOCS_TABLE}"
            ):
                docs[(table_name, column_name or "")] = description

        result = []
        for name in names:
            q = _quote(name)
            fks = {
                row[3]: f"{row[2]}.{row[4]}"
                for row in conn.execute(f"PRAGMA foreign_key_list({q})")
            }
            columns = []
            for _cid, col, col_type, notnull, _default, pk in conn.execute(
                f"PRAGMA table_info({q})"
            ):
                samples: list[object] = []
                if sample_values > 0:
                    samples = [
                        _short(row[0])
                        for row in conn.execute(
                            f"SELECT DISTINCT {_quote(col)} FROM {q} "
                            f"WHERE {_quote(col)} IS NOT NULL LIMIT ?",
                            (sample_values,),
                        )
                    ]
                columns.append(
                    ColumnInfo(
                        name=col,
                        type=col_type or "",
                        nullable=not notnull and not pk,
                        is_pk=bool(pk),
                        fk_ref=fks.get(col),
                        description=docs.get((name, col)),
                        sample_values=samples,
                    )
                )
            row_count = conn.execute(f"SELECT COUNT(*) FROM {q}").fetchone()[0]
            result.append(
                TableInfo(
                    name=name,
                    row_count=row_count,
                    columns=columns,
                    description=docs.get((name, "")),
                )
            )
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            raise QueryTimeoutError(
                f"Reading the database structure took longer than {timeout_s:g}s. "
                "The database may be too large to use here."
            ) from exc
        raise QueryError(f"Could not read the database: {exc}.") from exc
    except sqlite3.DatabaseError as exc:  # e.g. "file is not a database"
        raise QueryError(f"Could not read the database: {exc}.") from exc
    finally:
        conn.close()
    return SchemaInfo(tables=result)


def schema_to_prompt(
    schema: SchemaInfo, *, include_samples: bool = True, max_chars: int | None = None
) -> str:
    """Render the schema as compact text for an LLM prompt.

    If max_chars is set and the full text is longer, the text is shortened step by step until
    it fits: leave out sample values, then descriptions, then keep only the key columns plus
    the first columns of each table (SCHEMA_COLUMN_CAPS), then leave out tables at the end,
    and as a last resort cut the text. A line always says what was left out.
    """
    full = _render(schema, include_samples=include_samples)
    if max_chars is None or len(full) <= max_chars:
        return full

    attempts = [
        {"descriptions": True},
        {"descriptions": False},
        *({"descriptions": False, "max_columns": cap} for cap in SCHEMA_COLUMN_CAPS),
    ]
    for options in attempts:
        text = _render(schema, include_samples=False, **options)
        if len(text) <= max_chars:
            return text

    fewest = {
        "include_samples": False,
        "descriptions": False,
        "max_columns": SCHEMA_COLUMN_CAPS[-1],
    }
    for max_tables in range(len(schema.tables) - 1, 0, -1):
        text = _render(schema, max_tables=max_tables, **fewest)
        if len(text) <= max_chars:
            return text
    return _cut(_render(schema, max_tables=1, **fewest), max_chars)


def _render(
    schema: SchemaInfo,
    *,
    include_samples: bool,
    descriptions: bool = True,
    max_columns: int | None = None,
    max_tables: int | None = None,
) -> str:
    lines: list[str] = []
    shown = schema.tables if max_tables is None else schema.tables[:max_tables]
    for table in shown:
        header = f"TABLE {table.name} ({table.row_count} rows)"
        if descriptions and table.description:
            header += f": {table.description}"
        lines.append(header)
        columns = _kept_columns(table.columns, max_columns)
        for col in columns:
            parts = [f"  - {col.name} {col.type}".rstrip()]
            if col.is_pk:
                parts.append("PK")
            if col.fk_ref:
                parts.append(f"FK -> {col.fk_ref}")
            line = " ".join(parts)
            if descriptions and col.description:
                line += f" | {col.description}"
            # Sample values of id columns are random hashes and only cost tokens.
            is_id = col.is_pk or col.fk_ref or col.name.endswith("_id")
            if include_samples and col.sample_values and not is_id:
                line += " | samples: " + ", ".join(repr(v) for v in col.sample_values)
            lines.append(line)
        if len(columns) < len(table.columns):
            lines.append(f"  ... {len(table.columns) - len(columns)} more columns not shown")
        lines.append("")
    hidden = schema.tables[len(shown) :]
    if hidden:
        names = [t.name for t in hidden[:SCHEMA_OMITTED_TABLE_NAMES]]
        others = len(hidden) - len(names)
        listed = ", ".join(names) + (f", and {others} others" if others else "")
        lines.append(f"... {len(hidden)} more tables not shown: {listed}")
    return "\n".join(lines).rstrip() + "\n"


def _kept_columns(columns: list[ColumnInfo], max_columns: int | None) -> list[ColumnInfo]:
    """All key columns plus the first other columns, up to max_columns in total, in order."""
    if max_columns is None or len(columns) <= max_columns:
        return columns
    keys = {c.name for c in columns if c.is_pk or c.fk_ref}
    room = max(0, max_columns - len(keys))
    others = [c.name for c in columns if c.name not in keys][:room]
    keep = keys | set(others)
    return [c for c in columns if c.name in keep]


def _cut(text: str, max_chars: int) -> str:
    """Cut at a line boundary so the text plus a note fits in max_chars."""
    note = f"... schema text cut at {max_chars} characters\n"
    if len(note) >= max_chars:
        return text[:max_chars]
    head = text[: max_chars - len(note)]
    head = head[: head.rfind("\n") + 1] if "\n" in head else ""
    return head + note
