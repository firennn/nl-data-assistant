# Data

- `schema.sql` — target SQLite schema (tables, keys, indexes). Also used by the test fixture DB.
- `raw/` — downloaded CSV files (not committed).
- `olist.db` — the built database (not committed).

The download and build scripts (`python -m data.build_db`) and the full schema documentation
(`docs/SCHEMA.md`) are added in the data phase.
