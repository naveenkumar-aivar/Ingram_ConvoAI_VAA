# Vertical Adaptation Agent — Backend

FastAPI backend for the **Vertical Adaptation Agent**. Transforms a generic chatbot into an industry-specialized assistant by generating and applying versioned **context packs**.

**Architecture:** AWS-only (S3, Bedrock, DynamoDB, CloudWatch). Agentic generation with human review, then approval provisions real AWS resources (Guardrails, Knowledge Bases, Prompts) streamed to UI via Server-Sent Events.

---

## Quick Start

### Prerequisites
- Python 3.11+ (Strands requirement)
- AWS account with Bedrock access
- AWS credentials configured (`aws configure` or `~/.aws/credentials`)

### Setup
```bash
cd backend

# Create venv + install
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env: Set AWS_REGION, BEDROCK_MODEL_ID, S3_BUCKET

# Run
python -m uvicorn app.main:app --reload --port 8000
```

### Verify
```bash
curl http://localhost:8000/api/health
# {"status": "ok", "service": "Vertical Adaptation Agent (AWS-only)"}

# Interactive docs
open http://localhost:8000/docs
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  FastAPI (main.py)                                      │
│  - HTTP routes only (thin wrapper)                      │
└──────────┬────────────────────────────────────────────┬─┘
           │                                            │
    ┌──────▼────────────┐              ┌────────────────▼──┐
    │  Pack Management  │              │  Chat Agent       │
    │  ┌────────────┐   │              │  ┌──────────────┐ │
    │  │ generator_ │   │              │  │ agent.py     │ │
    │  │ agent.py   │──▶│ Strands+     │  │ (generic +   │ │
    │  │ (Bedrock)  │   │ Bedrock      │  │  adapted)    │ │
    │  ├────────────┤   │              │  ├──────────────┤ │
    │  │ pack_      │   │              │  │ compare.py   │ │
    │  │ builder.py │   │ Claude       │  │ (metrics +   │ │
    │  │ (fallback) │   │              │  │  evaluation) │ │
    │  ├────────────┤   │              │  ├──────────────┤ │
    │  │ drafts.py  │   │              │  │ evaluator.py │ │
    │  │ (S3 + SSE) │   │              │  │ (LLM-as-     │ │
    │  └────────────┘   │              │  │  judge)      │ │
    └────────┬──────────┘              └────────┬────────┘
             │                                  │
             └──────────┬───────────────────────┘
                        │
           ┌────────────┼────────────────┐
           │            │                │
      ┌────▼──────┐  ┌──▼────┐  ┌──────▼────┐
      │  AWS      │  │ AWS   │  │ AWS       │
      │  S3       │  │ Bedrock  │ DynamoDB  │
      │ (Storage) │  │(LLM+KB   │ (Sessions)│
      │           │  │ +Guard)  │           │
      └───────────┘  └─────────┘ └──────────┘
```

**All storage, compute, and data live in AWS.** No local fallback. No Chroma. No mock mode.

---

## File Structure

```
app/
├── main.py                    # FastAPI routes (HTTP layer)
├── config.py                  # AWS config (region, bucket, model ID, table names)
├── models.py                  # Pydantic schemas (ContextPack, requests/responses)
│
├── generator_agent.py         # Agentic pack builder (Strands + Bedrock)
├── generator_tools.py         # Agent tools (list_existing_pieces, get_existing_piece)
├── pack_builder.py            # Deterministic fallback + STAGES
│
├── agent.py                   # Chat agent (generic + adapted + RAG)
├── compare.py                 # Before/after comparison + metrics
├── evaluator.py               # LLM-as-judge (5-dimension scoring)
├── sessions.py                # DynamoDB session storage + TTL
├── drafts.py                  # S3 draft storage + approval SSE stream
│
├── middleware.py              # Request ID tracing, logging
├── logging_config.py          # CloudWatch JSON logger
│
└── aws/
    ├── bedrock_client.py       # Bedrock Runtime + Agent Runtime client
    ├── bedrock_kb.py           # Managed Knowledge Base (create + ingest)
    ├── bedrock_resources.py    # Guardrails & Prompts creation
    ├── storage.py              # Context-pack S3 catalog
    ├── pieces.py               # Ontology reuse + merge
    └── s3_manager.py           # Low-level S3 operations
```

---

## Configuration (`.env`)

