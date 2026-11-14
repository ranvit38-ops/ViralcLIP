# ViralClip

Self-hosted tool: long video in → captioned short clips out → (planned) YouTube upload and scheduling.
See `DEVELOPMENT_STATUS.md` for what works today and `ARCHITECTURE.md` for the design.

## Run locally

Needs Python 3.11+, Node 20+, and ffmpeg.

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000      # one worker process (see status doc)
```

```bash
cd frontend && npm install && npm run dev        # http://localhost:3000
```

Configuration is read from environment variables (see `.env.example`). To load a `.env` file: `set -a; . ../.env; set +a` before starting.

## Tests

```bash
cd backend && .venv/bin/python -m pytest -q
```

You are responsible for having the rights to every video you upload, edit and publish.
