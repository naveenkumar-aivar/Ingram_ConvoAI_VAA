// Data layer / backend seam. All values come from the FastAPI backend
// (proxied at /api), which reads the real context packs from s3-data.
// No mock fallback — if a call fails it throws, so problems are visible
// rather than hidden behind dummy data.
//
//   listIndustries()            -> GET  /api/industries
//   listVersions(industry)      -> GET  /api/context-packs?industry=
//   getPack(packId)             -> GET  /api/context-packs/{packId}
//   getSystemPrompt(packId)     -> GET  /api/context-packs/{packId}/system-prompt
//   genStages()                 -> GET  /api/context-packs/stages
//   generatePack(form)          -> POST /api/context-packs/generate
//   compare(question, packId)   -> POST /api/chat/compare

const API = '/api'

async function get(path) {
  const r = await fetch(`${API}${path}`)
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status}`)
  return r.json()
}
async function post(path, body) {
  const r = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`POST ${path} -> ${r.status}`)
  return r.json()
}

export async function listIndustries() {
  return get('/industries')
}

export async function listVersions(industry) {
  return get(`/context-packs?industry=${encodeURIComponent(industry)}`)
}

export async function getPack(packId) {
  if (!packId) return null
  return normalizePack(await get(`/context-packs/${encodeURIComponent(packId)}`))
}

export async function getSystemPrompt(packId) {
  if (!packId) return null
  return get(`/context-packs/${encodeURIComponent(packId)}/system-prompt`)
}

export async function genStages() {
  return get('/context-packs/stages')
}

// ---- Create a Context Pack (draft -> approve flow) ----

function formToBody(form) {
  return {
    industry: form.industry,
    use_case: form.useCase || '',
    name: form.name || '',
    persona_hint: form.persona || '',
    instructions: form.instructions || '',
    guardrails: form.guardrails || '',
    handoff: form.handoff || '',
    kb_file_names: form.kbFileNames || [],
    dynamic: form.dynamic !== false,
  }
}

export async function checkName(form) {
  const r = await post('/context-packs/check-name', formToBody(form))
  return r.exists
}

// Upload the actual KB files (bytes) for (industry, name). Returns the stored
// filenames. Called before generate so the pack can reference them.
export async function uploadKb(industry, name, fileList) {
  if (!fileList || fileList.length === 0) return []
  const fd = new FormData()
  fd.append('industry', industry)
  fd.append('name', name)
  for (const f of fileList) fd.append('files', f)
  const r = await fetch(`${API}/context-packs/upload-kb`, { method: 'POST', body: fd })
  if (!r.ok) throw new Error(`upload-kb -> ${r.status}`)
  const data = await r.json()
  return data.saved || []
}

// Returns the generated DRAFT (not yet saved): { draft_id, pack, reused,
// generated, provider, system_prompt }.
export async function generateDraft(form) {
  return post('/context-packs/generate', formToBody(form))
}

export async function approveDraft(draftId) {
  return post(`/context-packs/${encodeURIComponent(draftId)}/approve`, {})
}

export async function rejectDraft(draftId) {
  if (!draftId) return
  await fetch(`${API}/context-packs/${encodeURIComponent(draftId)}`, { method: 'DELETE' })
}

export async function newSession() {
  const r = await post('/chat/session', {})
  return r.session_id
}

export async function recentSessions() {
  try { return await get('/chat/sessions') } catch { return [] }
}

export async function sessionHistory(sessionId) {
  if (!sessionId) return null
  try { return await get(`/chat/session/${encodeURIComponent(sessionId)}`) } catch { return null }
}

export async function resetSession(sessionId) {
  if (!sessionId) return
  await fetch(`${API}/chat/session/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

export async function compare(question, packId, industry, sessionId) {
  return post('/chat/compare', {
    question,
    pack_id: packId || null,
    industry: industry || null,
    session_id: sessionId || null,
  })
}

// Flatten the backend ContextPack into what the right-bar renders.
function normalizePack(p) {
  if (!p) return null
  return {
    industry: p.meta?.industry ?? '',
    version: p.meta?.version ?? '',
    status: p.meta?.status ?? 'active',
    packId: p.meta?.pack_id ?? '',
    persona: {
      name: p.persona?.name ?? '',
      role: p.persona?.role ?? '',
      tone: p.persona?.tone ?? '',
    },
    escalations: (p.escalation_rules ?? []).map((e) => ({
      trigger: e.trigger, to: e.escalate_to ?? e.to, priority: e.priority ?? 'Medium',
    })),
  }
}
