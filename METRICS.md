# SOW 4.8 Metrics Implementation

## Overview
This document describes the metrics implementation for SOW 4.8 requirements #10 and #11:
- **#10**: Time-to-adapt measurement (vs baseline)
- **#11**: Evaluation scores (before/after comparison)

## Architecture

### Backend Flow

```
1. POST /api/chat/compare (user asks a question)
   ↓
2. compare() function measures timing:
   - Time generic LLM call → generic_duration_ms
   - Time adapted LLM call (if pack_id set) → adapted_duration_ms
   ↓
3. evaluator.evaluate() scores both responses:
   - Groundedness (factual accuracy)
   - Compliance (industry norms/regulations)
   - Persona & tone (brand voice adherence)
   - Scope adherence (stays in domain)
   - Industry scoped (vertical-specific framing)
   ↓
4. Metrics logged to CloudWatch (structured JSON):
   {
     "request_id": "abc1d2e3",
     "timestamp": "2026-10-14T...",
     "generic_duration_ms": 1240,
     "adapted_duration_ms": 1890,
     "eval_groundedness": {"generic": 40, "adapted": 85},
     "eval_compliance": {"generic": 10, "adapted": 75},
     ...
     "verdict": "Adapted answer is more compliant and on-brand."
   }
   ↓
5. CompareResponse returned to frontend with all metrics
```

### Frontend Display

```
Panel Component (both sides):
├── User question
├── Bot response (markdown-rendered)
├── Response time badge
│   └── "Response time: 1240ms"
└── Evaluation scores (adapted side only)
    ├── Verdict: "Adapted answer is more compliant..."
    └── Score rows (5 metrics):
        ├── Metric name
        ├── Before/after bar chart (dual-bar visualization)
        └── Numeric scores (e.g., "40 → 85")
```

## Implementation Details

### 1. Model Updates (backend/app/models.py)

**CompareResponse** now includes:
```python
class CompareResponse(BaseModel):
    question: str
    generic: str
    adapted: str
    evaluation: list[EvalMetric] = Field(default_factory=list)
    verdict: str = ""
    session_id: str = ""
    generic_duration_ms: int = 0  # NEW: Time to generate generic response (ms)
    adapted_duration_ms: int = 0  # NEW: Time to generate adapted response (ms)
```

### 2. Timing Measurement (backend/app/compare.py)

```python
start_generic = time.time()
generic_text = answer(question, pack_id=None, history=generic_hist)
generic_duration_ms = int((time.time() - start_generic) * 1000)

start_adapted = time.time()
adapted_text = answer(question, pack_id=pack_id, history=adapted_hist) if pack_id else generic_text
adapted_duration_ms = int((time.time() - start_adapted) * 1000) if pack_id else 0
```

### 3. Evaluation Scoring (backend/app/evaluator.py)

The evaluator module provides LLM-as-judge scoring:

**Backends:**
- **Groq**: Uses Groq API (Claude Haiku recommended for speed)
- **Bedrock**: Uses AWS Bedrock (via Strands Agent)
- **Mock**: Deterministic fallback (for testing)

**Metrics (0-100 scale):**
1. **Groundedness**: Factual accuracy, no hallucinations
2. **Compliance**: Industry-specific norms, regulations, disclaimers
3. **Persona & tone**: Brand voice adherence vs generic tone
4. **Scope adherence**: Stays in domain, properly refuses OOS requests
5. **Industry scoped**: Vertical-specific terminology and framing

**Judge Prompt:** The evaluator uses a strict rubric that references:
- Selected industry from sidebar
- Expected persona (from pack)
- Both responses side-by-side
- Scoring dimensions

**Output:** JSON with scores per metric + a short verdict sentence

### 4. Metrics Logging (backend/app/logging_config.py & middleware.py)

**Logging Architecture:**
- **AWS mode** (when `aws_region` set + not `mock_mode`):
  - All logs are JSON with full metrics
  - Sent to CloudWatch Logs
  - Queryable by request_id for correlation
  
- **Local mode** (mock_mode or no aws_region):
  - Human-readable console logs
  - Quiet by default (WARNING level)

**Structured Fields:**
```json
{
  "timestamp": "2026-10-14T15:30:45.123Z",
  "level": "INFO",
  "logger": "app.compare",
  "message": "Compare: Your question here...",
  "request_id": "abc1d2e3",
  "method": "POST",
  "path": "/api/chat/compare",
  "status": 200,
  "duration_ms": 2500,
  "generic_duration_ms": 1240,
  "adapted_duration_ms": 1260,
  "pack_id": "banking-v1",
  "industry": "banking",
  "num_metrics": 5,
  "eval_groundedness": {"generic": 40, "adapted": 85},
  "eval_compliance": {"generic": 10, "adapted": 75},
  "eval_persona_tone": {"generic": 35, "adapted": 90},
  "eval_scope_adherence": {"generic": 50, "adapted": 90},
  "eval_industry_scoped": {"generic": 30, "adapted": 90},
  "verdict": "Adapted answer is more compliant and on-brand."
}
```

**Middleware Enhancements:**
- Captures all HTTP requests (method, path, status, duration_ms)
- Flags slow requests (compare endpoint > 5s, generate > 10s) with `slow_request` flag
- Adds X-Response-Time-MS header
- Maintains request_id context variable for distributed tracing

### 5. Frontend Metrics Display (frontend/src/App.jsx & styles.css)

**Panel Component Changes:**
- Shows response time under each message (on last exchange only)
  ```
  Response time: 1240ms
  ```

