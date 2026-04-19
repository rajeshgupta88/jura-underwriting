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

## Architecture

```
config/          YAML rule files (admitted states, DOI rules, surplus lines, moratoriums)
jura/            Core package — server, checker, router, models, LLM client
compliance/      HITL inbox and review workflows
data/            Jurisdiction log, hold notices, disclosures, ES notices
templates/       Jinja2 templates for generated notices
```
