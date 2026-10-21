# Vertical Adaptation Agent (VAA)

Transform a generic support chatbot into an industry-specialized assistant through versioned **context packs**. Each pack contains a persona, domain ontology, operating rules, escalation workflows, and compliance guardrails.

**Production-grade system:** AWS-native (Bedrock, S3, DynamoDB, CloudWatch), agentic pack generation, human-in-the-loop refinement, and real-time metrics tracking.

---

## Key Features

### 🤖 Agentic Pack Generation
- **Strands Agent + Bedrock Claude** - LLM builds complete context packs with deep industry knowledge
- **Reuse-first logic** - Discovers and reuses existing components before generating new ones
- **Balanced guardrails** - Blocks harmful actions, never blocks domain terms customers ask about
- **8000 token budget** - Prevents truncation, ensures quality output

### 📊 Before/After Comparison
- Side-by-side generic vs. adapted responses
- LLM evaluation across 5 dimensions: Groundedness, Compliance, Persona, Scope, Industry-scoped
- Real-time token usage & latency metrics
- Input/output token counts per response

### 🔐 AWS-Native Architecture
- **No local fallback** - Pure AWS services only
- **S3** - Context packs, industry docs, uploaded files
- **Bedrock** - Knowledge Bases (vector search), Guardrails, Prompt Management, Foundation Models
- **DynamoDB** - Session storage with TTL-based auto-expiration
- **CloudWatch** - Structured JSON logs with request tracing

### 💾 Multi-Turn Memory
- Session-based conversation history stored in DynamoDB
- Separate contexts for generic and adapted sides
- Auto-expiring sessions (30 days default)

---

## Architecture

```
┌─────────────────────┐
│   React + Vite      │  (http://localhost:5173)
│   Frontend UI       │  - Pack creation form
└──────────┬──────────┘  - Chat interface
           │            - Token/latency tracking
           │ HTTP
           ▼
┌─────────────────────────────────────────────────┐
│   FastAPI Backend (http://localhost:8000)       │
├─────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────┐   │
│  │  Pack Management                        │   │
│  │  - Generate (Strands Agent + Bedrock)  │   │
│  │  - Draft storage (S3)                  │   │
│  │  - Approve workflow (SSE stream)       │   │
│  └─────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────┐   │
│  │  Chat Agent                             │   │
│  │  - Generic response (no pack)           │   │
│  │  - Adapted response (pack + KB)         │   │
│  │  - RAG with Bedrock Knowledge Base      │   │
│  │  - Guardrails enforcement               │   │
│  └─────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────┐   │
│  │  Evaluation & Metrics                   │   │
│  │  - LLM-as-judge scoring                 │   │
│  │  - Token counting                       │   │
│  │  - Latency tracking                     │   │
│  └─────────────────────────────────────────┘   │
└──────────┬──────────┬──────────┬────────────────┘
           │          │          │
      AWS Services    │          │
           │          │          │
    ┌──────┴──────┬───┴──┬───────┴─────┐
    ▼             ▼      ▼             ▼
  S3           Bedrock  DynamoDB  CloudWatch
  (Storage)    (LLM,KB) (Sessions) (Logs)
```

---

## Repository Structure

