"""Describe the database schema (tables, columns, types, keys, sample values) for prompts and docs.

Table and column descriptions are read from the `_schema_docs` table, which the database
build script fills in. Tables whose names start with "_" are internal and are not described.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from shared.db import connect_read_only

DOCS_TABLE = "_schema_docs"
_MAX_SAMPLE_CHARS = 40


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
) -> SchemaInfo:
    """Inspect the database and return its tables, columns, keys, descriptions and samples.

    Args:
        db_path: database file; defaults to the configured DB_PATH.
        sample_values: number of distinct non-null sample values per column (0 to skip).
        tables: restrict to these table names; defaults to all non-internal tables.
    """
    conn = connect_read_only(db_path)
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
    finally:
        conn.close()
    return SchemaInfo(tables=result)


def schema_to_prompt(schema: SchemaInfo, *, include_samples: bool = True) -> str:
    """Render the schema as compact text for an LLM prompt."""
    lines: list[str] = []
    for table in schema.tables:
        header = f"TABLE {table.name} ({table.row_count} rows)"
        if table.description:
            header += f": {table.description}"
        lines.append(header)
        for col in table.columns:
            parts = [f"  - {col.name} {col.type}".rstrip()]
            if col.is_pk:
                parts.append("PK")
            if col.fk_ref:
                parts.append(f"FK -> {col.fk_ref}")
            line = " ".join(parts)
            if col.description:
                line += f" | {col.description}"
            # Sample values of id columns are random hashes and only cost tokens.
            is_id = col.is_pk or col.fk_ref or col.name.endswith("_id")
            if include_samples and col.sample_values and not is_id:
                line += " | samples: " + ", ".join(repr(v) for v in col.sample_values)
            lines.append(line)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
