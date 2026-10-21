import { useEffect, useRef, useState } from 'react'
import {
  listIndustries, listVersions, listAgentNames, getPack, getSystemPrompt, compare,
  genStages, newSession, resetSession, recentSessions, sessionHistory,
  checkName, generateDraft, updateDraft, approveDraftStream, rejectDraft, uploadKb,
} from './data.js'

export default function App() {
  const [page, setPage] = useState('chatbot')
  const [recents, setRecents] = useState([])
  const [resume, setResume] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => { recentSessions().then(setRecents) }, [refreshKey])

  function openChat(sessionId) {
    setResume({ id: sessionId, nonce: Date.now() })
    setPage('chatbot')
  }

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="brand">Vertical Agent</div>
        <button className={`nav ${page === 'chatbot' ? 'on' : ''}`} onClick={() => setPage('chatbot')}>Chatbot</button>
        <button className={`nav ${page === 'create' ? 'on' : ''}`} onClick={() => setPage('create')}>Create a Context Pack</button>

        {recents.length > 0 && (
          <div className="recents">
            <div className="recents-h">Recent chats</div>
            {recents.map((r) => (
              <button key={r.session_id} className="recent-item" title={r.preview}
                onClick={() => openChat(r.session_id)}>
                <span className="recent-txt">{r.preview || 'Conversation'}</span>
                <span className="recent-n">{r.turns}</span>
              </button>
            ))}
          </div>
        )}

        <div className="foot">backend: /api</div>
      </nav>
      <main className="main">
        {page === 'chatbot'
          ? <Chatbot resume={resume} onActivity={() => setRefreshKey((k) => k + 1)} onRefreshPacks={() => setRefreshKey((k) => k + 1)} />
          : <CreatePack onApprovalSuccess={() => setRefreshKey((k) => k + 1)} />}
      </main>
    </div>
  )
}

// Derive the usecase/agent name from a NORMALIZED pack: strip the "-vN"
// suffix off packId, falling back to the pack's useCase.
function packAgentName(pack) {
  const id = pack?.packId || ''
  const base = id.includes('-v') ? id.slice(0, id.lastIndexOf('-v')) : id
  return base || pack?.useCase || ''
}