```
Ingram-Vertical/
├── backend/                          # FastAPI application
│   ├── app/
│   │   ├── main.py                   # FastAPI routes (thin HTTP layer)
│   │   ├── models.py                 # Pydantic schemas
│   │   ├── config.py                 # AWS settings
│   │   │
│   │   ├── generator_agent.py       # Agentic pack builder (Strands + Bedrock)
│   │   ├── generator_tools.py       # Agent tools (reuse discovery)
│   │   ├── pack_builder.py          # Deterministic fallback builder
│   │   ├── drafts.py                # Draft storage & approval SSE
│   │   │
│   │   ├── agent.py                 # Chat agent (generic + adapted)
│   │   ├── compare.py               # Before/after comparison + metrics
│   │   ├── evaluator.py             # LLM-as-judge scoring
│   │   ├── sessions.py              # DynamoDB session storage
│   │   │
│   │   ├── aws/
│   │   │   ├── bedrock_client.py    # Bedrock Runtime + Agent Runtime
│   │   │   ├── bedrock_kb.py        # Managed Knowledge Base operations
│   │   │   ├── bedrock_resources.py # Guardrails & Prompts creation
│   │   │   ├── storage.py           # S3 pack catalog operations
│   │   │   ├── pieces.py            # Ontology merge & reuse discovery
│   │   │   └── s3_manager.py        # Low-level S3 operations
│   │   │
│   │   ├── middleware.py            # Request tracing, logging
│   │   └── logging_config.py        # CloudWatch JSON logging
│   │
│   ├── agent/                        # AgentCore deployment
│   │   ├── core/
│   │   │   └── generator/           # Generator code (mirrored from app/)
│   │   ├── runtime.py               # HTTP router for AgentCore
│   │   ├── service.py               # Business logic layer
│   │   └── Dockerfile               # Containerized deployment
│   │
│   ├── lambda/                       # Lambda deployment
│   │   └── [Lambda handler code]    # Serverless function variants
│   │
│   ├── deploy/                       # Deployment configs
│   │   ├── iam-policies/            # IAM policy definitions
│   │   └── [deployment scripts]
│   │
│   ├── requirements.txt
│   ├── .env.example
│   └── README.md                     # Backend-specific guide
│
├── frontend/                         # React + Vite UI
│   ├── src/
│   │   ├── App.jsx                  # Main UI component
│   │   ├── main.jsx                 # Entry point
│   │   ├── data.js                  # API client helper
│   │   └── styles.css               # Styling
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   └── README.md                     # Frontend-specific guide (if needed)
│
├── documents/                        # Architecture docs
├── .gitignore
└── README.md                         # This file
```

---

## Workflow: Create → Generate → Review → Approve → Chat

### 1️⃣ Create a Pack
**User provides:**
- Industry (required) - e.g., "Banking", "Healthcare"
- Name (required) - e.g., "support", "agent-v2"
- Use case (required) - e.g., "Customer support for retail banking"
- Optional: Persona hints, instructions, guardrails notes, KB files

**Duplicate check:** Case-insensitive name check prevents "support" and "Support" in same industry.

### 2️⃣ Generate (Agentic)
**Backend executes:**
1. **List existing pieces** - Discover reusable ontology, prompts, guardrails
2. **Call agent tools** - `list_existing_pieces`, `get_existing_piece`
3. **Generate pack** - Agent builds complete JSON (persona, ontology, rules, escalation, guardrails)
4. **Return draft** - Pack shown in UI for human review (not saved to S3 yet)

**Agent settings:**
- Model: Claude Haiku (Bedrock)
- Max tokens: 8000 (increased from 5000 to prevent truncation)
- System prompt: Guides generation with industry knowledge, reuse-first logic
- Tools: Reuse discovery only (validation tools removed)

**Fallback:** If agent hits errors, deterministic builder creates generic pack.

### 3️⃣ Review & Edit
**User can:**
- Edit persona, scope, domain prompts
- Add/remove ontology terms
- Adjust escalation rules
- Modify guardrails

**Real-time validation:**
- Check persona completeness
- Validate guardrail structure
- Highlight gaps

**New feature:** Button changes to "Generate New Version" when name exists (allows v2, v3, etc.).

### 4️⃣ Approve (Server-Sent Events Stream)
**Approval executes 4 steps, streaming progress:**

1. **S3 Storage** - Save pack, merge industry ontology, assign version number
2. **Bedrock Guardrail** - Create guardrail resource with denied topics, blocked phrases, PII blocks
3. **Knowledge Base** - Ingest uploaded files into managed Bedrock KB (non-fatal if fails)
4. **Prompt Management** - Create versioned prompt resource

