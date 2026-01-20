# 🏗️ Stalled Project News — Search‑First, Evidence‑Bound Project Updates (India)

A local‑first pipeline that **searches the web (via SerpAPI)**, fetches only from **strictly whitelisted sources**, extracts evidence snippets, builds an event timeline, and generates a **human‑written style project update (500–1000 words)** with **hard no‑hallucination guardrails**.

> ✅ Core rule: **Every factual claim must map to stored evidence** (snippet + source ref).  
> ❌ If evidence is missing, the output must say **“Insufficient evidence”** instead of guessing.

---

## 🧩 Problem statement

Indian real‑estate buyers/investors often hear *“project is delayed / stuck / stalled”* but struggle to answer basic questions quickly:

- What **actually happened** (with dates)?
- Is there any **regulator/cause‑list / court / credible news** mention?
- What is the **latest known update**?
- What does it mean for **buyers vs investors**?

Most online results are noisy: broker pages, duplicates, irrelevant PDFs, and generic claims. We needed a pipeline that is:

- **Search‑first** (no dependency on a single data source)
- **Whitelisted** (only trusted domains)
- **Evidence‑bounded** (no made‑up facts)
- **Repeatable** (stored artifacts per run, reproducible output)

---

## ✅ Solution (what this project does)

This project builds a deterministic pipeline:

1. **Query generation → SERP retrieval (SerpAPI)**
2. **Whitelist filtering** (domain + optional subdomain rules)
3. **Fetch & extract** (store raw text per URL)
4. **Event/claim extraction** (date‑anchored events from extracted text)
5. **De‑dup + timeline build**
6. **News object generation** (OpenAI JSON mode; 500–1000 words; buyer + investor angle)
7. **Citation coverage verification** (refs used == refs in sources)
8. **Artifacts stored** for every run (JSON + HTML)

Outputs are written into an `artifacts/<slug>/<run_id>/` folder with everything needed to audit the result.

---

## 🧠 What the “model” does (in plain terms)

This project uses an LLM only in the **final step** to write a readable narrative. The LLM:

- Receives a compact “domain pack” + a strict timeline of events
- Must output a **JSON object only** (enforced via JSON response format)
- Is instructed to **never invent facts**
- Must cite only evidence refs that exist in the inputs

✅ The “intelligence” comes from:
- Strong retrieval + whitelist control
- Evidence storage and event extraction that is snippet‑backed
- Relevance gating to prevent timeline pollution

---

## 🧱 Tech stack

- 🐍 **Python** (tested on 3.10.x; compatible with 3.10+)
- 🔎 **SerpAPI** (search results retrieval)
- 🌐 **HTTP fetching** (httpx / requests style fetchers)
- 📄 **Text extraction** (HTML → text; optional PDF extraction where needed)
- 🧠 **OpenAI SDK** (JSON mode for strict structured output)
- 🗂️ **Artifacts‑first storage** (JSON/HTML files on disk)
- ✅ **CLI interface** (`python -m stalled_news ...`)
- 🧾 **YAML whitelist** (`configs/whitelist.yaml`)

---

## 📦 Repository structure (high level)

```
stalled-project-news/
  src/stalled_news/
    __main__.py                 # CLI entrypoint
    models.py                   # ProjectInput + data models
    serp_pipeline.py            # SERP (basic)
    serp_wide_pipeline.py       # SERP (wide: adds news/general queries)
    evidence_pipeline.py        # Fetch + extract from serp_results.json
    event_extractor.py          # Dated event extraction + timeline storage
    news_generator.py           # Build news.json + news.html via OpenAI
    whitelist.py                # WhitelistPolicy + is_url_allowed
    whitelist_helpers.py        # YAML loader + policy construction
  configs/
    whitelist.yaml              # Allowed domains (add RERA/courts/news, etc.)
  artifacts/
    <project-slug>/<run-id>/    # Stored runs (serp_results, evidence, timeline, news)
```

---

## 🚀 Quickstart (end‑to‑end)

### 1) Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2) Configure environment
Create `.env` (do **not** commit):
```bash
OPENAI_API_KEY=YOUR_KEY_HERE
OPENAI_MODEL=gpt-4.1-mini
SERPAPI_API_KEY=YOUR_SERPAPI_KEY
```

### 3) Add trusted domains (whitelist)
Edit:
```bash
configs/whitelist.yaml
```

Example:
```yaml
domains:
  - haryanarera.gov.in
  - maharera.mahaonline.gov.in
  - rera.karnataka.gov.in
  - rera.telangana.gov.in
  - www.rera.delhi.gov.in
  - indiankanoon.org
  - economictimes.indiatimes.com
  - livemint.com
  - squareyards.com
```

### 4) Run pipeline
#### A) Search
```bash
python -m stalled_news serp-run-wide   --project_name "ATS Grandstand"   --city "Gurgaon"
```

You’ll get:
```
stored: artifacts/<slug>/<run_id>/serp_results.json
whitelisted_results: <N>
```

#### B) Fetch + Extract
```bash
python -m stalled_news fetch-extract   --serp_results "artifacts/<slug>/<run_id>/serp_results.json"
```

Produces:
- `evidence.json`
- `/texts/*.txt` (one file per doc_id)

