"""Build a read-only SQLite database from uploaded CSV files or one SQLite file.

Usage:
    up = build_user_db([path_or_file, ...], out_dir)
    agent = SQLAgent(db_path=up.db_path, profile=up.profile)

A SQLite upload is checked (valid file, not damaged, no views, triggers or virtual tables) and
copied; its table and column names are cleaned with the same rules as CSV headers. Each CSV
becomes one table. Names are cleaned to safe identifiers, types are inferred strictly
(a column is a number or a date only if every non-empty value is one), dates are stored as ISO
text, and every change is reported in `notes` so the user can see what happened to the file.
The database is built in a temporary file and only kept if every step succeeds.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import sqlite3
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO

from shared.db import QueryError, connect_read_only
from shared.models import DatasetProfile
from shared.schema import SchemaInfo, describe_schema

# --- Limits --------------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # all files together, checked before reading them
MAX_FILES = 10
MAX_ROWS = 1_000_000  # per file
MAX_COLUMNS = 200  # per file (CSV) or per table (SQLite)
MAX_TABLES = 100  # per SQLite file

CSV_SUFFIXES = frozenset({".csv"})
SQLITE_SUFFIXES = frozenset({".sqlite", ".sqlite3", ".db"})
_SQLITE_HEADER = b"SQLite format 3\x00"
_MAX_LISTED_TABLES = 10

_DELIMITERS = ",;\t|"
_NULL_TOKENS = {"", "na", "n/a", "null", "none", "nan"}
_MAX_LISTED_RENAMES = 10

# SQLite keywords; identifiers that match one get a "_" suffix so generated SQL never has to
# quote them (e.g. a column called "order").
SQL_KEYWORDS = frozenset(
    """
    abort action add after all alter always analyze and as asc attach autoincrement before
    begin between by cascade case cast check collate column commit conflict constraint create
    cross current current_date current_time current_timestamp database default deferrable
    deferred delete desc detach distinct do drop each else end escape except exclude exclusive
    exists explain fail filter first following for foreign from full generated glob group groups
    having if ignore immediate in index indexed initially inner insert instead intersect into is
    isnull join key last left like limit match materialized natural no not nothing notnull null
    nulls of offset on or order others outer over partition plan pragma preceding primary query
    raise range recursive references regexp reindex release rename replace restrict returning
    right rollback row rows savepoint select set table temp temporary then ties to transaction
    trigger unbounded union unique update using vacuum values view virtual when where window
    with without
    """.split()
)

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_THOUSANDS_COMMA_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")  # 1,234,567.89
_DECIMAL_COMMA_RE = re.compile(r"^[+-]?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$")  # 1.234,56 or 12,5
_LEADING_ZERO_RE = re.compile(r"^[+-]?0\d")
_DATE_RE = re.compile(
    r"^(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})"
    r"(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?$"
)

Source = str | Path | IO[bytes]


class UploadError(ValueError):
    """The upload cannot be used. The message is written to be shown to the user."""


@dataclass
class UploadedDatabase:
    """Result of build_user_db."""

    db_path: Path  # the new SQLite file; open it read-only (SQLAgent and run_query do)
    profile: DatasetProfile  # pass to SQLAgent(profile=...)
    tables: dict[str, tuple[int, int]]  # table name -> (rows, columns)
    notes: list[str] = field(default_factory=list)  # what was changed or assumed, for the user


@dataclass
class _Column:
    name: str
    sql_type: str  # INTEGER, REAL or TEXT
    values: list[object]
    is_date: bool = False


# --- public entry point --------------------------------------------------------------------


def build_user_db(
    sources: Sequence[Source],
    out_dir: str | Path,
    *,
    dayfirst: bool | None = None,
) -> UploadedDatabase:
    """Build a new SQLite database in out_dir from CSV files (one table per file) or from one
    SQLite file.

    Args:
        sources: file paths, or file-like objects with a `.name` (e.g. uploaded files).
        out_dir: folder for the database; it gets a new random file name.
        dayfirst: how to read dates such as 03/04/2023 when every value is ambiguous;
            None (default) reads them as day/month. Dates with a day above 12 are always
            read the way the data shows. Either way a note says which order was used.

    Raises UploadError with a message for the user if a file or limit is not acceptable.
    """
    if not sources:
        raise UploadError("No files were uploaded.")
    if len(sources) > MAX_FILES:
        raise UploadError(f"Too many files: {len(sources)} (the limit is {MAX_FILES}).")
    names = [_source_name(s) for s in sources]
    suffixes = [Path(name).suffix.lower() for name in names]
    for name, suffix in zip(names, suffixes, strict=True):
        if suffix not in CSV_SUFFIXES | SQLITE_SUFFIXES:
            raise UploadError(
                f"{name} is not a CSV or SQLite file. "
                "Only .csv, .sqlite, .sqlite3 and .db files are supported."
            )
    is_sqlite = [suffix in SQLITE_SUFFIXES for suffix in suffixes]
    if any(is_sqlite) and len(sources) > 1:
        raise UploadError("Upload one SQLite file on its own, without other files.")
    total = sum(_source_size(s) for s in sources)
    if total > MAX_UPLOAD_BYTES:
        raise UploadError(
            f"The upload is {total / 1024 / 1024:.1f} MB; "
            f"the limit is {MAX_UPLOAD_BYTES / 1024 / 1024:g} MB."
        )

    if any(is_sqlite):
        return _build_from_sqlite(sources[0], names[0], Path(out_dir))

    notes: list[str] = []
    tables: dict[str, list[_Column]] = {}
    for source, name in zip(sources, names, strict=True):
        table = _unique(clean_identifier(Path(name).stem, "table", prefix="t_"), set(tables))
        if table != Path(name).stem:
            notes.append(f"{name} is stored as table {table}.")
        tables[table] = _read_csv(source, name, dayfirst, notes)

    final, tmp = _new_paths(Path(out_dir))
    try:
        _write(tmp, tables)
        describe_schema(tmp, sample_values=0)  # must open read-only within the schema limits
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)

    return UploadedDatabase(
        db_path=final,
        profile=_profile(tables),
        tables={t: (len(cols[0].values) if cols else 0, len(cols)) for t, cols in tables.items()},
        notes=notes,
    )


# --- names ---------------------------------------------------------------------------------


def clean_identifier(raw: str, fallback: str, *, prefix: str = "c_") -> str:
    """Make a safe snake_case SQL identifier, e.g. ' Total (€)' -> 'total', 'order' -> 'order_'.

    Accents are removed, other characters become "_", leading underscores are dropped (names
    starting with "_" are treated as internal and hidden), a leading digit gets `prefix`, and
    SQL keywords get a "_" suffix. An empty result becomes `fallback`.
    """
    text = unicodedata.normalize("NFKD", str(raw)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if not text:
        return fallback
    if text[0].isdigit():
        text = prefix + text
    if text in SQL_KEYWORDS:
        text += "_"
    return text


def _unique(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    n = 2
    while f"{name}_{n}" in taken:
        n += 1
    return f"{name}_{n}"


# --- reading -------------------------------------------------------------------------------


def _source_name(source: Source) -> str:
    if isinstance(source, (str, Path)):
        return Path(source).name
    name = getattr(source, "name", None)
    if not name:
        raise UploadError("Each uploaded file needs a name.")
    return Path(str(name)).name


def _source_size(source: Source) -> int:
    """Size in bytes without reading the content."""
    if isinstance(source, (str, Path)):
        return Path(source).stat().st_size
    size = getattr(source, "size", None)
    if isinstance(size, int):
        return size
    position = source.tell()
    source.seek(0, os.SEEK_END)
    size = source.tell()
    source.seek(position)
    return size


def _read_bytes(source: Source) -> bytes:
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    source.seek(0)
    return source.read()


def _decode(data: bytes, name: str, notes: list[str]) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    for encoding, label in (("cp1252", "Windows-1252"), ("latin-1", "Latin-1")):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        notes.append(f"{name} is not UTF-8; it was read as {label}.")
        return text
    raise AssertionError("latin-1 decodes any bytes")  # pragma: no cover


def _delimiter(text: str) -> str:
    sample = text[:65536]
    try:
        return csv.Sniffer().sniff(sample, delimiters=_DELIMITERS).delimiter
    except csv.Error:
        return ","


def _read_csv(source: Source, name: str, dayfirst: bool | None, notes: list[str]) -> list[_Column]:
    text = _decode(_read_bytes(source), name, notes)
    if not text.strip():
        raise UploadError(f"{name} is empty.")
    delimiter = _delimiter(text)
    try:
        rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if row]
    except csv.Error as exc:
        raise UploadError(f"{name} could not be read as CSV: {exc}.") from exc
    header, data = rows[0], rows[1:]
    if len(header) > MAX_COLUMNS:
        raise UploadError(f"{name} has {len(header)} columns; the limit is {MAX_COLUMNS}.")
    if len(data) > MAX_ROWS:
        raise UploadError(f"{name} has {len(data):,} rows; the limit is {MAX_ROWS:,}.")
    for line, row in enumerate(data, start=2):
        if len(row) > len(header):
            raise UploadError(
                f"{name} could not be read as CSV: row {line} has {len(row)} values "
                f"but the header has {len(header)}."
            )
    if delimiter != ",":
        shown = {"\t": "tab"}.get(delimiter, f"'{delimiter}'")
        notes.append(f"{name}: values are separated by {shown}.")
    if not data:
        notes.append(f"{name} has a header but no data rows.")

    columns: list[_Column] = []
    taken: set[str] = set()
    renames = []
    for i, raw in enumerate(header):
        col_name = _unique(clean_identifier(raw, f"column_{i + 1}"), taken)
        taken.add(col_name)
        if col_name != raw.strip().lower():  # case-only changes are not worth a note
            renames.append(f"'{raw}' -> {col_name}")
        raw_values = [row[i] if i < len(row) else "" for row in data]
        columns.append(
            _infer(
                col_name,
                raw_values,
                name,
                decimal_comma=delimiter == ";",
                dayfirst=dayfirst,
                notes=notes,
            )
        )
    if renames:
        listed = ", ".join(renames[:_MAX_LISTED_RENAMES])
        more = len(renames) - _MAX_LISTED_RENAMES
        notes.append(
            f"{name}: renamed columns {listed}" + (f" and {more} more." if more > 0 else ".")
        )
    return columns


# --- type inference ------------------------------------------------------------------------


def _infer(
    col: str,
    raw: list[str],
    name: str,
    *,
    decimal_comma: bool,
    dayfirst: bool | None,
    notes: list[str],
) -> _Column:
    """Pick INTEGER, REAL, a date (ISO text) or TEXT. decimal_comma prefers 1.234,5 style
    numbers, the usual convention in semicolon-separated files."""
    stripped = [v.strip() for v in raw]
    values: list[str | None] = [None if v.lower() in _NULL_TOKENS else v for v in stripped]
    present = [v for v in values if v is not None]
    if not present:
        if values:
            notes.append(f"{name}: column {col} is empty.")
        return _Column(col, "TEXT", [None] * len(values))

    numbers = _parse_numbers(present, decimal_comma)
    if numbers is not None:
        if any(_LEADING_ZERO_RE.match(v) for v in present):
            notes.append(f"{name}: column {col} is kept as text because of leading zeros.")
            return _Column(col, "TEXT", values)
        style, parsed = numbers
        if style == "decimal comma":
            notes.append(f"{name}: column {col} uses decimal commas; read as numbers.")
        if all(isinstance(n, int) for n in parsed.values()):
            return _Column(col, "INTEGER", [None if v is None else parsed[v] for v in values])
        return _Column(col, "REAL", [None if v is None else float(parsed[v]) for v in values])

    dates = _parse_dates(present, col, name, dayfirst, notes)
    if dates is not None:
        has_time = any(d.time() != datetime.min.time() for d in dates.values())
        fmt = "%Y-%m-%d %H:%M:%S" if has_time else "%Y-%m-%d"
        return _Column(
            col, "TEXT", [None if v is None else dates[v].strftime(fmt) for v in values], True
        )
    return _Column(col, "TEXT", values)


_INT64_MAX = 2**63 - 1


def _parse_numbers(
    present: list[str], decimal_comma: bool
) -> tuple[str, dict[str, int | float]] | None:
    """Parse every value as a number, trying the file's likely style first.

    Returns (style, {text: number}) or None if any value is not a number in either style.
    """
    styles = ("decimal comma", "point") if decimal_comma else ("point", "decimal comma")
    for style in styles:
        parsed = _try_numbers(present, style)
        if parsed is not None:
            return style, parsed
    return None


def _try_numbers(present: list[str], style: str) -> dict[str, int | float] | None:
    if style == "decimal comma" and not any("," in v for v in present):
        return None  # without a comma, the point style reads the column correctly
    out: dict[str, int | float] = {}
    for v in set(present):
        if style == "point":
            if _INT_RE.match(v) or _FLOAT_RE.match(v):
                text = v
            elif _THOUSANDS_COMMA_RE.match(v):
                text = v.replace(",", "")
            else:
                return None
        else:
            if not _DECIMAL_COMMA_RE.match(v):
                return None
            text = v.replace(".", "").replace(",", ".")
        if _INT_RE.match(text):
            number = int(text)
            if abs(number) > _INT64_MAX:
                return None  # too large for SQLite INTEGER: keep the column as text
            out[v] = number
        else:
            out[v] = float(text)
    return out


def _parse_dates(
    present: list[str], col: str, name: str, dayfirst: bool | None, notes: list[str]
) -> dict[str, datetime] | None:
    """Parse every value as a date, or return None if any value is not one."""
    matches = {}
    for v in set(present):
        m = _DATE_RE.match(v)
        if not m:
            return None
        matches[v] = m.groups()

    year_first = all(len(g[0]) == 4 for g in matches.values())
    order = "ymd"
    if not year_first:
        if any(len(g[0]) == 4 for g in matches.values()):
            return None  # mixed formats
        firsts = [int(g[0]) for g in matches.values()]
        seconds = [int(g[1]) for g in matches.values()]
        if any(f > 12 for f in firsts):
            order = "dmy"
            note = (
                f"{name}: dates in column {col} were read as day/month "
                "(some values have a day above 12 first)."
            )
        elif any(s > 12 for s in seconds):
            order = "mdy"
            note = (
                f"{name}: dates in column {col} were read as month/day "
                "(some values have a day above 12 second)."
            )
        else:
            order = "mdy" if dayfirst is False else "dmy"
            reading = "month/day" if order == "mdy" else "day/month"
            how = "as requested" if dayfirst is not None else "the default"
            note = f"{name}: dates in column {col} are ambiguous; read as {reading} ({how})."
    else:
        note = None

    parsed = {}
    for v, (a, b, c, hh, mm, ss) in matches.items():
        if order == "ymd":
            y, mo, d = a, b, c
        elif order == "dmy":
            d, mo, y = a, b, c
        else:
            mo, d, y = a, b, c
        year = int(y)
        if len(y) == 2:
            year += 2000 if year < 70 else 1900
        elif len(y) != 4:
            return None
        try:
            parsed[v] = datetime(year, int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
        except ValueError:
            return None
    if note:
        notes.append(note)
    return parsed


# --- writing -------------------------------------------------------------------------------


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _write(path: Path, tables: dict[str, list[_Column]]) -> None:
    conn = sqlite3.connect(path)
    try:
        for table, columns in tables.items():
            if not columns:
                continue
            column_sql = ", ".join(f"{_quote(c.name)} {c.sql_type}" for c in columns)
            conn.execute(f"CREATE TABLE {_quote(table)} ({column_sql})")
            placeholders = ", ".join("?" for _ in columns)
            rows = zip(*(c.values for c in columns), strict=True)
            conn.executemany(f"INSERT INTO {_quote(table)} VALUES ({placeholders})", rows)
        conn.commit()
    finally:
        conn.close()


def _profile(tables: dict[str, list[_Column]]) -> DatasetProfile:
    rules = []
    for table, columns in tables.items():
        for c in columns:
            dates = [v for v in c.values if v is not None]
            if c.is_date and dates:
                rules.append(f"Dates in {table}.{c.name} run from {min(dates)} to {max(dates)}.")
    return _upload_profile(list(tables), rules)


def _upload_profile(tables: list[str], rules: list[str]) -> DatasetProfile:
    names = ", ".join(tables[:_MAX_LISTED_TABLES])
    if len(tables) > _MAX_LISTED_TABLES:
        names += f" and {len(tables) - _MAX_LISTED_TABLES} more"
    return DatasetProfile(
        name=f"Uploaded data ({names})",
        description=f"data uploaded by the user (tables: {names})",
        rules=rules,
        include_samples=False,
    )


def _new_paths(out_dir: Path) -> tuple[Path, Path]:
    """A new random database path in out_dir and the temporary file it is built in."""
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{uuid.uuid4().hex}.db"
    return final, final.with_suffix(".db.tmp")


# --- SQLite uploads ------------------------------------------------------------------------


def _build_from_sqlite(source: Source, name: str, out_dir: Path) -> UploadedDatabase:
    final, tmp = _new_paths(out_dir)
    notes: list[str] = []
    try:
        _copy(source, tmp)
        with open(tmp, "rb") as f:
            if f.read(len(_SQLITE_HEADER)) != _SQLITE_HEADER:
                raise UploadError(f"{name} is not a SQLite database.")
        tables = _check_sqlite(tmp, name)
        _clean_sqlite_names(tmp, tables, name, notes)
        try:
            schema = describe_schema(tmp, sample_values=0)
        except QueryError as exc:
            raise UploadError(f"{name} could not be read: {exc}") from exc
        _check_sqlite_limits(schema, name)
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)
    return UploadedDatabase(
        db_path=final,
        profile=_upload_profile(schema.table_names, []),
        tables={t.name: (t.row_count, len(t.columns)) for t in schema.tables},
        notes=notes,
    )


def _copy(source: Source, target: Path) -> None:
    if isinstance(source, (str, Path)):
        shutil.copyfile(source, target)
        return
    source.seek(0)
    with open(target, "wb") as f:
        shutil.copyfileobj(source, f)


def _check_sqlite(path: Path, name: str) -> list[str]:
    """Read-only checks on the copied file; returns the user table names."""
    try:
        conn = connect_read_only(path)  # trusted_schema is off
        try:
            check = [row[0] for row in conn.execute("PRAGMA quick_check")]
            objects = conn.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise UploadError(f"{name} is damaged and cannot be used ({exc}).") from exc
    if check != ["ok"]:
        raise UploadError(f"{name} is damaged and cannot be used ({check[0]}).")

    unsupported = sorted(n for kind, n, _ in objects if kind in ("view", "trigger"))
    if unsupported:
        raise UploadError(
            f"{name} contains views or triggers ({', '.join(unsupported)}), which are not "
            "supported. Save a copy without them and upload that."
        )
    virtual = sorted(
        n
        for kind, n, sql in objects
        if kind == "table" and (sql or "").lstrip().upper().startswith("CREATE VIRTUAL TABLE")
    )
    if virtual:
        raise UploadError(
            f"{name} contains virtual tables ({', '.join(virtual)}), which are not supported."
        )
    tables = [n for kind, n, _ in objects if kind == "table" and not n.startswith("sqlite_")]
    if not tables:
        raise UploadError(f"{name} has no tables.")
    if len(tables) > MAX_TABLES:
        raise UploadError(f"{name} has {len(tables)} tables; the limit is {MAX_TABLES}.")
    return tables


def _clean_sqlite_names(path: Path, tables: list[str], name: str, notes: list[str]) -> None:
    """Rename tables and columns in our own copy with the CSV header rules.

    Safe because the file has no triggers or views and trusted_schema is off. Every name goes
    through a temporary name first, so swaps and case-only changes cannot collide.
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA trusted_schema = OFF")
        conn.execute("PRAGMA journal_mode = DELETE")  # one self-contained file, even after WAL
        taken: set[str] = set()
        plan = []
        for table in tables:
            new = _unique(clean_identifier(table, "table", prefix="t_"), taken)
            taken.add(new)
            plan.append((table, new))
        table_renames = [(old, new) for old, new in plan if old != new]
        for i, (old, _new) in enumerate(table_renames):
            conn.execute(f"ALTER TABLE {_quote(old)} RENAME TO {_quote(f'tmp_rename_{i}')}")
        for i, (_old, new) in enumerate(table_renames):
            conn.execute(f"ALTER TABLE {_quote(f'tmp_rename_{i}')} RENAME TO {_quote(new)}")
        listed = [f"{old} -> {new}" for old, new in table_renames if new != old.lower()]
        if listed:
            notes.append(f"{name}: renamed tables {_listing(listed)}")

        for _old, table in plan:
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({_quote(table)})")]
            col_taken: set[str] = set()
            col_plan = []
            for i, col in enumerate(columns):
                new = _unique(clean_identifier(col, f"column_{i + 1}"), col_taken)
                col_taken.add(new)
                if new != col:
                    col_plan.append((col, new))
            q = _quote(table)
            for i, (old, _new) in enumerate(col_plan):
                tmp_name = _quote(f"tmp_rename_{i}")
                conn.execute(f"ALTER TABLE {q} RENAME COLUMN {_quote(old)} TO {tmp_name}")
            for i, (_old, new) in enumerate(col_plan):
                tmp_name = _quote(f"tmp_rename_{i}")
                conn.execute(f"ALTER TABLE {q} RENAME COLUMN {tmp_name} TO {_quote(new)}")
            listed = [f"'{old}' -> {new}" for old, new in col_plan if new != old.strip().lower()]
            if listed:
                notes.append(f"{name}: table {table}: renamed columns {_listing(listed)}")
        conn.commit()
    except sqlite3.Error as exc:
        raise UploadError(f"{name} could not be prepared: {exc}.") from exc
    finally:
        conn.close()


def _check_sqlite_limits(schema: SchemaInfo, name: str) -> None:
    for table in schema.tables:
        if len(table.columns) > MAX_COLUMNS:
            raise UploadError(
                f"{name}: table {table.name} has {len(table.columns)} columns; "
                f"the limit is {MAX_COLUMNS}."
            )
        if table.row_count > MAX_ROWS:
            raise UploadError(
                f"{name}: table {table.name} has {table.row_count:,} rows; "
                f"the limit is {MAX_ROWS:,}."
            )


def _listing(items: list[str]) -> str:
    listed = ", ".join(items[:_MAX_LISTED_RENAMES])
    more = len(items) - _MAX_LISTED_RENAMES
    return listed + (f" and {more} more." if more > 0 else ".")