**Output:** Final pack with resource IDs persisted to S3.

### 5️⃣ Chat & Compare
**Ask a question:**
```
User: "How do I dispute a transaction?"
```

**System returns:**
```json
{
  "generic": "A transaction dispute is a request...",
  "adapted": "I can help you dispute a transaction. According to Regulation E, you have 60 days to report an unauthorized transaction...",
  "evaluation": {
    "generic": { "groundedness": 4, "compliance": 2, "persona": 2, "scope": 3, "industry_aligned": 2 },
    "adapted": { "groundedness": 5, "compliance": 5, "persona": 5, "scope": 5, "industry_aligned": 5 }
  },
  "metrics": {
    "generic_input_tokens": 1250,
    "generic_output_tokens": 380,
    "adapted_input_tokens": 3400,
    "adapted_output_tokens": 420,
    "generic_latency_ms": 1800,
    "adapted_latency_ms": 2200
  }
}
```

**Multi-turn:** Each session stores separate conversation histories for generic and adapted sides.

---

## API Endpoints

### Health & Metadata
```
GET /api/health
→ {"status": "ok", "service": "Vertical Adaptation Agent"}

GET /api/industries
→ [{"id": "banking", "label": "Banking"}, ...]

GET /api/context-packs/stages
→ ["Generating...", "Validating...", "Approved"]
```

### Pack Management
```
POST /api/context-packs/check-name
Body: {"industry": "Banking", "name": "support"}
→ {"exists": false}

POST /api/context-packs/generate
Body: {
  "industry": "Banking",
  "name": "support",
  "use_case": "Customer support for retail banking",
  "persona_hint": "Professional and empathetic",
  "dynamic": true
}
→ DraftResponse (pack + system_prompt)

PUT /api/context-packs/{draft_id}/draft
Body: UpdateDraftRequest (edited pack)
→ {pack, system_prompt, gaps}

POST /api/context-packs/{draft_id}/approve
→ SSE Stream:
  {"step": "s3", "status": "in_progress"}
  {"step": "s3", "status": "succeeded"}
  {"step": "guardrail", "status": "in_progress"}
  ...
  {"step": "done", "status": "succeeded", "pack": {...}}

DELETE /api/context-packs/{draft_id}
→ {"deleted": true}
```

### Reading Packs
```
GET /api/context-packs?industry=Banking&agent_name=support
→ [PackVersionItem, ...]

GET /api/agent-names?industry=Banking
→ ["support", "vip-support"]

GET /api/context-packs/support-v1
→ ContextPack JSON

GET /api/context-packs/support-v1/system-prompt
→ {"generic": "...", "adapted": "..."}
```

### Chat
```
POST /api/chat/session
Body: {"pack_id": "support-v1"}
→ {"session_id": "sess_abc123"}

GET /api/chat/sessions
→ [{"session_id": "...", "pack_id": "...", "created_at": "..."}]

POST /api/chat/compare
Body: {"session_id": "sess_abc123", "question": "How do I dispute?"}
→ CompareResponse (generic + adapted + evaluation + metrics)

DELETE /api/chat/session/{session_id}
→ {"cleared": true}
```

---

## AWS Services Used

| Service | Purpose | Scoped By |
|---------|---------|-----------|
| **S3** | Context packs, industry docs, uploaded KB files | Industry + Usecase |
| **Bedrock** | LLM (Claude), Knowledge Base, Guardrails, Prompts | Per Industry |
| **DynamoDB** | Chat session storage with TTL | Global (auto-expiring) |
| **CloudWatch** | Structured JSON logs, request tracing | Region |
| **IAM** | KB execution role (self-provisioned) | Account |

**Resource IDs persisted** in `pack.integrations`:
```python
guardrail_id: str         # Bedrock Guardrail ID
guardrail_version: str    # Guardrail version (e.g., "1")
knowledge_base_id: str    # Bedrock KB ID
prompt_id: str            # Bedrock Prompt ID
```