| Variable | Default | Notes |
|----------|---------|-------|
| `AWS_REGION` | `us-east-1` | Region for S3, Bedrock, DynamoDB |
| `BEDROCK_MODEL_ID` | `CONFIRM_IN_CONSOLE` | Exact model ID from Bedrock console (e.g., `us.anthropic.claude-haiku-4-5-20251001-v1:0`) |
| `S3_BUCKET` | `vertical-adaption-agent` | S3 bucket (auto-created if needed) |
| `CONTEXT_PACK_PREFIX` | `context-pack/` | S3 path for pack versions |
| `INDUSTRY_DOCS_PREFIX` | `industry-docs/` | S3 path for ontology + KB files |
| `KB_ROLE_NAME` | `vaa-bedrock-kb-role` | IAM role for KB (auto-provisioned, idempotent) |
| `SESSIONS_TABLE_NAME` | `vaa-sessions` | DynamoDB table (auto-provisioned with TTL) |
| `SESSION_TTL_DAYS` | `30` | Session auto-expiration (days) |
| `API_HOST` | `0.0.0.0` | Server bind address |
| `API_PORT` | `8000` | Server port |
| `CORS_ORIGINS` | `http://localhost:5173` | Allowed origins (Vite dev server) |

**Credentials:** boto3 reads from standard chain (env vars, `~/.aws`, SSO, instance role).

---

## Pack Generation Flow

### Step 1: Agent Tool Discovery
```python
# Agent calls:
list_existing_pieces(industry)
# Returns: {
#   "ontology": [{term, definition, core}, ...],
#   "domain_prompts": [...],
#   "escalation_rules": [...],
#   ...
# }
```

### Step 2: Agent Builds Pack
```python
# Agent uses tools to:
# - Discover reusable components
# - Get specific piece: get_existing_piece(industry, kind, name)
# - Generate missing parts with industry knowledge
# - Return complete JSON pack

pack = {
  "persona": {...},
  "scope": {...},
  "ontology": [...],
  "domain_prompts": [...],
  "escalation_rules": [...],
  "guardrails": {...}
}
```

### Step 3: Return Draft
```python
# Draft NOT saved to S3 yet
# Returned to UI for human review
# User can edit any field
```

### Step 4: Validate on Save
```python
# When user saves edits:
gaps = validate_pack(json.dumps(pack))
# Returns: "OK" or "GAPS: persona.name missing; ontology empty"
```

### Step 5: Approve (Streaming)
```python
# 4-step process, SSE stream:
# 1. S3       - Save pack, assign version (v1, v2, etc.)
# 2. Guardrail- Create Bedrock Guardrail
# 3. KB       - Ingest files into Knowledge Base
# 4. Prompt   - Create Bedrock Prompt resource
# Final: Pack persisted with resource IDs
```

---

## API Endpoints (Reference)

### Health
```http
GET /api/health
→ 200 {"status": "ok"}
```

### Pack Management
```http
POST /api/context-packs/check-name
{"industry": "Banking", "name": "support"}
→ 200 {"exists": false}

POST /api/context-packs/generate
{"industry": "Banking", "name": "support", "use_case": "..."}
→ 200 DraftResponse {draft_id, pack, system_prompt, ...}

PUT /api/context-packs/{draft_id}/draft
{"pack": {...}}
→ 200 {pack, system_prompt, gaps}

POST /api/context-packs/{draft_id}/approve
→ 200 SSE Stream {"step": "s3", "status": "in_progress"}
         {"step": "s3", "status": "succeeded"}
         ...
         {"step": "done", "status": "succeeded", "pack": {...}}

DELETE /api/context-packs/{draft_id}
→ 200 {"deleted": true}
```

### Reading Packs
```http
GET /api/context-packs?industry=Banking&agent_name=support
→ 200 [PackVersionItem, ...]

GET /api/context-packs/support-v1
→ 200 ContextPack {...}

GET /api/context-packs/support-v1/system-prompt
→ 200 {generic: "...", adapted: "..."}
```

### Chat
```http
POST /api/chat/session
{"pack_id": "support-v1"}
→ 200 {session_id: "sess_abc123"}

POST /api/chat/compare
{"session_id": "sess_abc123", "question": "How do I...?"}
→ 200 CompareResponse {
    generic: "...",
    adapted: "...",
    evaluation: {generic: {...}, adapted: {...}},
    metrics: {input_tokens, output_tokens, latency_ms, ...}
  }

DELETE /api/chat/session/{session_id}
→ 200 {"cleared": true}
```