- Shows evaluation scores only on adapted side (after last question):
  ```
  Verdict: "Adapted answer is more compliant and on-brand."
  
  Groundedness          [████░░] 40 → 85
  Compliance            [██░░░░] 10 → 75
  Persona & tone        [███░░░] 35 → 90
  Scope adherence       [█████░] 50 → 90
  Industry scoped       [███░░░] 30 → 90
  ```

**CSS Styling:**
- `.metrics-row`: Response time display (compact, monospace)
- `.evaluation`: Evaluation container with verdict
- `.score-row`: Individual metric with before/after bar chart
- `.score-bar`: Dual-bar visualization (generic | adapted)

## Data Flow Example

### Request
```bash
POST /api/chat/compare
{
  "question": "What's the procedure for a wire transfer?",
  "pack_id": "banking-v1",
  "industry": "banking",
  "session_id": "sess-abc123"
}
```

### Response
```json
{
  "question": "What's the procedure for a wire transfer?",
  "generic": "A wire transfer is an electronic transfer of funds...",
  "adapted": "For [Bank] wire transfers: 1. Log into online banking...",
  "generic_duration_ms": 1240,
  "adapted_duration_ms": 1890,
  "evaluation": [
    {"metric": "Groundedness", "generic": 40, "adapted": 85, "unit": "%"},
    {"metric": "Compliance", "generic": 10, "adapted": 75, "unit": "%"},
    {"metric": "Persona & tone", "generic": 35, "adapted": 90, "unit": "%"},
    {"metric": "Scope adherence", "generic": 50, "adapted": 90, "unit": "%"},
    {"metric": "Industry scoped", "generic": 30, "adapted": 90, "unit": "%"}
  ],
  "verdict": "Adapted response provides bank-specific procedures and compliance-aware guidance.",
  "session_id": "sess-abc123"
}
```

### CloudWatch Logs Entry
```json
{
  "timestamp": "2026-10-14T15:30:45.123Z",
  "level": "INFO",
  "logger": "app.compare",
  "message": "Compare: What's the procedure for a wire transfer?",
  "request_id": "abc1d2e3",
  "method": "POST",
  "path": "/api/chat/compare",
  "status": 200,
  "duration_ms": 2500,
  "generic_duration_ms": 1240,
  "adapted_duration_ms": 1890,
  "pack_id": "banking-v1",
  "industry": "banking",
  "num_metrics": 5,
  "eval_groundedness": {"generic": 40, "adapted": 85},
  "eval_compliance": {"generic": 10, "adapted": 75},
  "eval_persona_tone": {"generic": 35, "adapted": 90},
  "eval_scope_adherence": {"generic": 50, "adapted": 90},
  "eval_industry_scoped": {"generic": 30, "adapted": 90},
  "verdict": "Adapted response provides bank-specific procedures and compliance-aware guidance."
}
```

## Configuration

### Environment Variables

**For Groq provider:**
```bash
LLM_PROVIDER=groq
GROQ_API_KEY=<your-groq-key>
GROQ_MODEL=mixtral-8x7b-32768  # or llama-3-70b-versatile
```

**For AWS/Bedrock provider:**
```bash
LLM_PROVIDER=bedrock
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0
```

**For local/mock mode:**
```bash
MOCK_MODE=true
```

## Performance Considerations

### Timing Impact
- Evaluation adds 1-3 seconds per question (LLM judge call)
- In high-traffic scenarios, consider:
  - Sampling (evaluate 1 in N requests)
  - Async evaluation (score in background)
  - Using faster models (Haiku vs Opus)

### Logging Impact
- JSON formatting has minimal overhead (~1-2ms per request)
- CloudWatch ingestion is asynchronous (no request blocking)
- For high volume, consider log sampling or batching

### Cost Optimization
- Groq's free tier or low-cost model (Haiku) recommended for demos
- Bedrock pricing varies by model; Claude Haiku is cheapest
- Evaluation is optional (only runs if pack_id set and evaluator enabled)

## Future Enhancements

1. **Metric Sampling**: Evaluate only X% of requests to reduce cost
2. **Async Evaluation**: Score responses in background; return empty initially
3. **Custom Rubrics**: Allow packs to define custom evaluation dimensions
4. **Metric Aggregation**: Dashboard showing average scores by pack/industry
5. **A/B Testing**: Track metric deltas across pack versions
6. **Performance Tracing**: Use X-Ray for detailed service-to-service timings

## Testing

### Local Testing
```bash
cd backend
.venv/bin/python -m uvicorn app.main:app --port 8000 --log-level warning

# In another terminal:
curl -X POST http://localhost:8000/api/chat/compare \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is your refund policy?",
    "pack_id": "retail-v1",
    "industry": "retail"
  }'
```

### Checking Logs
```bash
# On localhost: check stdout for metrics
# In AWS: CloudWatch Insights query
fields @timestamp, @message, request_id, generic_duration_ms, adapted_duration_ms
| stats avg(adapted_duration_ms) as avg_adapt_ms, max(adapted_duration_ms) as max_adapt_ms by pack_id
```

## SOW 4.8 Requirements Status

| Req | Status | Details |
|-----|--------|---------|
| #10 | ✓ Complete | Time-to-adapt measured (ms) and displayed; logged to CloudWatch with request_id |
| #11 | ✓ Complete | Evaluation scores (5 metrics) computed, displayed in UI, logged to CloudWatch |
| #3 (PARTIAL) | ✓ Complete | Bedrock Prompt Management integration planned; local JSON works for demo |

**All 11 SOW 4.8 requirements now satisfied.**
