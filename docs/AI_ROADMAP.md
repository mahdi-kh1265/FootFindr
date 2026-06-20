# AI Integration Roadmap

## Overview

FootFindr will integrate AI capabilities gradually, starting with
datasheet extraction and IC profile drafting. AI will never be given
authority to pick footprints or approve parts — the human engineer
remains the final decision-maker.

## Guiding Principles

1. **AI starts with datasheet/profile extraction, never "AI picks footprints."**
2. **AI-generated data is always draft status until human-approved.**
3. **No AI-driven automatic substitutions or resolver overrides.**
4. **Local models preferred for privacy; cloud models require explicit opt-in.**

## Four-Stage Roadmap

### Stage 1 — Scaffolding (Current: M7)

**Status: Implemented**

- `AIProvider` abstract base class (`ai/provider.py`)
- `MockAIProvider` for testing
- `DraftICProfile` schema (`ai/schemas.py`)
- `ff profile draft <MPN> --mock` — mock profile drafting
- `ff profile show <MPN>` — display profile
- `ff profile approve <MPN>` — approve draft profile

**No live AI API calls.** Mock provider returns canned profiles.

### Stage 2 — Datasheet Extraction (M8)

**Goal**: Real AI extracts structured data from PDF/text datasheets.

```text
PDF/text datasheet
    → AI provider (Claude/GPT)
    → DraftICProfile (JSON)
    → human review
    → approved profile
    → resolver can use approved profile
```

Implementation:
- `ff profile draft <MPN>` — calls AI provider with datasheet text
- Profile includes: pinout, abs max ratings, recommended footprint,
  electrical specs, application notes summary
- All drafts marked `status: draft` until approved
- Approval creates `status: approved` and stores in project profile DB

### Stage 3 — AI Explain and Q&A (M9+)

**Goal**: AI helps engineers understand parts and design decisions.

- `ff ai explain <ref>` — explain why a part was selected, what
  alternatives exist, and what the trade-offs are
- `ff ai datasheet-qa <MPN> <question>` — ask questions about a
  part's datasheet (requires extracted text)
- `ff ai eval-bom <pdf>` — extract BOM from eval board PDF/datasheet

### Stage 4 — Multi-Provider and RAG (M10+)

**Goal**: Production-ready AI with provider selection and local knowledge.

- Multi-provider support (OpenAI, Claude, Gemini, local Ollama)
- RAG over local datasheet corpus
- Confidence scoring for AI-generated data
- Automatic re-extraction when datasheets are updated
- Integration with resolved BOM for design review

## Architecture

```text
footfindr/ai/
  provider.py      — AIProvider ABC, provider registry
  schemas.py       — DraftICProfile, ExtractionResult models
  profile_drafter.py — Profile extraction logic
  (future)
  explainer.py     — AI explain command logic
  datasheet_qa.py  — Datasheet Q&A logic
  rag.py           — RAG over local datasheet corpus
```

## Safety Rules for AI Integration

1. **Draft-only output.**
   AI-generated profiles, explanations, and extractions are always
   draft status. They never auto-approve or auto-apply.

2. **No resolver influence without approval.**
   AI-generated data must be explicitly approved before the resolver
   can use it as a constraint or selection criterion.

3. **No hidden API calls.**
   All AI API calls must be triggered by explicit user commands.
   Background AI processing requires opt-in.

4. **Data provenance.**
   Every AI-generated artifact must include:
   - Model name and version
   - Timestamp
   - Input hash (datasheet SHA256 or text fingerprint)
   - Confidence score (if available)

5. **Local-first option.**
   Users must be able to use local models (Ollama, llama.cpp) for
   privacy-sensitive designs. Cloud providers are opt-in only.
