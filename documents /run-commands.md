# Run Commands — Backend & Frontend

How to start both servers for the Vertical Adaptation Agent.

- Backend (FastAPI): http://localhost:8000  (API docs: http://localhost:8000/docs)
- Frontend (React/Vite): http://localhost:5173

Run them in **two separate terminals**. Start the backend first.

---

## Backend (FastAPI + Strands)

Requires Python ≥ 3.10. This project uses Python 3.11 via `uv`.

### First time only (setup)
```bash
cd /Users/naveenkumarc/Ingram-Vertical/backend

# create the virtualenv with Python 3.11
uv venv --python 3.11 .venv

# install dependencies
uv pip install --python .venv/bin/python -r requirements.txt

# create the .env file (defaults to MOCK_MODE=true — no AWS needed)
cp .env.example .env
```

### Every time (run the server)
```bash
cd /Users/naveenkumarc/Ingram-Vertical/backend
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Check it works:
```bash
curl http://localhost:8000/api/health
# -> {"status":"ok","mock_mode":true}
```

---

## Frontend (React + Vite)

Requires Node.js (v18+). The Vite dev server proxies `/api` to the backend
on port 8000, so the backend should be running first.

### First time only (setup)
```bash
cd /Users/naveenkumarc/Ingram-Vertical/frontend
npm install
```

### Every time (run the server)
```bash
cd /Users/naveenkumarc/Ingram-Vertical/frontend
npm run dev
```

Then open **http://localhost:5173** in the browser.

---

## Quick reference

| Server   | Directory   | Command                                                        | URL                   |
|----------|-------------|----------------------------------------------------------------|-----------------------|
| Backend  | `backend/`  | `.venv/bin/python -m uvicorn app.main:app --reload --port 8000`| http://localhost:8000 |
| Frontend | `frontend/` | `npm run dev`                                                  | http://localhost:5173 |

---

## Stopping the servers
Press `Ctrl + C` in each terminal.

## Notes
- Default is **MOCK_MODE=true** — the app runs fully without AWS credentials.
- To switch to real AWS later, edit `backend/.env` (set `MOCK_MODE=false`,
  `BEDROCK_MODEL_ID`, `AWS_REGION`, `S3_BUCKET`) — see `backend/README.md`.
- If the frontend shows data but the chatbot answers look like fallback mock
  text, confirm the backend is running on port 8000.
