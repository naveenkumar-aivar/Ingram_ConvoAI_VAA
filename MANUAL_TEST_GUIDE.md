# Manual Testing Guide - Local Development

## Quick Start (2 terminals)

### Terminal 1: Backend
```bash
cd /Users/naveenkumarc/Ingram-Vertical/backend
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

**Expected output:**
```
INFO:     Will watch for changes in these directories: ['/Users/naveenkumarc/Ingram-Vertical/backend']
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

### Terminal 2: Frontend
```bash
cd /Users/naveenkumarc/Ingram-Vertical/frontend
npm run dev -- --port 5173 --strictPort
```

**Expected output:**
```
  VITE v5.4.21  ready in XXX ms

  ➜  Local:   http://localhost:5173/
  ➜  press h to show help
```

Open browser: **http://localhost:5173**

---

## Test Flow

### Test 1: Chat (Before/After Comparison)
1. **Select industry:** Banking (sidebar, top)
2. **Select agent:** support-v1
3. **Type question:** "What's the procedure for a wire transfer?"
4. **Expected:**
   - ✅ Left panel (generic): Generic banking answer
   - ✅ Right panel (adapted): Banking-specific answer
   - ✅ Response time shown (milliseconds) under each answer
   - ✅ Evaluation scores shown on adapted side (if evaluator enabled)
   - ✅ Before/after bars for 5 metrics

---

### Test 2: Create Context Pack
1. **Click:** "Create a Context Pack"
2. **Fill form:**
   - Industry: `Retail` (or any new industry)
   - Name: `checkout-v2`
   - Use case: `Help customers with checkout, returns, and refunds.`
   - (optional) Upload a knowledge base file (PDF/TXT)
3. **Click:** "Generate with agent"
4. **Wait:** Progress bar shows generation stages
5. **Review page:** Shows generated pack
6. **Click:** "Approve & save"
7. **Expected:**
   - ✅ Pack saved successfully
   - ✅ Confirmation: "Saved retail-checkout-v2 (v1)"
   - ✅ File appears in `s3-data/context-pack/Retail/checkout-v2/v1.json`

---

### Test 3: Verify S3 Storage (Local)

**After creating a pack, verify in Terminal (new tab):**

```bash
# Check if pack was saved
ls -la /Users/naveenkumarc/Ingram-Vertical/s3-data/context-pack/

# Expected output:
# drwxr-xr-x  Banking/
# drwxr-xr-x  Healthcare/
# drwxr-xr-x  Retail/

# View the saved pack
cat /Users/naveenkumarc/Ingram-Vertical/s3-data/context-pack/Retail/checkout-v2/v1.json | head -50

# Expected: JSON with meta, persona, scope, ontology, escalation_rules, guardrails
```

---

### Test 4: Multi-Turn Chat (Memory)
1. **Start new chat**
2. **Q1:** "What's your return policy?"
3. **Q2:** "Can I return it after 30 days?"
4. **Q3:** "What if the item is damaged?"
5. **Expected:**
   - ✅ Each response builds on previous context
   - ✅ Chat history saved in `s3-data/sessions/{session-id}.json`
   - ✅ Conversation continues across page refresh

---

### Test 5: Knowledge Base Upload (Optional)
1. **Create pack form** → check "Add optional overrides"
2. **Upload a file:** `my-faq.txt` or `policies.pdf`
3. **Submit for generation**
4. **Expected:**
   - ✅ File uploaded to `s3-data/uploaded-data/{industry}/{name}/my-faq.txt`
   - ✅ File chunked into Chroma vector DB
   - ✅ Ontology terms retrieve from KB during chat

---

## Configuration

### Backend (.env)

**For local testing (use these):**
```env
MOCK_MODE=true              # Use local s3-data folder
LLM_PROVIDER=bedrock        # Uses Claude Haiku (deterministic)
AWS_REGION=us-east-1        # Required but not used in mock mode
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
S3_BUCKET=vertical-adaption-agent
LOCAL_S3_DIR=../s3-data     # Local folder simulating S3
```

