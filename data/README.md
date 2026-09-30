# Data

| File | Purpose |
|---|---|
| `download.py` | Downloads the raw Olist CSVs into `raw/` (no Kaggle account needed). |
| `build_db.py` | Cleans the CSVs and builds `olist.db` (`python -m data.build_db`). |
| `schema.sql` | Table definitions, keys and indexes. Also used by the test fixture database. |
| `schema_docs.py` | Table and column descriptions, stored in the `_schema_docs` table. |
| `raw/` | Downloaded CSV files (not committed). |
| `olist.db` | The built database (not committed, about 115 MB). |

Build or rebuild the database:

```bash
python -m data.build_db
```

Manual alternative to the download: get the zip from
https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce, extract the CSVs into `data/raw/`
and run `python -m data.build_db --no-download`.

Schema, cleaning steps and known data limitations: [docs/SCHEMA.md](../docs/SCHEMA.md).