#### C) Extract Events
```bash
python -m stalled_news extract-events   --evidence "artifacts/<slug>/<run_id>/evidence.json"   --project_name "ATS Grandstand"   --city "Gurgaon"   --min_conf 0.55
```

Produces:
- `events_raw.json`
- `events_deduped.json`
- `timeline.json`

#### D) Render News (JSON + HTML)
```bash
python -m stalled_news render-news   --project_name "ATS Grandstand"   --city "Gurgaon"   --run_dir "artifacts/<slug>/<run_id>"   --events "events_deduped.json"
```

Produces:
- ✅ `news.json`
- ✅ `news.html`
- ✅ `news_inputs.json`
- ✅ `news_llm_raw.json`

---

## 🧾 Output schema (news.json)

The generator writes a strict schema like:

```json
{
  "headline": "string",
  "shortSummary": "2-3 lines",
  "detailedSummary": "500-1000 words",
  "primaryDateSource": {"date": "YYYY-MM-DD|null", "domain": "string|null", "ref": "doc_id", "url": "plain text"},
  "timeline": [{"date": "YYYY-MM-DD", "event": "string", "ref": "doc_id"}],
  "latestUpdate": {"date": "YYYY-MM-DD|null", "update": "string", "ref": "doc_id"},
  "buyerImplications": ["..."],
  "investorImplications": ["..."],
  "newsCoverage": [{"title": "string", "date": "YYYY-MM-DD|null", "sourceDomain": "string", "ref": "doc_id"}],
  "sources": [{"ref": "doc_id", "domain": "string", "urlText": "plain text (no hyperlink)"}],
  "generatedAt": "ISO",
  "validUntil": "ISO"
}
```

---

## 🧪 Why timelines got polluted earlier (root cause) + fix ✅

### Root cause
Some whitelisted regulator PDFs (ex: **cause‑list PDFs**) contain **many unrelated cases** inside one document. The extractor was pulling dates from those PDFs and incorrectly attributing them to the target project, causing:
- unrelated events (other project names)
- very old dates
- junk domains

### Fix applied
We introduced **event‑level relevance gating**:
- Each candidate event must pass relevance checks using:
  - project name / key tokens
  - city (optional)
  - RERA id (if available)
- We reject events that don’t mention the project context within the same text window.
- Evidence packing in the news generator is now **relevance‑filtered**, so the LLM sees fewer but higher‑signal sources.

✅ Result: timelines are driven by **project‑relevant** evidence only.

---

## 🧭 Stepwise evolution (what we built, version‑wise)

### ✅ Step 6E — Wide SERP + robust fetch/extract
- Added “wide” search mode: more query variants (news + general)
- Stored SERP artifacts (all results + whitelisted + domain summary)
- Fetch & extract pipeline writes `evidence.json` + extracted text files

### ✅ Step 6F — Event extraction + timeline artifacts
- Extracted date‑anchored events from stored texts
- De‑duped similar events
- Stored `events_raw.json`, `events_deduped.json`, `timeline.json`

### ✅ Step 6G — Render news (OpenAI JSON mode + HTML)
- Generated:
  - `news.json` (structured)
  - `news.html` (readable)
  - `news_inputs.json` (debug)
- Enforced “JSON‑only” output from the LLM

### ✅ Fix wave — Compatibility + correctness hardening
- Compatibility fixes across models / imports
- Stable evidence format handling (`docs` wide format → compat list)
- WhitelistPolicy updated to support `allow_subdomains_for`
- **Relevance gating** to prevent unrelated PDFs polluting timelines
- Evidence packing reworked to avoid irrelevant domain packs
- CLI upgraded: extract-events accepts `--project_name --city --rera_id`

---

## 🔐 Security & repo hygiene

- Never commit `.env`
- Keep `.env.example` with placeholders only:
  - `OPENAI_API_KEY=REPLACE_WITH_YOUR_OPENAI_KEY`
- If GitHub blocks pushes due to secret scanning:
  - **rewrite history** using `git filter-repo`

---

## 🧰 Handy snippets

### Check refs coverage (news.json)
```bash
python - <<'PY'
import json
from pathlib import Path
run_dir = Path("artifacts/<slug>/<run_id>")
news = json.loads((run_dir/"news.json").read_text())
used=set()
def collect(x):
    if isinstance(x, dict):
        for k,v in x.items():
            if k=="ref" and isinstance(v,str): used.add(v)
            collect(v)
    elif isinstance(x, list):
        for i in x: collect(i)
collect(news)
sources = {s.get("ref") for s in (news.get("sources") or []) if isinstance(s,dict)}
print("refs_used:", len(used))
print("missing_in_sources:", sorted([r for r in used if r not in sources])[:20])
PY
```

### Grep your extracted texts for a date or RERA id
```bash
RUN="artifacts/<slug>/<run_id>"
rg -n "GGM/582/314/2022/57|27[./-]06[./-]2022" "$RUN/texts" | head
```

---

## 🗺️ Roadmap ideas (next)
- 🔁 Multi‑project batch runner
- 🧠 Better PDF segmentation (split large cause lists into per‑case slices)
- 🧪 Automated eval suite (hallucination checks + coverage tests)
- 🌐 FastAPI wrapper for “generate on demand” (optional)

---

## 👤 Author
Built by **Deepesh Gupta** — product + AI systems for real‑estate discovery, trust, and intelligence. 🚀
