# s3-data — Local stand-in for the S3 bucket

Mirrors the real S3 bucket `vertical-adaption-agent/`. In local mode
(MOCK_MODE=true) the backend reads/writes here; the same layout maps to S3.

## Structure (name-level)

```
s3-data/                                   (= s3://vertical-adaption-agent/)
├── <industry>/                            e.g. banking
│   ├── ontology/ vN.json                  ← COMMON per industry (shared by all names)
│   └── <name>/                            e.g. support, wealth-desk
│       ├── persona/     vN.json
│       ├── prompts/     vN.json
│       ├── handoff/     vN.json
│       └── guardrails/  vN.json           ← per name
└── context-pack/
    └── <industry>/<name>/ vN.json, latest.json   ← the assembled pack the chatbot loads
```

Uploaded knowledge-base files live in a **sibling** folder, not here:

```
kb-data/<industry>/<name>/ <uploaded files>   ← ingested into Chroma (local) / Bedrock KB (later)
kb-data/_chroma/                               ← local Chroma vector store
```

## Rules

- **Name is required.** A pack is identified by (industry, name); the same name
  may exist in different industries. `pack_id = "<name>-vN"`.
- **Ontology is common per industry** — on approval, only *missing* terms are
  appended and a new ontology version is written (history kept, sorted).
- **Guardrails/persona/prompts/handoff are per name.**
- **Versions are per (industry, name):** new name → v1, else max+1.
- Nothing is written until a draft is **approved** (drafts live in `_drafts/`).

## Current contents
- `banking` industry with one assistant: `support` (v1), plus the common
  `banking/ontology`.