---

## Deployment

### Local Development
```bash
# Backend
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Set AWS credentials
python -m uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev  # Runs on http://localhost:5173
```

### Production (AgentCore)
Located in `backend/agent/`:
```bash
cd backend/agent
docker build -t vaa-agent .
docker push <ecr-repo>/vaa-agent:latest
# Deploy via ECS or similar
```

### Serverless (Lambda)
Located in `backend/lambda/`:
- Lambda function handler
- API Gateway integration
- Environment variables via Systems Manager

### Infrastructure
Located in `backend/deploy/`:
- IAM policies
- S3 bucket configuration
- DynamoDB table setup
- Bedrock resource provisioning

---

## Configuration

### Backend `.env`
```bash
# AWS
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
S3_BUCKET=vertical-adaption-agent
CONTEXT_PACK_PREFIX=context-pack/
INDUSTRY_DOCS_PREFIX=industry-docs/

# DynamoDB
SESSIONS_TABLE_NAME=vaa-sessions
SESSION_TTL_DAYS=30

# Bedrock
KB_ROLE_NAME=vaa-bedrock-kb-role

# Server
API_HOST=0.0.0.0
API_PORT=8000
CORS_ORIGINS=http://localhost:5173
```

### Getting Started with AWS
1. **Enable Bedrock models** - AWS Console → Bedrock → Model access (request Claude)
2. **Configure credentials** - `aws configure` or `aws sso login`
3. **Create S3 bucket** - `aws s3 mb s3://vertical-adaption-agent`
4. **Set region** - Must support Bedrock, Knowledge Bases, Guardrails
5. **Restart backend** - DynamoDB table + IAM role auto-provisioned on first request

---

## Token & Latency Metrics

**Per response:**
```json
{
  "generic_input_tokens": 1250,      // Tokens sent to model
  "generic_output_tokens": 380,      // Tokens generated
  "adapted_input_tokens": 3400,      // RAG context increases input
  "adapted_output_tokens": 420,      // Adapted response output
  "generic_latency_ms": 1800,        // Time to Bedrock + back
  "adapted_latency_ms": 2200,        // Including KB search
  "generic_duration_ms": 2100,       // Total (network + server)
  "adapted_duration_ms": 4500        // Total (network + server)
}
```

**Cost estimate (Claude Haiku on Bedrock):**
- Input: $0.003 per 1K tokens
- Output: $0.015 per 1K tokens
- **Per pack generation:** ~$0.06 (with 8000 token limit)

---

## Recent Changes

**Version 1.3 (Oct 2026):**
- ✅ Increased agent token budget: 5000 → 8000 (prevents truncation)
- ✅ Removed validation tools from agent tools list (saves tokens)
- ✅ Fixed name checking: Case-insensitive, allows duplicate names for versioning
- ✅ UX improvement: Button changes to "Generate New Version" when name exists
- ✅ Removed .env and __pycache__ from git tracking
- ✅ Integrated deployment code (AgentCore, Lambda, Deploy)
- ✅ Separated validation: Only happens on user edits, not during generation

**Version 1.2:**
- ✅ Fixed guardrail overblocking (separated actions from domain terms)
- ✅ Improved ontology: Skip basic terms, focus on industry-specific
- ✅ Reduced escalation rules: 18 → 6 consolidated
- ✅ Added token/latency metrics to compare endpoint

**Version 1.1:**
- ✅ AWS-only architecture (removed local fallback)
- ✅ Added LLM-as-judge evaluation
- ✅ Multi-turn session memory

---

## Known Limitations

- **No concurrent KB ingestion** - One pack approval at a time
- **No pack deletion** - Versioned, not deleted from S3
- **Session cleanup** - Manual or DynamoDB TTL (30 days default)
- **Token metrics** - Require Strands/Bedrock metadata (may vary)

---

## License

Internal project.


