# Vertical Adaptation Agent

The Vertical Adaptation Agent transforms a generic support chatbot into an industry-specialized assistant through versioned **context packs**. Each pack contains a persona, domain ontology, operating rules, escalation workflows, and compliance guardrails. The system uses an agentic generator (LLM + tools) to build packs, human review to refine them, and AWS Bedrock to power the production chatbot with RAG (Retrieval-Augmented Generation) and guardrails.

**Key capabilities:**
- **Agentic pack generation** - LLM builds complete context packs with reuse-first logic
- **Before/after comparison** - Side-by-side generic vs. adapted responses with LLM evaluation
- **AWS-native** - S3, Bedrock (Knowledge Bases, Guardrails, Prompts), DynamoDB, CloudWatch
- **Token & latency tracking** - Real-time metrics for input/output tokens and response times
- **Multi-turn conversations** - Session memory in DynamoDB for context-aware dialogues

## Architecture

**AWS Services (Production):**
- **Amazon S3** - Context packs (`context-pack/`), industry docs, uploaded KB files
- **Amazon Bedrock Knowledge Bases** - Managed vector store with S3 data sources
- **Amazon Bedrock Guardrails** - Content filtering and compliance enforcement
- **Amazon Bedrock Prompt Management** - Versioned system prompts
- **Amazon Bedrock Models** - Claude Haiku for chat and generation (via Strands)
- **Amazon DynamoDB** - Chat session storage with TTL
- **Amazon CloudWatch** - Structured JSON logs with request tracing
- **IAM** - Self-provisioned KB execution role (`vaa-bedrock-kb-role`)

**All local fallback code removed** - system runs AWS-only (no Chroma, no mock mode).

## Repository Structure

```
.
├── backend/              # FastAPI application
│   ├── app/
│   │   ├── agent.py           # Chat agent (answer with RAG)
│   │   ├── compare.py         # Before/after comparison with metrics
│   │   ├── generator_agent.py # Agentic pack builder (Strands)
│   │   ├── evaluator.py       # LLM-as-judge for quality scoring
│   │   ├── sessions.py        # DynamoDB session storage
│   │   ├── drafts.py          # S3 draft storage + approval workflow
│   │   ├── models.py          # Pydantic models (ContextPack, etc.)
│   │   └── aws/
│   │       ├── bedrock_kb.py       # Knowledge Base operations
│   │       ├── bedrock_resources.py # Guardrails & Prompts
│   │       └── storage.py          # S3 pack storage
│   ├── requirements.txt
│   └── README.md
├── frontend/            # React + Vite UI
├── docs/               # Architecture diagrams
└── README.md           # This file
```

## Workflow

**Create → Generate → Review → Approve → Chat**

### 1. Create a Pack
- Provide: **Industry**, **Name**, **Use Case** (required)
- Optional: Persona hints, instructions, guardrails notes, KB files

### 2. Generate (Agentic)
The generator agent:
- Calls `list_existing_pieces` to discover reusable components
- Builds a complete pack (persona, ontology, rules, guardrails)
- Validates output with `validate_pack` and `shape_guardrail_and_prompt`
- Fallback: Deterministic builder if agent fails or hits token limits

**Key improvements (latest):**
- 6000 token budget (up from 4000) prevents truncation
- Ontology: 15-25 industry-specific terms (skips basic terms like "deposit")
- Escalation: Exactly 6 consolidated rules (down from 18+)
- Guardrails: Balanced (blocks harmful actions, not domain terms)

### 3. Review & Edit
- Structured form with real-time validation
- Edit any field (persona, ontology, rules, guardrails)
- Re-validation on save (gaps highlighted)

### 4. Approve (Streaming)
Server-Sent Events stream progress through 4 steps:
1. **S3** - Save pack, merge industry ontology
2. **Guardrail** - Create Bedrock Guardrail (denied topics, blocked phrases, PII)
3. **KB** - Ingest uploaded files into Bedrock Knowledge Base (non-fatal)
4. **Prompt** - Create Bedrock Prompt Management resource

Pack is persisted with all resource IDs (guardrail_id, prompt_id, knowledge_base_id).

### 5. Chat & Compare
- Ask a question → system returns:
  - **Generic response** (no pack, no KB)
  - **Adapted response** (pack + KB + guardrails)
  - **Evaluation metrics** (5 dimensions: Groundedness, Compliance, Persona, Scope, Industry-scoped)
  - **Token usage** (input/output tokens per response)
  - **Latency** (response time + Bedrock latency)