---

## AWS Resources Provisioned

### Per Pack (Approval)
- **Bedrock Guardrail** - Stores denied_topics, blocked_phrases, pii_blocks
- **Bedrock Prompt** - Versioned system prompt
- **Bedrock Knowledge Base** - Vector search over uploaded files
  - S3 data source: `industry-docs/{industry}/{usecase}/uploaded_data/`

### Global (Auto-provisioned, Idempotent)
- **S3 Bucket** - `vertical-adaption-agent`
  - Prefixes: `context-pack/`, `industry-docs/`, `_drafts/`
- **DynamoDB Table** - `vaa-sessions`
  - TTL field: `expires_at` (auto-deletes old sessions)
- **IAM Role** - `vaa-bedrock-kb-role`
  - Trust: Bedrock Knowledge Base service
  - Policy: S3 read/write for KB data source

---

## Sample Pack Structure

```json
{
  "meta": {
    "industry": "Banking",
    "version": "v1",
    "status": "active",
    "pack_id": "support-v1"
  },
  "persona": {
    "name": "Morgan",
    "role": "Banking Support Specialist",
    "tone": "Professional, empathetic, clear",
    "characteristics": [...]
  },
  "scope": {
    "in_scope": ["Account inquiries", "Dispute resolution", ...],
    "out_of_scope": ["Investment advice", "Tax preparation"],
    "refusal_message": "I appreciate the question, but..."
  },
  "ontology": [
    {
      "term": "ACH Transfer",
      "definition": "Electronic bank-to-bank transfer...",
      "core": true
    },
    ...
  ],
  "domain_prompts": [
    "Always verify customer identity before discussing accounts...",
    ...
  ],
  "escalation_rules": [
    {
      "trigger": "Fraud or unauthorized transactions",
      "escalate_to": "Fraud Prevention Team",
      "priority": "High"
    },
    ...
  ],
  "guardrails": {
    "safety_rules": ["Never execute transactions...", ...],
    "denied_topics": ["Committing fraud", ...],
    "blocked_phrases": ["Send me your password", ...],
    "pii_blocks": ["Social Security Numbers", ...]
  },
  "integrations": {
    "guardrail_id": "gr_abc123",
    "guardrail_version": "1",
    "prompt_id": "pr_def456",
    "knowledge_base_id": "kb_ghi789"
  }
}
```

---

## Deployment

### Local Development
```bash
python -m uvicorn app.main:app --reload --port 8000
```

### Production (AgentCore)
Located in `backend/agent/`:
```bash
docker build -t vaa-agent .
docker push <ecr-repo>/vaa-agent:latest
```

### Serverless (Lambda)
Located in `backend/lambda/`:
- Handler function
- API Gateway integration
- Environment via Systems Manager

### Infrastructure as Code
Located in `backend/deploy/`:
- IAM policies
- S3 bucket config
- DynamoDB setup
- Bedrock resource provisioning

---

## Logging & Observability

**Structured JSON logging:**
```json
{
  "timestamp": "2026-10-21T06:44:42.315Z",
  "level": "INFO",
  "logger": "app.main",
  "message": "POST /api/chat/compare 200",
  "request_id": "req_abc123",
  "duration_ms": 2150
}
```

**CloudWatch in production** - All logs auto-streamed.

---

## Costs (Claude Haiku, Bedrock)

| Operation | Tokens | Cost |
|-----------|--------|------|
| Pack generation (8000 budget) | ~2000 input, ~1500 output | ~$0.06 |
| Chat response (with RAG) | ~3500 input, ~400 output | ~$0.05 |
| LLM evaluation | ~1000 input, ~200 output | ~$0.01 |

**Per month (1000 generations + 10K chats):** ~$600-800

---

## Known Limitations

- **No concurrent KB ingestion** - One pack approval at a time
- **No pack deletion** - Only versioning
- **Session cleanup** - Manual or TTL-based
- **Token metrics** - Require Strands/Bedrock metadata

---

## Version History

**v1.3 (Oct 2026):**
- 8000 token budget (prevents truncation)
- Validation only on user edits (not during generation)
- Case-insensitive name checking
- "Generate New Version" UX button

**v1.2:**
- Fixed guardrail overblocking
- Improved ontology (skip generic terms)
- 6 consolidated escalation rules

**v1.1:**
- AWS-only architecture
- LLM-as-judge evaluation
- Multi-turn sessions

