# Vertical Adaptation Agent — Backend

FastAPI backend for the **Vertical Adaptation Agent**. It adapts a single
generic chatbot to a specific industry/usecase by generating and applying a
versioned **context pack** (persona, scope, domain prompts, ontology,
escalation rules, guardrails), and exposes a small HTTP API the React/Vite UI
calls.

The pack is generated agentically (Strands Agents SDK + Amazon Bedrock, with a
deterministic fallback), reviewed and edited by a human, then **approved** —
and approval provisions real per-industry/usecase AWS resources, streamed to
the UI as Server-Sent Events (SSE).

---

## Architecture: AWS-Only Backend

**This is a pure AWS implementation.** All storage, retrieval, and LLM operations
use AWS services exclusively. No local fallback, no mock mode, no Chroma.

| Component | Implementation |
|-----------|-----------------|
| **Storage** | Amazon S3 (context packs, industry docs, uploaded KB files) |
| **Sessions** | Amazon DynamoDB (chat memory, auto-expiring with TTL) |
| **LLM** | Amazon Bedrock (Claude, Llama, or other available models) |
| **Knowledge Base** | Bedrock managed Knowledge Base (vector search over uploaded docs) |
| **Safety** | Bedrock Guardrails (topic denial, phrase blocking, PII protection) |
| **Prompts** | Bedrock Prompt Management (versioned, reusable system prompts) |
| **Logging** | CloudWatch (structured JSON logs with request tracing) |

---

## Project layout

```
backend/
├── requirements.txt
├── .env.example               # copy to .env (AWS config only)
└── app/
    ├── main.py                # FastAPI app — the HTTP layer (thin wrapper)
    ├── config.py              # AWS settings (region, bucket, model ID, table names)
    ├── models.py              # ContextPack + API request/response shapes
    ├── pack_builder.py        # deterministic pack builder + STAGES + BuildInput
    ├── generator_agent.py     # agentic pack builder (Strands + Bedrock)
    ├── generator_tools.py     # agent tools: reuse-discovery, validate_pack, shape_guardrail_and_prompt
    ├── drafts.py              # draft create/get/update/discard + approve_stream (SSE, S3 storage)
    ├── agent.py               # answer() — the chatbot (generic vs adapted + RAG)
    ├── compare.py             # before/after comparison + evaluation + timing
    ├── evaluator.py           # LLM-as-judge scoring of the two answers (Bedrock)
    ├── sessions.py            # conversation memory (DynamoDB, auto-expiring)
    ├── logging_config.py      # CloudWatch JSON logging
    ├── middleware.py          # request id + slow-request logging
    └── aws/
        ├── storage.py             # context-pack catalog (list/get/save packs from S3)
        ├── pieces.py              # ontology merge + reuse discovery (S3 only)
        ├── bedrock_resources.py   # Bedrock Guardrail + Prompt Management creation
        ├── bedrock_kb.py          # managed Bedrock Knowledge Base (create + ingest)
        ├── bedrock_client.py      # Bedrock Runtime + Agent Runtime client
        └── s3_manager.py          # low-level S3 operations

scripts/
└── cleanup_s3_case_duplicates.py  # utility: remove case-duplicate S3 prefixes
```

Design rules:
- **All AWS/business logic lives server-side.** `main.py` is a thin HTTP wrapper.
- The pack-building agent **reuses existing pieces** (`aws/pieces.py`) and only
  generates what is missing.
- **100% AWS storage & compute.** No local files, no mock modes, no fallback paths.

---

## Running it

Requires **Python ≥ 3.10** (Strands). This repo was set up with Python 3.11 via `uv`.

```bash
cd backend

# one-time: create venv + install
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# config
cp .env.example .env          # set AWS credentials and region

# run
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Health check:

```bash
curl localhost:8000/api/health
# {"status":"ok","service":"Vertical Adaptation Agent (AWS-only)"}
```

Interactive API docs: `http://localhost:8000/docs`.

---

## Configuration (`.env`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `AWS_REGION` | `us-east-1` | AWS region for S3, Bedrock, DynamoDB. |
| `BEDROCK_MODEL_ID` | `CONFIRM_IN_CONSOLE` | Exact model/inference-profile id from the Bedrock console. |
| `S3_BUCKET` | `vertical-adaption-agent` | S3 bucket for context packs, docs, KB files. |
| `CONTEXT_PACK_PREFIX` | `context-pack/` | S3 prefix for context-pack version files. |
| `INDUSTRY_DOCS_PREFIX` | `industry-docs/` | S3 prefix for ontology + uploaded data. |
| `KB_ROLE_NAME` | `vaa-bedrock-kb-role` | IAM role the managed Bedrock KB assumes (self-provisioned once, idempotent). |
| `SESSIONS_TABLE_NAME` | `vaa-sessions` | DynamoDB table for chat sessions (self-provisioned once, with TTL). |
| `SESSION_TTL_DAYS` | `30` | Days a chat session is kept before DynamoDB TTL expires it. |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `8000` | Server bind address. |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins (Vite dev server). |