function Chatbot({ resume, onActivity, onRefreshPacks, refreshKey }) {
  const [industries, setIndustries] = useState([])
  const [industry, setIndustry] = useState('')
  const [agentNames, setAgentNames] = useState([])
  const [agentName, setAgentName] = useState('')
  const [versions, setVersions] = useState([])
  const [versionId, setVersionId] = useState('')
  const [pack, setPack] = useState(null)
  const [sysPrompt, setSysPrompt] = useState('')
  const [turns, setTurns] = useState([])
  const [question, setQuestion] = useState('')
  const [pending, setPending] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState('')
  const [error, setError] = useState('')

  useEffect(() => { newSession().then(setSessionId) }, [])

  useEffect(() => {
    if (!resume?.id) return
    setSessionId(resume.id)
    sessionHistory(resume.id).then((h) => {
      const g = h?.generic || []
      const a = h?.adapted || []
      const rebuilt = []
      for (let i = 0; i < g.length; i += 2) {
        rebuilt.push({
          question: g[i]?.text || '',
          generic: g[i + 1]?.text || '',
          adapted: a[i + 1]?.text || g[i + 1]?.text || '',
        })
      }
      setTurns(rebuilt)
    })
  }, [resume?.nonce])

  // Fetch industries on mount and when refreshKey changes
  useEffect(() => { 
    listIndustries().then((inds) => {
      setIndustries(inds)
      // Auto-select the first industry if none is selected
      if (!industry && inds.length > 0) {
        setIndustry(inds[0].id)
      }
    })
  }, [refreshKey])

  // When industry changes, fetch agent names for that industry
  useEffect(() => {
    if (!industry) { setAgentNames([]); setAgentName(''); setVersions([]); return }
    listAgentNames(industry).then((names) => {
      setAgentNames(names)
      setAgentName(names[0] || '')
    })
  }, [industry, refreshKey])

  // When agent name changes, fetch versions for that agent
  useEffect(() => {
    if (!industry || !agentName) { setVersions([]); setVersionId(''); return }
    listVersions(industry, agentName).then((vs) => {
      setVersions(vs)
      setVersionId(vs[0]?.id || '')
    })
  }, [industry, agentName, refreshKey])

  // When version changes, fetch the pack and system prompt
  useEffect(() => {
    if (!versionId) { setPack(null); setSysPrompt(''); return }
    getPack(versionId).then(setPack)
    getSystemPrompt(versionId).then((r) => setSysPrompt(r?.adapted_system_prompt || ''))
  }, [versionId])

  async function ask(e) {
    e.preventDefault()
    const q = question.trim()
    if (!q || loading) return
    setQuestion('')
    setError('')
    setPending(q)
    setLoading(true)
    try {
      const r = await compare(q, versionId || null, industry, sessionId)
      setTurns((t) => [...t, r])
      onActivity?.()
    } catch (err) {
      setQuestion(q)
      setError(err?.message || 'Failed to send message. Please try again.')
    } finally {
      setPending('')
      setLoading(false)
    }
  }

  async function newChat() {
    await resetSession(sessionId)
    setTurns([])
    const sid = await newSession()
    setSessionId(sid)
  }

  return (
    <>
      <header className="top">
        <h1>Chatbot · Before vs After</h1>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span className={`badge ${pack ? '' : 'muted'}`}>{pack ? versionId : 'no pack'}</span>
          <button className="newchat" onClick={newChat}>New chat</button>
        </div>
      </header>

      <div className="layout">
        <section className="compare">
          <div className="panels">
            <Panel kind="generic" title="Generic" sub="no context pack"
              turns={turns} loading={loading} pending={pending} side="generic" />
            <Panel kind="adapted" title="Context-based"
              sub={pack ? `${pack.industry} · ${packAgentName(pack)}` : 'no pack'}
              turns={turns} loading={loading} pending={pending} side="adapted" />
          </div>
          <SharedEval turns={turns} />
          {error && <div className="err chat-err">{error}</div>}
          <form className="composer" onSubmit={ask}>
            <input placeholder="Chat — sent to both bots, with memory…" value={question}
              onChange={(e) => setQuestion(e.target.value)} />
            <button type="submit" disabled={loading}>{loading ? '…' : 'Send'}</button>
          </form>
        </section>

        <aside className="side">
          <div className="selector">
            <label>Step 1: Select Industry</label>
            <select value={industry} onChange={(e) => setIndustry(e.target.value)}>
              {industries.map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}
            </select>

            <label>Step 2: Select Agent</label>
            <select value={agentName} onChange={(e) => setAgentName(e.target.value)} disabled={!agentNames.length}>
              {!agentNames.length && <option value="">no agents</option>}
              {agentNames.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>

            <label>Step 3: Select Version</label>
            <select value={versionId} onChange={(e) => setVersionId(e.target.value)} disabled={!versions.length}>
              {!versions.length && <option value="">none</option>}
              {versions.map((v) => <option key={v.id} value={v.id}>{v.version} · {v.status}</option>)}
            </select>
          </div>

          {!pack ? (
            <p className="hint">No context pack for this industry yet.</p>
          ) : (
            <>
              <div className="card">
                <h3>Agent: {agentName}</h3>
                <b>{pack.persona.name}</b> — {pack.persona.role}
                <div className="dim">{pack.persona.tone}</div>
              </div>
              <div className="card">
                <h3>Escalation Rules</h3>
                <ul className="esc">
                  {pack.escalations.map((e, i) => (
                    <li key={i}><span className="dim">{e.trigger} → {e.to}</span><span className={`pri ${e.priority}`}>{e.priority}</span></li>
                  ))}
                </ul>
              </div>
              <div className="card">
                <h3>System Prompt (adapted agent)</h3>
                <pre className="sysprompt">{sysPrompt || '…'}</pre>
              </div>
            </>
          )}
        </aside>
      </div>
    </>
  )
}

function Panel({ kind, title, sub, turns, loading, pending, side }) {
  return (
    <div className="panel">
      <div className="panel-h"><span className={`dot ${kind}`} /><b>{title}</b><span className="dim">{sub}</span></div>
      <div className="msgs">
        {turns.length === 0 && !loading && (
          <div className="dim" style={{ margin: 'auto', fontSize: 12 }}>Start the conversation below.</div>
        )}
        {turns.map((t, idx) => {
          const latency = side === 'generic' ? t.generic_duration_ms : t.adapted_duration_ms
          return (
            <div key={idx} className="exchange">
              <div className="msg user">{t.question}</div>
              <div className={`msg bot ${kind} md`} dangerouslySetInnerHTML={{ __html: renderMd(side === 'generic' ? t.generic : t.adapted) }} />
              {latency != null && (
                <div className="metrics-row">
                  <span className="metric-label">Response time:</span>
                  <span className="metric-value">{latency} ms</span>
                </div>
              )}
            </div>
          )
        })}
        {loading && pending && (
          <div className="exchange">
            <div className="msg user">{pending}</div>
            <div className={`msg bot ${kind} typing`}><span></span><span></span><span></span></div>
          </div>
        )}
      </div>
    </div>
  )
}

// A single shared comparison bar spanning both panels. Shows the verdict and
// per-metric generic→adapted score bars for the most recent turn that carries
// evaluation data.
function SharedEval({ turns }) {
  let turn = null
  for (let i = turns.length - 1; i >= 0; i--) {
    if (turns[i]?.evaluation && turns[i].evaluation.length > 0) { turn = turns[i]; break }
  }
  if (!turn) return null
  return (
    <div className="eval-shared">
      <div className="evaluation">
        {turn.verdict && <div className="eval-verdict">{turn.verdict}</div>}
        <div className="eval-scores">
          {turn.evaluation.map((m) => (
            <div key={m.metric} className="score-row">
              <span className="score-metric">{m.metric}</span>
              <span className="score-bar">
                <span className="bar generic" style={{ width: `${m.generic}%` }} title={`Generic: ${m.generic}`} />
                <span className="bar adapted" style={{ width: `${m.adapted}%` }} title={`Adapted: ${m.adapted}`} />
              </span>
              <span className="score-nums">{m.generic} → {m.adapted}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function renderMd(src) {
  if (!src) return ''
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const inline = (s) => esc(s)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')

  const lines = String(src).replace(/\r/g, '').split('\n')
  const out = []
  let i = 0
  const closeList = (tag) => { if (tag) out.push(`</${tag}>`) }
  let listTag = ''

  while (i < lines.length) {
    const line = lines[i]

    if (/\|/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      closeList(listTag); listTag = ''
      const cells = (r) => r.replace(/^\s*\|?|\|?\s*$/g, '').split('|').map((c) => c.trim())
      const head = cells(line)
      i += 2
      const rows = []
      while (i < lines.length && lines[i].includes('|')) { rows.push(cells(lines[i])); i++ }
      out.push('<table><thead><tr>' + head.map((h) => `<th>${inline(h)}</th>`).join('') + '</tr></thead><tbody>' +
        rows.map((r) => '<tr>' + r.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table>')
      continue
    }

    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) { closeList(listTag); listTag = ''; const n = h[1].length; out.push(`<h${n + 2}>${inline(h[2])}</h${n + 2}>`); i++; continue }

    const li = line.match(/^\s*([-*]|\d+\.)\s+(.*)$/)
    if (li) {
      const want = /\d/.test(li[1]) ? 'ol' : 'ul'
      if (listTag !== want) { closeList(listTag); out.push(`<${want}>`); listTag = want }
      out.push(`<li>${inline(li[2])}</li>`); i++; continue
    }

    closeList(listTag); listTag = ''
    if (line.trim() === '') { i++; continue }
    out.push(`<p>${inline(line)}</p>`); i++
  }
  closeList(listTag)
  return out.join('')
}

const EMPTY = {
  industry: '', useCase: '', name: '',
  persona: '', instructions: '', guardrails: '', handoff: '',
  kbFiles: [], kbFileNames: [], dynamic: true,
}

// ---- Requirement 1 edit helpers: pack <-> structured text form ----
const nlJoin = (arr) => (arr || []).join('\n')
const nlSplit = (s) => String(s || '').split('\n').map((x) => x.trim()).filter(Boolean)

function packToEditForm(pack) {
  const p = pack || {}
  const persona = p.persona || {}
  const scope = p.scope || {}
  const g = p.guardrails || {}
  return {
    persona_name: persona.name || '',
    persona_role: persona.role || '',
    persona_tone: persona.tone || '',
    persona_characteristics: nlJoin(persona.characteristics),
    scope_industry: scope.industry || p.meta?.industry || '',
    scope_in: nlJoin(scope.in_scope),
    scope_out: nlJoin(scope.out_of_scope),
    scope_refusal: scope.refusal_message || '',
    ontology: (p.ontology || []).map((o) => `${o.term}: ${o.definition}`).join('\n'),
    domain_prompts: nlJoin(p.domain_prompts),
    g_safety: nlJoin(g.safety_rules),
    g_denied: nlJoin(g.denied_topics),
    g_blocked: nlJoin(g.blocked_phrases),
    g_pii: nlJoin(g.pii_blocks),
    escalation: (p.escalation_rules || [])
      .map((e) => `${e.trigger} -> ${e.escalate_to} -> ${e.priority || 'Medium'}`)
      .join('\n'),
  }
}

// Build a ContextPack dict from the edit form, merged over the existing pack so
// meta, use_case, response_guidelines, integrations, etc. are preserved.
function editFormToPack(f, base) {
  const prev = base || {}
  const prevPersona = prev.persona || {}
  const prevScope = prev.scope || {}
  const prevGuard = prev.guardrails || {}

  const ontology = nlSplit(f.ontology).map((line) => {
    const idx = line.indexOf(':')
    const term = idx === -1 ? line : line.slice(0, idx).trim()
    const definition = idx === -1 ? '' : line.slice(idx + 1).trim()
    const existing = (prev.ontology || []).find((o) => o.term?.toLowerCase() === term.toLowerCase())
    return { term, definition, core: existing?.core ?? false }
  })

  const escalation_rules = nlSplit(f.escalation).map((line) => {
    const parts = line.split('->').map((x) => x.trim())
    const existing = (prev.escalation_rules || []).find((e) => e.trigger === parts[0])
    return {
      trigger: parts[0] || '',
      escalate_to: parts[1] || '',
      priority: parts[2] || existing?.priority || 'Medium',
      contact: existing?.contact ?? '',
    }
  })

  return {
    ...prev,
    persona: {
      ...prevPersona,
      name: f.persona_name,
      role: f.persona_role,
      tone: f.persona_tone,
      characteristics: nlSplit(f.persona_characteristics),
    },
    scope: {
      ...prevScope,
      industry: f.scope_industry,
      in_scope: nlSplit(f.scope_in),
      out_of_scope: nlSplit(f.scope_out),
      refusal_message: f.scope_refusal,
    },
    ontology,
    domain_prompts: nlSplit(f.domain_prompts),
    guardrails: {
      ...prevGuard,
      safety_rules: nlSplit(f.g_safety),
      denied_topics: nlSplit(f.g_denied),
      blocked_phrases: nlSplit(f.g_blocked),
      pii_blocks: nlSplit(f.g_pii),
    },
    escalation_rules,
  }
}

const APPROVAL_STEPS = [
  { key: 's3', label: 'Uploading to S3', hint: 'Saving the context pack + ontology' },
  { key: 'guardrail', label: 'Creating guardrail', hint: 'Bedrock topic / word / PII policy' },
  { key: 'kb', label: 'Storing in knowledge base', hint: 'Bedrock KB ingest of uploaded files' },
  { key: 'prompt', label: 'Creating prompt management', hint: 'Bedrock Prompt resource' },
]

// Rich, legible approval progress: a progress bar, per-step status cards with a
// spinner/check/cross and an inline detail line, and a final outcome banner so
// the user can see exactly where the process is and where (if anywhere) it failed.
function ApprovalProgress({ steps, terminal, terminalStatus }) {
  const done = steps.filter((s) => s.status === 'succeeded' || s.status === 'failed').length
  const pct = Math.round((done / steps.length) * 100)
  const anyFailed = steps.some((s) => s.status === 'failed')
  const barClass = terminal ? (terminalStatus === 'succeeded' ? 'ok' : 'fail') : ''

  const ICON = {
    succeeded: '✓',
    failed: '✕',
    in_progress: '',   // rendered as a spinner element
    pending: '',
  }

  return (
    <div className="approval">
      <div className="approval-head">
        <h3>{terminal
          ? (terminalStatus === 'succeeded' ? 'Approval complete' : 'Approval finished with issues')
          : 'Approving…'}</h3>
        <span className="approval-count">{done} / {steps.length} steps</span>
      </div>

      <div className={`approval-bar ${barClass}`}>
        <div className="approval-fill" style={{ width: `${pct}%` }} />
      </div>

      <ol className="approval-steps">
        {steps.map((s) => (
          <li key={s.key} className={`astep ${s.status}`}>
            <span className="astep-ico">
              {s.status === 'in_progress'
                ? <span className="spinner" aria-label="in progress" />
                : <span className="astep-glyph">{ICON[s.status] || '○'}</span>}
            </span>
            <div className="astep-body">
              <div className="astep-row">
                <span className="astep-label">{s.label}</span>
                <span className={`astep-badge ${s.status}`}>
                  {s.status === 'in_progress' ? 'running'
                    : s.status === 'succeeded' ? 'done'
                    : s.status === 'failed' ? 'failed'
                    : 'waiting'}
                </span>
              </div>
              <div className="astep-hint">
                {s.detail ? s.detail : s.hint}
              </div>
            </div>
          </li>
        ))}
      </ol>

      {terminal && (
        <div className={`approval-banner ${terminalStatus === 'succeeded' ? 'ok' : 'fail'}`}>
          {terminalStatus === 'succeeded'
            ? 'All steps completed successfully.'
            : (anyFailed
                ? 'One or more steps failed — see the highlighted step above. Nothing is left half-saved that you need to clean up; fix the cause (e.g. refresh AWS credentials) and approve again.'
                : 'Approval did not complete.')}
        </div>
      )}
    </div>
  )
}

function CreatePack({ onApprovalSuccess }) {
  const [form, setForm] = useState(EMPTY)
  const [phase, setPhase] = useState('define')
  const [stage, setStage] = useState(0)
  const [stages, setStages] = useState([])
  const [draft, setDraft] = useState(null)
  const [committed, setCommitted] = useState(null)
  const [industries, setIndustries] = useState([])
  const [nameTaken, setNameTaken] = useState(false)
  const [error, setError] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const timer = useRef(null)

  // Requirement 1 — edit preview state.
  const [editing, setEditing] = useState(false)
  const [editForm, setEditForm] = useState(null)
  const [gaps, setGaps] = useState('')
  const [savingEdit, setSavingEdit] = useState(false)

  // Requirement 4 — streamed approval progress state.
  const [steps, setSteps] = useState(() => APPROVAL_STEPS.map((s) => ({ ...s, status: 'pending', detail: '' })))
  const [terminal, setTerminal] = useState(false)
  const [terminalStatus, setTerminalStatus] = useState('')  // 'succeeded' | 'failed' | ''

  const gapsBlocking = gaps !== '' && !gaps.startsWith('OK')

  useEffect(() => { genStages().then(setStages) }, [])
  useEffect(() => { listIndustries().then(setIndustries) }, [])

  function set(key, val) { setForm((f) => ({ ...f, [key]: val })) }

  useEffect(() => {
    if (!form.industry.trim() || !form.name.trim()) { setNameTaken(false); return }
    let active = true
    checkName(form).then((exists) => { if (active) setNameTaken(exists) }).catch(() => {})
    return () => { active = false }
  }, [form.industry, form.name])

  const canGenerate = form.industry.trim() && form.name.trim()

  async function generate(e) {
    e.preventDefault()
    if (!canGenerate) return
    setError('')
    setPhase('generating'); setStage(0)
    clearInterval(timer.current)
    const total = stages.length || 7
    timer.current = setInterval(() => setStage((s) => Math.min(s + 1, total - 1)), 450)
    try {
      let kbNames = form.kbFileNames
      if (form.kbFiles && form.kbFiles.length) {
        kbNames = await uploadKb(form.industry, form.name, form.kbFiles)
      }
      const d = await generateDraft({ ...form, kbFileNames: kbNames })
      clearInterval(timer.current); setStage(total - 1)
      setDraft(d)
      setPhase('review')
    } catch (err) {
      clearInterval(timer.current)
      setError(String(err.message || err))
      setPhase('define')
    }
  }

  async function approve() {
    setError('')
    setTerminal(false)
    setTerminalStatus('')
    setSteps(APPROVAL_STEPS.map((s) => ({ ...s, status: 'pending', detail: '' })))
    setPhase('saving')
    try {
      const done = await approveDraftStream(draft.draft_id, (evt) => {
        if (!evt || !evt.step || evt.step === 'done') return
        setSteps((prev) => prev.map((s) =>
          s.key === evt.step
            ? { ...s, status: evt.status, detail: evt.detail || '' }
            : s
        ))
      })
      setTerminal(true)
      setTerminalStatus(done.status || '')
      if (done.status === 'succeeded' && done.pack) {
        setCommitted(done.pack)
        setPhase('done')
        // Notify the parent to refresh the chat UI so it picks up the newly approved pack
        onApprovalSuccess?.()
      } else {
        setError(done.detail || 'Approval failed. One or more steps did not complete.')
      }
    } catch (err) {
      setTerminal(true)
      setTerminalStatus('failed')
      setError(String(err.message || err))
    }
  }

  async function reject() {
    await rejectDraft(draft?.draft_id)
    setDraft(null)
    setPhase('define')
  }

  function reset() {
    setForm(EMPTY); setDraft(null); setCommitted(null); setError(''); setPhase('define')
    setEditing(false); setEditForm(null); setGaps(''); setSavingEdit(false)
    setTerminal(false); setTerminalStatus('')
    setSteps(APPROVAL_STEPS.map((s) => ({ ...s, status: 'pending', detail: '' })))
  }

  // ---- Requirement 1: edit preview ----
  function startEdit() {
    setError('')
    setEditForm(packToEditForm(draft.pack))
    setEditing(true)
  }

  function cancelEdit() {
    setEditing(false)
    setEditForm(null)
    setGaps('')
  }

  function setEdit(key, val) { setEditForm((f) => ({ ...f, [key]: val })) }

  async function saveEdit() {
    setSavingEdit(true)
    setError('')
    try {
      const pack = editFormToPack(editForm, draft.pack)
      const resp = await updateDraft(draft.draft_id, pack)
      setDraft((d) => ({ ...d, pack: resp.pack, system_prompt: resp.system_prompt }))
      setGaps(resp.gaps || '')
      if (resp.gaps && resp.gaps.startsWith('OK')) {
        setEditing(false)
        setEditForm(null)
      }
    } catch (err) {
      setError('Re-validation did not complete: ' + String(err.message || err))
    } finally {
      setSavingEdit(false)
    }
  }

  const scrollable = phase === 'review' || phase === 'saving' || phase === 'done'
  const wizardSteps = ['Define', 'Generate', 'Review']
  const stepIdx = phase === 'define' ? 0 : phase === 'generating' ? 1 : 2

  return (
    <>
      <header className="top">
        <h1>Create a Context Pack</h1>
        <div className="stepper">
          {wizardSteps.map((s, i) => (
            <span key={s} className={`step ${i === stepIdx ? 'on' : ''} ${i < stepIdx ? 'done' : ''}`}>{s}</span>
          ))}
        </div>
      </header>

      <div className={`create ${scrollable ? 'scroll' : 'noscroll'}`}>
        {phase === 'define' && (
          <form className="define-form" onSubmit={generate}>
            <p className="lead">
              Tell the agent the <b>industry</b> and <b>what the assistant is for</b>.
              It generates the full context pack and reuses anything that already exists.
            </p>

            <div className="field-row">
              <div className="field-col">
                <label>Industry *</label>
                <input type="text" placeholder="Type any industry — e.g. Banking, Logistics…"
                  value={form.industry} onChange={(e) => set('industry', e.target.value)} />
                {industries.length > 0 && (
                  <div className="suggest">
                    existing:
                    {industries.map((i) => (
                      <button type="button" key={i.id} className="sg-chip"
                        onClick={() => set('industry', i.label)}>{i.label}</button>
                    ))}
                  </div>
                )}
              </div>
              <div className="field-col">
                <label>Name *</label>
                <input placeholder="e.g. support, wealth-desk" value={form.name}
                  onChange={(e) => set('name', e.target.value)} />
                {nameTaken && <span className="warn-inline">📦 Existing pack found. A new version will be created when approved.</span>}
              </div>
            </div>

            <label>Use case *</label>
            <textarea rows={3} placeholder="What should this assistant help with? e.g. account, card and payment questions for retail banking customers."
              value={form.useCase} onChange={(e) => set('useCase', e.target.value)} />

            <label className="kb-label">Knowledge base files (optional)</label>
            <input type="file" multiple className="kb-input"
              onChange={(e) => {
                const files = Array.from(e.target.files || [])
                setForm((f) => ({ ...f, kbFiles: files, kbFileNames: files.map((x) => x.name) }))
              }} />
            {form.kbFileNames.length > 0 && <div className="dim small">{form.kbFileNames.join(', ')}</div>}

            <button type="button" className="link-btn" onClick={() => setShowAdvanced((v) => !v)}>
              {showAdvanced ? '− Hide' : '+ Add'} optional overrides (persona, rules, guardrails, handoff)
            </button>

            {showAdvanced && (
              <div className="advanced">
                <div>
                  <label>Persona tone</label>
                  <input placeholder="e.g. Professional, compliance-first" value={form.persona}
                    onChange={(e) => set('persona', e.target.value)} />
                </div>
                <div>
                  <label>Instructions (one per line)</label>
                  <textarea rows={2} value={form.instructions} onChange={(e) => set('instructions', e.target.value)} />
                </div>
                <div>
                  <label>Guardrails — denied topics (one per line)</label>
                  <textarea rows={2} value={form.guardrails} onChange={(e) => set('guardrails', e.target.value)} />
                </div>
                <div>
                  <label>Handoff rules (trigger -&gt; team)</label>
                  <textarea rows={2} value={form.handoff} onChange={(e) => set('handoff', e.target.value)} />
                </div>
              </div>
            )}

            {error && <div className="err">{error}</div>}

            <button className="submit" type="submit" disabled={!canGenerate}>
              {nameTaken ? "Generate New Version" : "Generate with agent"}
            </button>
          </form>
        )}

        {phase === 'generating' && (
          <div className="progress-wrap">
            <h2>Agent is building the pack…</h2>
            <div className="pbar"><div className="pfill" style={{ width: `${((stage + 1) / (stages.length || 7)) * 100}%` }} /></div>
            <ul className="stages">
              {stages.map((s, i) => (
                <li key={s} className={i < stage ? 'done' : i === stage ? 'cur' : ''}>
                  <span className="s-ico">{i < stage ? '✓' : i === stage ? '•' : '○'}</span>{s}
                </li>
              ))}
            </ul>
          </div>
        )}

        {(phase === 'review' || phase === 'saving' || phase === 'done') && draft && (
          <div className="review">
            <div className="review-head">
              <div>
                <h2>{phase === 'done' ? 'Saved' : 'Review the generated pack'}</h2>
                <div className="dim small">
                  provider: {draft.provider}
                  {draft.reused.length > 0 && <> · reused: {draft.reused.join(', ')}</>}
                  {draft.generated.length > 0 && <> · generated: {draft.generated.join(', ')}</>}
                </div>
              </div>
              {phase === 'review' && !editing && (
                <div className="hitl">
                  <button className="reject" onClick={reject}>Reject</button>
                  <button className="reject" onClick={startEdit}>Edit</button>
                  <button className="approve" onClick={approve} disabled={gapsBlocking}>Approve &amp; save</button>
                </div>
              )}
              {phase === 'review' && editing && (
                <div className="hitl">
                  <button className="reject" onClick={cancelEdit} disabled={savingEdit}>Cancel</button>
                  <button className="approve" onClick={saveEdit} disabled={savingEdit}>
                    {savingEdit ? 'Validating…' : 'Save & re-validate'}
                  </button>
                </div>
              )}
              {(phase === 'saving' || phase === 'done') && (
                <button className="approve" onClick={reset} disabled={phase === 'saving' && !terminal}>
                  Create another
                </button>
              )}
            </div>

            {(phase === 'saving' || phase === 'done') && (
              <ApprovalProgress steps={steps} terminal={terminal} terminalStatus={terminalStatus} />
            )}

            {phase === 'done' && terminalStatus === 'succeeded' && committed && (
              <div className="ok">
                Saved <b>{committed.meta.pack_id}</b> ({committed.meta.version}). Reusable pieces
                stored for {committed.meta.industry}; future packs will build on them.
              </div>
            )}

            {error && editing && <div className="err">{error}</div>}

            {gapsBlocking && (
              <div className="err">{gaps}</div>
            )}

            {editing && editForm && (
              <div className="advanced edit-form">
                <div>
                  <label>Persona name</label>
                  <input value={editForm.persona_name} onChange={(e) => setEdit('persona_name', e.target.value)} />
                </div>
                <div>
                  <label>Persona role</label>
                  <input value={editForm.persona_role} onChange={(e) => setEdit('persona_role', e.target.value)} />
                </div>
                <div>
                  <label>Persona tone</label>
                  <input value={editForm.persona_tone} onChange={(e) => setEdit('persona_tone', e.target.value)} />
                </div>
                <div>
                  <label>Persona characteristics (one per line)</label>
                  <textarea rows={3} value={editForm.persona_characteristics} onChange={(e) => setEdit('persona_characteristics', e.target.value)} />
                </div>
                <div>
                  <label>Scope — industry</label>
                  <input value={editForm.scope_industry} onChange={(e) => setEdit('scope_industry', e.target.value)} />
                </div>
                <div>
                  <label>Scope — in scope (one per line)</label>
                  <textarea rows={3} value={editForm.scope_in} onChange={(e) => setEdit('scope_in', e.target.value)} />
                </div>
                <div>
                  <label>Scope — out of scope (one per line)</label>
                  <textarea rows={3} value={editForm.scope_out} onChange={(e) => setEdit('scope_out', e.target.value)} />
                </div>
                <div>
                  <label>Scope — refusal message</label>
                  <textarea rows={2} value={editForm.scope_refusal} onChange={(e) => setEdit('scope_refusal', e.target.value)} />
                </div>
                <div>
                  <label>Ontology (one "term: definition" per line)</label>
                  <textarea rows={4} value={editForm.ontology} onChange={(e) => setEdit('ontology', e.target.value)} />
                </div>
                <div>
                  <label>Domain prompts (one per line)</label>
                  <textarea rows={4} value={editForm.domain_prompts} onChange={(e) => setEdit('domain_prompts', e.target.value)} />
                </div>
                <div>
                  <label>Guardrails — safety rules (one per line)</label>
                  <textarea rows={3} value={editForm.g_safety} onChange={(e) => setEdit('g_safety', e.target.value)} />
                </div>
                <div>
                  <label>Guardrails — denied topics (one per line)</label>
                  <textarea rows={3} value={editForm.g_denied} onChange={(e) => setEdit('g_denied', e.target.value)} />
                </div>
                <div>
                  <label>Guardrails — blocked phrases (one per line)</label>
                  <textarea rows={3} value={editForm.g_blocked} onChange={(e) => setEdit('g_blocked', e.target.value)} />
                </div>
                <div>
                  <label>Guardrails — PII blocks (one per line)</label>
                  <textarea rows={3} value={editForm.g_pii} onChange={(e) => setEdit('g_pii', e.target.value)} />
                </div>
                <div>
                  <label>Escalation rules (one "trigger -&gt; escalate_to -&gt; priority" per line)</label>
                  <textarea rows={3} value={editForm.escalation} onChange={(e) => setEdit('escalation', e.target.value)} />
                </div>
              </div>
            )}

            {!editing && (
            <>
            <div className="chips">
              {draft.reused.map((p) => <span key={p} className="chip reused">reused · {p}</span>)}
              {draft.generated.map((p) => <span key={p} className="chip gen">new · {p}</span>)}
            </div>

            <div className="rgrid">
              <div className="card"><h3>Industry</h3><b>{draft.pack.meta.industry}</b></div>
              <div className="card"><h3>Persona</h3>{draft.pack.persona.name} — {draft.pack.persona.tone}</div>
              <div className="card"><h3>Use case</h3>{draft.pack.use_case || '—'}</div>
              <div className="card"><h3>Ontology</h3><ul>{draft.pack.ontology.map((o, i) => <li key={i}><b>{o.term}</b>: {o.definition}</li>)}</ul></div>
              <div className="card"><h3>Escalation / Handoff</h3><ul>{draft.pack.escalation_rules.map((e, i) => <li key={i}>{e.trigger} → {e.escalate_to}</li>)}</ul></div>
              <div className="card"><h3>Guardrails</h3>
                {draft.pack.guardrails.denied_topics.length
                  ? <ul>{draft.pack.guardrails.denied_topics.map((t, i) => <li key={i}>{t}</li>)}</ul>
                  : <span className="dim">PII defaults only</span>}
              </div>
              <div className="card" style={{ gridColumn: '1 / -1' }}>
                <h3>Composed system prompt</h3>
                <pre className="sysprompt">{draft.system_prompt}</pre>
              </div>
            </div>
            </>
            )}
          </div>
        )}
      </div>
    </>
  )
}
