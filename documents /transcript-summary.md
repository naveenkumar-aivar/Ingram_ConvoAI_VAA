# Conversational AI — Working Session Summary (Sep 17)

Source: Ingram Micro × Aivar/AWS deep-dive / working session on the "Conversational AI" initiative.

---

## What the project actually is

A working session between **Ingram Micro** (Lucas, Kevin, Mauro, Paul) and **Aivar/AWS** (Sundar, Sparsh, Aadarsh) to scope a **"Conversational AI"** initiative.

Despite the name, this is **not a customer-facing chatbot**. It is an **internal productivity tool** for Ingram's delivery teams that helps them discover, combine, and reuse existing AI solutions instead of rebuilding every project from scratch.

## The core problem being solved

Mauro (head of AI services, USC / EMEA) described two halves of one problem:

1. **Pre-sales bridge** — customers know they have a problem but can't connect it to the right solution. (Mostly owned by *Project Catalyst*, a separate platform.)
2. **Delivery reinvention** — every AI project starts from zero. Engineers rebuild similar solutions (almost all are RAG + Bedrock + chatbot + document processing), use their own private standards, and don't reuse each other's work. "Reinventing the wheel" is the recurring phrase.

The Conversational AI layer targets **problem #2**: stop rebuilding, start reusing.

## Critical distinction: Project Catalyst vs. Conversational AI

- **Project Catalyst** = the broader platform. Handles qualification, sales pipeline, pre-sales, funding programs (POC / MAP / etc.). It's the "entrance door."
- **Conversational AI** = a *solution inside* Catalyst, focused only on the **delivery/build** phase. "The Genie" — you describe a problem, it finds existing solutions, tells you what can be combined or reused, and flags gaps.

Lucas framed the vision as pillars (Migration/Modernization, Data+AI, MSP) all sitting on Catalyst, with Conversational AI being the assistant for **repeatable AI solutions** specifically.

## The build / combine / reuse model

- **Components** = single-purpose pieces (e.g. a WhatsApp connector, a Knowledge Base, a Vision-for-invoices module). One clear use.
- **Solutions** = assembled from components. Can have *multiple* use cases.
- Over time work shifts: **building → combining → reusing → maintaining**.
- Versioning matters: new variants bump a version rather than spawning duplicate repos; existing deployments get upgrade notifications (potential upsell).

## MVP scope (build first)

Target: **before re:Invent, ~2.5 months**, internal-only, no partner-facing features yet.

1. **Discovery agent** — two jobs:
   - Takes a use case (or CRM opportunity ID / link), asks *qualifying questions*, finds matching existing solutions and returns their repos.
   - Drives **solution onboarding**: interactively asks SOP-driven questions to index a new solution into the catalog (the SOP is *instructions for the bot*, NOT a form the user fills out).
2. **Solutioning agent** — takes discovery output, assembles the recommended stack, validates with the user, identifies gaps/missing components, and produces an **output artifact** (one-pager / two-pager / deck / design doc).
3. **Solution catalog / knowledge base** — built from existing delivered projects (expect **5–10 proven solutions** to start, not vendor-generic ones).
4. **Human-in-the-loop (HITL)** — AI can hallucinate or combine stacks wrong. Generated docs are drafts requiring human approval before sharing. The AIDLC design-doc → tasks stage is the natural review gate.

**Explicitly NOT in MVP:** actual auto-deployment of solutions, partner-facing access, language localization, security reviews of uploaded solutions. These are post-MVP.

## Key technical facts for implementation

- **Repos:** GitHub, primarily. Not consolidated — UK has one repo, Spain is partway through unifying (~20–30 solutions scattered, 3–4 in the main repo). No global standard exists yet; building that standard is part of the goal.
- **Documents (SOWs, use cases, pricing):** stored as files in a **shared SharePoint**, not standardized.
- **Discovery methodology:** AWS **AIDLC** framework (AI-Driven software Development Lifecycle, now v2, 4 stages). Use the **discovery phase** to analyze existing/brownfield projects and extract structured descriptions into a knowledge base. Start with a pilot (e.g. EMEA, or UK/Spain, last 6 months of data).
- **No governance exists today** — no change management, no security controls, no standardized delivery. ~50% of projects don't even hand code to the customer. Info-sec involvement comes later.
- **Common AI stack across projects:** Amazon Bedrock, RAG/knowledge bases, chatbots, document/data extraction, some voice/vision.

## Scale & business context (sizing / ROI)

- EMEA volume YTD: **31 AWS data+AI opportunities**, 186 Microsoft (164 of those Copilot, so noisy/miscategorized).
- Baseline date range: **Jan 1 this year** (when USC centralized tracking).
- Growth motions ramping in UK/Spain greenfield teams; estimate ~24 opportunities/week once UK/Spain/Netherlands/Austria onboard.
- Targets discussed: **4x productivity**, **200 projects/year delivered globally**, standardized workflows.
- Average services deal ~$20K today, goal to drive average down to ~$10K with tooling efficiency.
- ROI drivers: **faster delivery + not reinventing the wheel** internally; broader catalog without technical skills for partners later.

## Long-term "North Star" (not now, but know it)

Eventually: Catalyst auto-suggests existing solutions to partners; partners upload their own solutions (pre-approved, feeding the "AI Exchange" marketplace); the platform identifies missing components and routes build work to Ingram as a services revenue path. "Build here, launch everywhere." Everything should **compound** — never start from scratch, including the catalog itself.

## Practical takeaways for a builder

- Think **agents + knowledge base**, not a monolith. Two MVP agents: Discovery and Solutioning.
- Keep the **conversational interface** central — onboarding and discovery are both interactive, not form-based.
- Design the catalog around the **component vs. solution** distinction and **versioning** from day one.
- Bake in **HITL approval** on anything generated.
- Expect messy, non-standardized inputs (scattered GitHub repos, SharePoint docs) — the first real engineering task is **discovery + normalization** of existing projects via AIDLC.
- Keep MVP strictly **internal**; don't gold-plate with partner/security/i18n features that are deferred.
