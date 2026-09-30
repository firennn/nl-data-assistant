# Contributing

## Setup
1. Python 3.11 or newer.
2. Create a virtual environment and install dev dependencies:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```
3. Copy `.env.example` to `.env` and add your own API key(s) for the LLM provider API.
   Never commit `.env`.
4. Build the database: `python -m data.build_db` (see `data/README.md`).
5. Check everything works: `python -m pytest`.

## Ownership
Each person works inside their own folders (see the table in `README.md`). Modules only talk
through `shared/` and the database, never through each other's internal functions.

If you need a change in someone else's folder or in `shared/`, open an issue and tag the owner
instead of editing it directly. Changes to `shared/` interfaces must update `docs/ARCHITECTURE.md`.

## Branches
- Never commit directly to `main`; open a pull request.
- Branch names: `feat/<area>-<topic>`, `fix/<area>-<topic>`, `docs/<topic>`
  (for example `feat/reports-weekly-metrics`, `fix/agent-retry-limit`).
- Keep branches short-lived and rebase or merge `main` often.

## Commits
- Small, focused commits: one logical change per commit.
- Message: imperative summary under 72 characters, optional body explaining why.
  ```
  Add weekly revenue metric

  Revenue excludes canceled and unavailable orders, matching the definition
  in docs/DECISIONS.md.
  ```
- Stage files by name and check `git status` before committing. Never commit `.env`,
  database files, raw data, model weights or generated report files.

## Pull requests
Use the PR template. Before requesting a review:
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `python -m pytest` passes (CI runs both on every PR)
- [ ] New behavior has tests
- [ ] `docs/DECISIONS.md` has an entry for any design decision
- [ ] At least one teammate reviewed the PR

## Decisions log
Add a row to `docs/DECISIONS.md` when you choose a library, a metric definition, a model, or
anything someone might later ask "why did we do it this way?" about.
