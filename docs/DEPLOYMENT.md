# Deployment

The dashboard is a Streamlit app (`dashboard/app.py`). It needs Python 3.11+, the packages in
`requirements.txt`, an API key for the LLM provider, and write access to a temporary folder for
uploads. The Olist demo database is not committed; it is built on the server.

## Settings

Set these as environment variables or in the host's secrets settings (never commit them):

| Variable | Required | Meaning |
|---|---|---|
| `LLM_API_KEY` | yes | API key of the primary LLM provider |
| `LLM_PROVIDER`, `LLM_MODEL` | no | default `gemini`, `gemini-3.1-flash-lite` (see `.env.example`) |
| `LLM_FALLBACK_PROVIDER`, `LLM_FALLBACK_MODEL`, `LLM_FALLBACK_API_KEY` | recommended | used when the primary provider is rate limited |
| `DB_PATH` | no | demo database file, default `data/olist.db` |
| `DEMO_DB_AUTO_BUILD` | no | `0` turns off building the demo database at start-up |

Without an API key the app still starts, but questions return "The language model is not
available" and reports use the plain summary.

## Option 1: Streamlit Community Cloud (free, no server to manage)

1. Sign in at share.streamlit.io with the GitHub account that can read the repository.
2. "Create app" -> choose the repository, branch `main` and main file `dashboard/app.py`.
   Under "Advanced settings" choose Python 3.11 and paste the secrets in TOML format:

   ```toml
   LLM_API_KEY = "..."
   LLM_FALLBACK_PROVIDER = "groq"
   LLM_FALLBACK_API_KEY = "..."
   ```

   Top-level secrets are available to the app as environment variables, which is what
   `shared/config.py` reads.
3. Deploy. On the first start the app downloads the Olist dataset and builds the demo database
   (about a minute, shown with a spinner). The file is kept until the app is restarted or
   redeployed, and then built again.

The free tier has limited memory and CPU. If the first-start build fails there, the app keeps
running without the demo (uploads still work) and shows the reason on the demo pages; use the
Docker option to build the database ahead of time instead. Apps that are not used for a while
go to sleep and start again on the next visit.

## Option 2: Docker (any container host: Render, Railway, Fly.io, a VM)

```bash
docker build -t nl-data-assistant .
docker run -p 8501:8501 -e LLM_API_KEY=... -e LLM_FALLBACK_API_KEY=... nl-data-assistant
```

The image builds the demo database during `docker build`, so the app starts at once. It
listens on `$PORT` if the host sets one (default 8501) and has a health check on
`/_stcore/health`. Use `--build-arg BUILD_DEMO_DB=0` for a smaller image without the demo
(uploads still work; the demo pages then say the database is not available).

## Before going public

- **LLM quota.** Every chat question uses two LLM requests and every report one. A public app
  can use up a free daily quota quickly; set the fallback provider and watch the provider's
  usage page.
- **Uploaded data.** Uploads are stored in the system temp folder, one folder per browser
  session, and deleted when replaced, removed, or after 24 hours. The upload page tells users
  what is sent to the LLM provider and not to upload personal data.
- **Usage log.** Questions about the demo data are written to `dashboard/usage_log.jsonl` and
  shown on the Usage page to every visitor. Questions about uploaded data are not stored. On a
  container host the log is lost on restart.
- **Evaluation page.** Evaluation results are not committed, so a fresh deployment shows "No
  evaluation results yet" until `python -m evaluation.run_eval` is run on the server.
- **Access.** The app has no login. If the data or the API budget should stay private, use
  the host's access control (for example a private Streamlit Community Cloud app shared with
  named viewers).

## Checking a deployment

1. The sidebar shows "Olist e-commerce (demo)" and the Chat page answers "How many orders were
   placed in 2017?".
2. On "Upload data", load a small CSV export: the table summary and notes appear, and the
   sidebar switches to the uploaded data.
3. On "Reports", the columns are pre-filled; "Generate report" shows the report for the last
   week in the file.
