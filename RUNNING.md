# Running Yusor Locally

Two processes, run in **two separate terminals** (both need to stay open while you use the app):
- FastAPI backend on port **8000**
- Streamlit UI on port **8501** (talks to the backend via `API_BASE_URL` in `.env`)

## One-time setup (already done on this machine)

```powershell
pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env`: `QDRANT_URL`, `QDRANT_API_KEY`, `LLM_API_KEY`, and make sure `LLM_BASE_URL` /
`LLM_MODEL` belong to the same provider (e.g. both Groq, or both OpenAI) — a mismatch here is
what causes 404 errors from the agent pipeline.

RAG index is already built in Qdrant — don't re-run `python -m app.rag.ingest` unless the
policy files under `policy_texts/` changed.

## Start

**Terminal 1 — backend:**
```powershell
cd C:\Users\shaha\YusrAgent\YusrAgent
uvicorn app.api.main:app --reload
```
Wait for `Application startup complete.` / `Uvicorn running on http://127.0.0.1:8000`.

**Terminal 2 — frontend:**
```powershell
cd C:\Users\shaha\YusrAgent\YusrAgent
streamlit run app/ui/streamlit_app.py
```
It opens `http://localhost:8501` in your browser automatically.

## Verify it's up

```powershell
curl http://127.0.0.1:8000/health      # expect {"status":"ok"}
```
Then open `http://localhost:8501` in a browser and log in.

## Stop

- Normal case: click into each terminal window and press **Ctrl+C**.
- If you lost the terminals (e.g. they were started in the background), kill by port:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

  or, broader (kills *all* local python/streamlit processes — only use if you know nothing
  else important is running):

```powershell
Get-Process python, streamlit -ErrorAction SilentlyContinue | Stop-Process -Force
```

## Troubleshooting

- **UI loads but every query fails / errors mentioning the LLM**: check `LLM_BASE_URL` and
  `LLM_MODEL` in `.env` match the same provider. See `.env.example` for the Groq pairing example.
- **429 / quota errors**: the `LLM_API_KEY` owner's free-tier daily limit is exhausted — needs a
  new key or to wait for the provider's daily reset. Not a code issue.
- **Port already in use on start**: something is already bound to 8000 or 8501 — either an
  earlier run wasn't stopped (see Stop section above) or another app is using the port.
