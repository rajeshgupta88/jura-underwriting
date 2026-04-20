# Jura — Jurisdiction & Regulatory Authority Agent

Jura is a state jurisdiction eligibility checker for small commercial insurance underwriting. It runs **before Aria (W2-B)** on **port 8003** and determines whether a risk is eligible for admitted market placement in a given state, or must be routed to surplus lines — with full regulatory rationale.

All jurisdiction rules are **deterministic YAML lookups**. The LLM is used only for generating plain-language regulatory rationale summaries.

---

## Setup

### 1. Activate the virtual environment

```bash
source .venv/bin/activate
```

### 2. Configure API keys

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

### 3. Provider switching

Edit `config/llm_config.yaml` and set `provider:` to either `openai` or `anthropic`.

---

## Running Jura

```bash
source .venv/bin/activate
uvicorn jura.server:app --port 8003 --reload
```

---

## Running tests

```bash
source .venv/bin/activate
pytest tests/
```

---

## Running the Jura exec demo

The exec demo seeds 5 sample submissions and displays results in a Rich summary table.

```bash
source .venv/bin/activate

# Seed submissions and print summary (server must be running separately, or it auto-starts)
python run_demo.py

# Seed submissions AND open the browser to the exec UI
python run_demo.py --demo
```

### Exec demo screens

| Screen | URL | Description |
|---|---|---|
| Submission queue | `http://localhost:8003/` | All submissions with filter bar |
| Jurisdiction blocks | `http://localhost:8003/blocks` | Blocked subs + pattern detection |
| Audit log | `http://localhost:8003/audit` | SHA-256 tamper-evident log |
| Insights | `http://localhost:8003/insights` | Clear rate · SLA · governance health |
| Compliance review | `http://localhost:8003/compliance` | Disclosure review portal |

---

## Running Jura + Aria together

Jura (port 8003) routes to Aria (port 8001) via `POST /score`. Set `ARIA_ENDPOINT` to override the default:

```bash
# Terminal 1 — start Aria (W2-B)
cd ../aria-underwriting
uvicorn aria.server:app --port 8001

# Terminal 2 — start Jura
cd ../jura-underwriting
HITL_MODE=browser uvicorn jura.server:app --port 8003 --reload
```

---

## Architecture

```
config/          YAML rule files (admitted states, DOI rules, surplus lines, moratoriums)
jura/            Core package — server, checker, router, models, LLM client
compliance/      HITL inbox and review workflows
data/            Jurisdiction log, hold notices, disclosures, ES notices
templates/       Jinja2 templates for generated notices
```
