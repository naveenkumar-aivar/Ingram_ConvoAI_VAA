import { useEffect, useRef, useState } from 'react'
import {
  listIndustries, listVersions, getPack, getSystemPrompt, compare,
  genStages, newSession, resetSession, recentSessions, sessionHistory,
  checkName, generateDraft, approveDraft, rejectDraft, uploadKb,
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
          ? <Chatbot resume={resume} onActivity={() => setRefreshKey((k) => k + 1)} />
          : <CreatePack />}
      </main>
    </div>
  )
}

function agentName(v) {
  const id = v?.id || ''
  const base = id.includes('-v') ? id.slice(0, id.lastIndexOf('-v')) : id
  return base || v?.version || id
}

function Chatbot({ resume, onActivity }) {
  const [industries, setIndustries] = useState([])
  const [industry, setIndustry] = useState('banking')
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

  useEffect(() => { listIndustries().then(setIndustries) }, [])

  useEffect(() => {
    listVersions(industry).then((vs) => {
      setVersions(vs)
      setVersionId(vs[0]?.id || '')
    })
  }, [industry])

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
            <Panel kind="adapted" title="Context-based" sub={pack ? `${pack.industry} · ${versionId}` : 'no pack'}
              turns={turns} loading={loading} pending={pending} side="adapted" />
          </div>
          {error && <div className="err chat-err">{error}</div>}
          <form className="composer" onSubmit={ask}>
            <input placeholder="Chat — sent to both bots, with memory…" value={question}
              onChange={(e) => setQuestion(e.target.value)} />
            <button type="submit" disabled={loading}>{loading ? '…' : 'Send'}</button>
          </form>
        </section>

        <aside className="side">
          <label>Industry</label>
          <select value={industry} onChange={(e) => setIndustry(e.target.value)}>
            {industries.map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}
          </select>

          <label>Name of the agent</label>
          <select value={versionId} onChange={(e) => setVersionId(e.target.value)} disabled={!versions.length}>
            {!versions.length && <option value="">none</option>}
            {versions.map((v) => <option key={v.id} value={v.id}>{agentName(v)} · {v.status}</option>)}
          </select>

          {!pack ? (
            <p className="hint">No context pack for this industry yet.</p>
          ) : (
            <>
              <div className="card">
                <h3>Persona</h3>
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
        {turns.map((t, idx) => (
          <div key={idx} className="exchange">
            <div className="msg user">{t.question}</div>
            <div className={`msg bot ${kind} md`} dangerouslySetInnerHTML={{ __html: renderMd(side === 'generic' ? t.generic : t.adapted) }} />
            {idx === turns.length - 1 && (t.generic_duration_ms || t.adapted_duration_ms) && (
              <div className="metrics-row">
                <span className="metric-label">Response time:</span>
                <span className="metric-value">
                  {side === 'generic' ? t.generic_duration_ms : t.adapted_duration_ms}ms
                </span>
              </div>
            )}
            {idx === turns.length - 1 && t.evaluation && t.evaluation.length > 0 && side === 'adapted' && (
              <div className="evaluation">
                <div className="eval-verdict">{t.verdict}</div>
                <div className="eval-scores">
                  {t.evaluation.map((m) => (
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
            )}
          </div>
        ))}
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

function CreatePack() {
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

  useEffect(() => { genStages().then(setStages) }, [])
  useEffect(() => { listIndustries().then(setIndustries) }, [])

  function set(key, val) { setForm((f) => ({ ...f, [key]: val })) }

  useEffect(() => {
    if (!form.industry.trim() || !form.name.trim()) { setNameTaken(false); return }
    let active = true
    checkName(form).then((exists) => { if (active) setNameTaken(exists) }).catch(() => {})
    return () => { active = false }
  }, [form.industry, form.name])

  const canGenerate = form.industry.trim() && form.name.trim() && !nameTaken

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
    setPhase('saving')
    try {
      const c = await approveDraft(draft.draft_id)
      setCommitted(c)
      setPhase('done')
    } catch (err) {
      setError(String(err.message || err)); setPhase('review')
    }
  }

  async function reject() {
    await rejectDraft(draft?.draft_id)
    setDraft(null)
    setPhase('define')
  }

  function reset() {
    setForm(EMPTY); setDraft(null); setCommitted(null); setError(''); setPhase('define')
  }

  const scrollable = phase === 'review' || phase === 'saving' || phase === 'done'
  const steps = ['Define', 'Generate', 'Review']
  const stepIdx = phase === 'define' ? 0 : phase === 'generating' ? 1 : 2

  return (
    <>
      <header className="top">
        <h1>Create a Context Pack</h1>
        <div className="stepper">
          {steps.map((s, i) => (
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
                {nameTaken && <span className="warn-inline">This name already exists for {form.industry}.</span>}
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

            <button className="submit" type="submit" disabled={!canGenerate}>Generate with agent</button>
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
              {phase === 'review' && (
                <div className="hitl">
                  <button className="reject" onClick={reject}>Reject</button>
                  <button className="approve" onClick={approve}>Approve &amp; save</button>
                </div>
              )}
              {phase === 'saving' && <span className="dim">Saving…</span>}
              {phase === 'done' && <button className="approve" onClick={reset}>Create another</button>}
            </div>

            {phase === 'done' && committed && (
              <div className="ok">
                Saved <b>{committed.meta.pack_id}</b> ({committed.meta.version}). Reusable pieces
                stored for {committed.meta.industry}; future packs will build on them.
              </div>
            )}

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
          </div>
        )}
      </div>
    </>
  )
}
