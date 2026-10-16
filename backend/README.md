# Vertical Adaptation Agent — Backend

Bedrock-only backend for the Vertical Adaptation Agent. It adapts one generic
chatbot to an industry by generating and applying a versioned **context pack**
(persona, domain prompts, escalation rules, guardrails), and exposes a small
HTTP API the UI (and any existing website/agent) calls.

Built with **FastAPI** + the **Strands Agents SDK**, talking to **Amazon
Bedrock** (model + Knowledge Bases + Guardrails + Prompt Management) and
**S3**. AgentCore is intentionally **not** used yet (not in the SOW) — the
agent is structured so it can be hosted on AgentCore later with minimal change.

---

## Mock mode vs. real mode

The single most important setting is `MOCK_MODE` (in `.env`):

| MOCK_MODE | Behaviour |
|-----------|-----------|
| `true` (default) | No AWS calls. Returns realistic mock data. Runs with zero credentials. |
| `false` | Real AWS via boto3 / Strands. Needs credentials + Bedrock model access. |

This means the whole stack runs and demos **today without AWS**, and flips to
real AWS by changing one env var (plus confirming the model id + region).

---

## Project layout

```
backend/
├── requirements.txt
├── .env.example            # copy to .env
└── app/
    ├── config.py           # Settings (MOCK_MODE, LLM_PROVIDER, region, bucket)
    ├── models.py           # ContextPack + API request/response shapes
    ├── pack_builder.py     # the agent — builds a pack, REUSING existing pieces
    ├── drafts.py           # draft (temp) storage; commit on approve, discard on reject
    ├── agent.py            # answer() — the chatbot. AgentCore-portable.
    ├── compare.py          # compare() — before/after + evaluation
    ├── evaluator.py        # LLM-as-judge scoring of the two answers
    ├── sessions.py         # conversation memory (per session_id)
    ├── main.py             # FastAPI app (the HTTP layer)
    └── aws/
        ├── bedrock_client.py  # Bedrock calls: KB retrieve + guardrail (mock/real)
        ├── pieces.py          # reusable source pieces per industry (discover/read/write)
        └── storage.py         # context-pack catalog (local s3-data / S3)
```

Key design rules:
- **All AWS/business logic lives server-side**; `main.py` is a thin wrapper.
- `agent.py:answer()` and `pack_builder.py:build_pack()` have no web code, so
  they are the units a future AgentCore deployment would host.
- The pack-building agent **reuses existing pieces** (`aws/pieces.py`) and only
  generates what's missing — "don't build the wheel again".

---

## Running it

Requires **Python ≥ 3.10** (Strands). This repo was set up with Python 3.11
via `uv`.

```bash
cd backend

# one-time: create venv + install (uv)
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# config
cp .env.example .env          # defaults to MOCK_MODE=true

# run
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Health check: `curl localhost:8000/api/health` → `{"status":"ok","mock_mode":true}`

Interactive API docs: open `http://localhost:8000/docs`.

---

## Switching to real AWS (tomorrow)

1. **Credentials**: run `aws configure` / `aws sso login` (boto3 reads the
   standard chain — you usually don't put keys in `.env`).
2. **Enable Bedrock model access**: AWS console → Bedrock → *Model access* →
   request the Claude model(s) you'll use.
3. **Confirm the model id**: copy the exact id from the console into
   `BEDROCK_MODEL_ID`. Do **not** guess — Bedrock rejects wrong ids. (Ids are
   often inference-profile ids prefixed `us.`.)
4. **Pick a region** where Bedrock + Knowledge Bases + Guardrails + the model
   are all available; set `AWS_REGION`.
5. **S3 bucket**: create it, set `S3_BUCKET`. Context packs are written to
   `s3://<bucket>/context-packs/`.
6. Set `MOCK_MODE=false` and restart.

Nothing in the application logic changes between mock and real — only
`aws/bedrock_client.py` and `aws/storage.py` switch behaviour internally.

---

## How the demo works

- **Chatbot (before/after):** `POST /api/chat/compare` runs the agent twice for
  one question — once with no pack (generic) and once with the selected pack
  (adapted) — and returns both answers plus evaluation metrics.
- **Create a Context Pack:** `POST /api/context-packs/generate` runs the 6-step
  generator, creates the guardrail, stores the pack, and returns it for the
  human-in-the-loop review step before it goes live.

See `INTEGRATION_GUIDE.md` for the full API contract.