Credentials are read by boto3 from the standard chain (env vars, `~/.aws`,
SSO, instance role) — you usually do **not** put keys in `.env`.

---

## Getting started with AWS

1. **Credentials:** Run `aws sso login` or `aws configure`. boto3 reads the
   standard chain automatically.
2. **Enable Bedrock model access:** AWS console → Bedrock → *Model access* →
   request the model(s) you will use (e.g., Claude).
3. **Confirm the model id:** Copy the exact id from the console into
   `BEDROCK_MODEL_ID` (often an inference-profile id prefixed `us.`).
4. **Pick a region** where Bedrock, Knowledge Bases, Guardrails, and your model
   are available. Set `AWS_REGION`.
5. **Create the S3 bucket** and set `S3_BUCKET`.
6. **Restart the backend.** DynamoDB table and IAM role are auto-provisioned on
   first request.

---

## API endpoints

**Health / metadata**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Status. |
| GET | `/api/industries` | Known industries (from the context-pack catalog in S3). |
| GET | `/api/context-packs/stages` | Ordered generation stage labels. |

**Read packs**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/context-packs?industry=&agent_name=` | Pack versions for an industry (optionally filtered by agent name). |
| GET | `/api/agent-names?industry=` | List of unique agent names for an industry. |
| GET | `/api/context-packs/{pack_id}` | A single pack from S3. |
| GET | `/api/context-packs/{pack_id}/system-prompt` | Both generic (before) and adapted (after) system prompts. |

**Create flow (draft → approve)**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/context-packs/upload-kb` | Upload KB files for an industry/usecase (stored in S3). |
| POST | `/api/context-packs/check-name` | Check whether a name already exists. |
| POST | `/api/context-packs/generate` | Generate a draft pack; returns the draft for review. |
| PUT | `/api/context-packs/{draft_id}/draft` | Save edits + re-validate; returns `{pack, system_prompt, gaps}`. |
| POST | `/api/context-packs/{draft_id}/approve` | Approve; **SSE stream** of `{step, status, detail}` then terminal `{step:"done", status, pack}`. |
| DELETE | `/api/context-packs/{draft_id}` | Reject/discard the draft (deleted from S3). |

**Chat**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/chat/session` | Start a new chat session (stored in DynamoDB). |
| GET | `/api/chat/sessions` | Recent sessions (sidebar resume list). |
| GET | `/api/chat/session/{id}` | Full history of a session from DynamoDB. |
| DELETE | `/api/chat/session/{id}` | Reset/clear a session. |
| POST | `/api/chat/compare` | Before/after answer for one question + LLM evaluation + latency. |

---

## Approval: SSE step sequence

Approving a draft emits one `in_progress` event and one terminal
`succeeded`/`failed` event per step, in this order:

1. **`s3`** — Finalise meta, merge industry-common ontology, save the pack to S3
   (the context-pack version file is the single source of truth).
2. **`guardrail`** — Create the Bedrock Guardrail from the pack; write
   `guardrail_id` / `guardrail_version` into `pack.integrations`.
3. **`kb`** — Provision a managed Bedrock Knowledge Base over the uploaded
   files and ingest. **Non-fatal:** a KB failure does not abort approval.
4. **`prompt`** — Create the Bedrock Prompt Management resource; write
   `prompt_id` into `pack.integrations`.

A final `{step:"done", status, pack}` event carries the committed pack.

---

## AWS resources created + scoping

Approval provisions **per-industry/usecase** AWS resources:

- **Amazon S3** — Context packs at `context-pack/{industry}/{usecase}/vN.json`;
  ontology at `industry-docs/{industry}/ontology/vN.json`; uploaded files at
  `industry-docs/{industry}/{usecase}/uploaded_data/`; drafts at `_drafts/{draft_id}.json`.
- **Amazon Bedrock** — Foundation Model (via Converse/Strands), a **Guardrail**,
  a **Prompt Management** resource, and a managed **Knowledge Base** (with an S3
  data source scoped to that usecase's `uploaded_data/` prefix).
- **Amazon DynamoDB** — `vaa-sessions` table for chat sessions with TTL-based
  auto-expiration.
- **AWS IAM** — A reusable KB execution role (`vaa-bedrock-kb-role`) the app
  self-provisions once (idempotent).
- **Amazon CloudWatch** — Structured JSON logs with request IDs and latency.

The KB, guardrail, and prompt are scoped per industry/usecase. The KB's S3 data
source is limited to `industry-docs/{industry}/{usecase}/uploaded_data/`.
Switching industry loads a different pack with its own resource ids — there is
**no cross-industry reuse**. Resource ids are persisted into `pack.integrations`.