- **Multi-turn memory** - Sessions stored in DynamoDB

## Key Features

### Agentic Generation
- Uses Strands Agent framework on Bedrock Claude Haiku
- Reuse-first: Checks existing pieces before generating
- Self-validates output for completeness
- Concise: 6000 token budget with explicit constraints

### Balanced Guardrails
System prompt guidance:
- **Safety rules**: True boundaries (never execute transactions, never bypass auth)
- **Denied topics**: Illegal actions only (fraud, laundering) - NOT domain terms
- **Blocked phrases**: Actual harmful statements - NOT helpful responses
- **Critical rule**: NEVER block ontology terms customers need to ask about

### Token & Latency Metrics
API returns:
```json
{
  "generic_input_tokens": 1250,
  "generic_output_tokens": 380,
  "adapted_input_tokens": 3400,
  "adapted_output_tokens": 420,
  "generic_latency_ms": 1800,
  "adapted_latency_ms": 2200,
  "generic_duration_ms": 2100,
  "adapted_duration_ms": 4500
}
```

### Session Memory
- DynamoDB table: `vaa-sessions`
- Separate histories for generic/adapted sides
- TTL: 30 days (configurable)
- Sidebar shows recent sessions with preview

## Environment Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- AWS credentials configured (`~/.aws/credentials`)
- AWS region with Bedrock access

### Backend Setup
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Configure .env
cp .env.example .env
# Edit .env: set AWS_REGION, S3_BUCKET, BEDROCK_MODEL_ID

# Run server
python -m uvicorn app.main:app --reload --port 8000
```

### Frontend Setup
```bash
cd frontend
npm install
npm run dev  # Runs on http://localhost:5173
```

## Configuration

**Backend `.env` variables:**
```bash
# AWS
AWS_REGION=us-east-1
S3_BUCKET=vertical-adaption-agent
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0

# DynamoDB
SESSIONS_TABLE_NAME=vaa-sessions
SESSION_TTL_DAYS=30

# Server
API_HOST=0.0.0.0
API_PORT=8000
CORS_ORIGINS=http://localhost:5173
```

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Health check |
| GET | `/api/industries` | List industries |
| GET | `/api/context-packs` | List packs (filtered by industry/agent) |
| GET | `/api/context-packs/{id}` | Get pack details |
| POST | `/api/context-packs/generate` | Generate draft pack |
| PUT | `/api/context-packs/{draft_id}/draft` | Update draft |
| POST | `/api/context-packs/{draft_id}/approve` | Approve (SSE stream) |
| DELETE | `/api/context-packs/{draft_id}` | Discard draft |
| POST | `/api/chat/session` | Create session |
| GET | `/api/chat/sessions` | List recent sessions |
| POST | `/api/chat/compare` | Compare generic vs adapted |
| DELETE | `/api/chat/session/{id}` | Clear session |

## Testing

**Quick test (backend only):**
```bash
cd backend
.venv/bin/python -m pytest  # (if tests exist)
```

**Manual test:**
1. Start backend: `python -m uvicorn app.main:app --reload --port 8000`
2. Visit: `http://localhost:8000/api/health`
3. Generate a pack: POST to `/api/context-packs/generate`
4. Approve it: POST to `/api/context-packs/{draft_id}/approve`
5. Chat: POST to `/api/chat/compare`

## Logs & Observability

- **Structured JSON logs** (stdout → CloudWatch in production)
- **Request tracing** with X-Request-ID
- **Performance metrics** (duration_ms, slow_request warnings)
- **Token usage** logged per LLM call
- **CloudWatch Logs** in deployed environment

## Known Limitations

- **No concurrent KB ingestion** - approve one pack at a time
- **No pack deletion** - packs are versioned, not deleted
- **Session cleanup** - manual or rely on DynamoDB TTL
- **Token metrics** - Require Strands/Bedrock metadata (may not always populate)

## Recent Changes

**2024-10-20:**
- ✅ Fixed guardrail overblocking (separated actions from domain terms)
- ✅ Improved generator ontology (skip basic terms, focus on industry-specific)
- ✅ Reduced escalation rules (18 → 6 consolidated)
- ✅ Added token/latency metrics to compare endpoint
- ✅ Increased agent token budget (4000 → 6000)
- ✅ Fixed generic answer support (pack_id=None works)
- ✅ Updated .gitignore (removed kb-data/, s3-data/, logs/)

## License

Internal project - not licensed for external use.