**For AWS testing (later):**
```env
MOCK_MODE=false             # Use real AWS S3
LLM_PROVIDER=bedrock
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
S3_BUCKET=vertical-adaption-agent  # Real S3 bucket
```

---

## File Locations to Check

### S3 Storage (Local)
```
s3-data/
├── context-pack/              ← Generated packs
│   ├── Banking/support/v1.json
│   ├── Healthcare/assist/v1.json
│   └── Retail/checkout-v2/v1.json  ← Your new pack
├── uploaded-data/             ← Knowledge base files (raw)
│   └── Retail/checkout-v2/my-faq.txt
├── sessions/                  ← Chat history
│   └── {session-id}.json
└── _chroma/                   ← Vector DB (embedded)
    └── hnswlib_data/
```

### Backend Logs
```bash
# Terminal output shows:
# - Request logging (method, path, duration)
# - LLM calls (bedrock/groq)
# - S3 operations (save_pack, get_pack)
# - Errors (if any)

# JSON format:
{"timestamp": "...", "level": "INFO", "logger": "app.main", "message": "...", "request_id": "abc1d2e3"}
```

---

## Troubleshooting

### Issue: 500 Error on Chat
**Cause:** Pack not found or agent failed  
**Fix:** Check backend logs, ensure pack ID is correct

### Issue: Files not appearing in s3-data
**Cause:** `MOCK_MODE=false` (sending to AWS instead)  
**Fix:** Set `MOCK_MODE=true` in .env and restart backend

### Issue: Chat takes >5 seconds
**Cause:** Evaluator running (LLM judge call)  
**Fix:** This is normal; evaluation adds 1-3s per response

### Issue: CORS error in browser
**Cause:** Frontend/backend ports mismatch  
**Fix:** Frontend at 5173, backend at 8000. Check CORS_ORIGINS in .env

### Issue: "Pack not found" error
**Cause:** Pack not generated/approved yet  
**Fix:** Go to "Create a Context Pack", generate, approve, then retry chat

---

## What to Look For

### Backend Console (Success)
```json
{"timestamp": "2026-10-16T...", "level": "INFO", "logger": "app.middleware", "message": "POST /api/chat/compare 200", "duration_ms": 1240}
{"timestamp": "2026-10-16T...", "level": "INFO", "logger": "app.compare", "message": "Compare: What's the procedure...", "generic_duration_ms": 600, "adapted_duration_ms": 640}
```

### Frontend UI (Success)
- ✅ Two chat panels side-by-side
- ✅ "Generic" panel (left) vs "Context-based" panel (right)
- ✅ Markdown rendering (bold, lists, tables)
- ✅ Response time badge: "Response time: 640ms"
- ✅ Evaluation scores (5 metrics with before/after)

### S3 Storage (Success)
```bash
$ cat s3-data/context-pack/Retail/checkout-v2/v1.json
{
  "meta": {"industry": "Retail", "pack_id": "checkout-v2", "version": "v1", "status": "active"},
  "persona": {"name": "...", "role": "...", ...},
  "ontology": [...],
  "escalation_rules": [...],
  ...
}
```

---

## Lean/Low-Token Notes

✅ **Why this setup uses fewer tokens:**
- Bedrock deterministic builder (no agent loops for default packs)
- Web search disabled by default
- Minimal logging (WARNING level, quiet console)
- Evaluator optional (only runs if pack_id set)
- Groq/Bedrock Haiku (faster, cheaper models)

✅ **Token usage per request:**
- Chat compare: ~500-1000 tokens (2 LLM calls: generic + adapted)
- Pack generation: ~2000-3000 tokens (deterministic builder)
- Evaluation: +1000 tokens (if enabled, optional)
- **Total per workflow:** ~3500-5000 tokens max

---

## Next Steps After Manual Testing

1. ✅ Verify chat works with metrics
2. ✅ Create 2-3 new packs (different industries)
3. ✅ Test knowledge base upload
4. ✅ Check S3 storage structure
5. 🔄 Switch to AWS mode: Set `MOCK_MODE=false` + ensure AWS credentials
6. 🚀 Deploy to AWS: Container + CloudWatch logging
