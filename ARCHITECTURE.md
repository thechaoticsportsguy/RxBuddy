# RxBuddy Architecture Audit

A read-only snapshot of the RxBuddy codebase as of `phase-1-eval-harness` (2026-04-27). Stack: FastAPI backend (Python 3.11+), Next.js 16 frontend, Postgres (Railway-managed in production / local in dev), Anthropic Claude, Google Gemini, and public drug data APIs (RxNorm, openFDA, DailyMed, MedlinePlus, RxImage, PubMed).

## Endpoints

All routes are mounted on the FastAPI app in [backend/main.py](backend/main.py). The bulk of routes are defined on a single router in [backend/api/search.py](backend/api/search.py). **None of the routes require authentication**; the API is open to any caller within CORS scope and rate limits.

| Method | Path | Purpose | File | Auth |
|---|---|---|---|---|
| GET | `/health` | Fly.io platform health check | [backend/main.py:211](backend/main.py:211) | No |
| GET | `/` | Router-level health/liveness probe | [backend/api/search.py:812](backend/api/search.py:812) | No |
| GET | `/health` | Router-level health probe (duplicate of platform check) | [backend/api/search.py:817](backend/api/search.py:817) | No |
| POST | `/search` | Legacy search: TF-IDF + KNN + low-confidence Claude fallback | [backend/api/search.py:822](backend/api/search.py:822) | No (10/min rate limit) |
| POST | `/search/stream` | Server-Sent Events streaming variant of `/search` | [backend/api/search.py:1067](backend/api/search.py:1067) | No |
| POST | `/answer` | Direct LLM answer (no retrieval, runs `_generate_full_answer`) | [backend/api/search.py:1163](backend/api/search.py:1163) | No |
| POST | `/v2/search` | Production search via 10-step deterministic pipeline | [backend/api/search.py:1175](backend/api/search.py:1175) | No (10/min rate limit) |
| POST | `/v2/search/stream` | SSE streaming variant of `/v2/search` | [backend/api/search.py:1200](backend/api/search.py:1200) | No |
| POST | `/v2/chat` | Drug-specific chat widget; cached in `drug_chat_cache` | [backend/api/search.py:1255](backend/api/search.py:1255) | No (30/min rate limit) |
| GET | `/drug-image` | Returns category-tinted SVG pill for a drug name | [backend/api/search.py:1318](backend/api/search.py:1318) | No |
| GET | `/pill-image` | Real-pill image lookup via RxImage | [backend/api/search.py:1330](backend/api/search.py:1330) | No |
| GET | `/drug-index` | Browseable A–Z drug index (cached in-memory) | [backend/api/search.py:1343](backend/api/search.py:1343) | No |
| POST | `/log` | Records a search event to `search_logs` | [backend/api/search.py:1368](backend/api/search.py:1368) | No |
| GET | `/admin/cache-stats` | Returns `label_updater` in-memory cache stats | [backend/api/search.py:1387](backend/api/search.py:1387) | No (no admin guard!) |
| POST | `/admin/refresh-label` | Forces a re-fetch of an FDA label for a single drug | [backend/api/search.py:1394](backend/api/search.py:1394) | No (no admin guard!) |
| GET | `/api/drugs/{drug_name}/side-effects` | Structured side-effect tiers from DB or live parse | [backend/api/search.py:1422](backend/api/search.py:1422) | No |
| POST | `/api/drugs/{drug_name}/parse-label` | Triggers Gemini parse of an FDA label into the DB | [backend/api/search.py:1464](backend/api/search.py:1464) | No (per-drug 2/hour throttle only) |

> **Note.** `/admin/*` endpoints are not authenticated. Anyone can flush a label cache or read cache stats. See *Open Questions* below.

## External APIs

Every third-party service the backend calls.

| API | Base URL | Auth | Env Var | Backend file(s) |
|---|---|---|---|---|
| **openFDA** (label, FAERS, NDC, recalls) | `https://api.fda.gov/drug` | Optional API key | `OPENFDA_API_KEY` (not in `.env.example`) | [backend/services/openfda_client.py](backend/services/openfda_client.py), [backend/services/fda_client.py](backend/services/fda_client.py), [backend/pipeline/api_layer.py](backend/pipeline/api_layer.py) |
| **RxNorm** (drug name normalization, RxCUI) | `https://rxnav.nlm.nih.gov/REST` | None (public) | n/a | [backend/services/rxnorm_client.py](backend/services/rxnorm_client.py), [backend/rxnorm_client.py](backend/rxnorm_client.py), [backend/pipeline/api_layer.py](backend/pipeline/api_layer.py) |
| **DailyMed** (SPL labels) | `https://dailymed.nlm.nih.gov/dailymed/services/v2` | None | n/a | [backend/services/dailymed_client.py](backend/services/dailymed_client.py), [backend/pipeline/api_layer.py](backend/pipeline/api_layer.py) |
| **MedlinePlus** (consumer health summaries) | `https://wsearch.nlm.nih.gov/ws/query`, `https://connect.medlineplus.gov/application` | None | n/a | [backend/services/medlineplus_client.py](backend/services/medlineplus_client.py) |
| **RxImage** (real pill photos) | `https://rximage.nlm.nih.gov/api/rximage/1/rxbase` | None | n/a | [backend/services/fda_client.py](backend/services/fda_client.py) |
| **PubMed** (E-utilities, study lookups) | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils` | None | n/a | [backend/services/fda_client.py](backend/services/fda_client.py) |
| **Anthropic Claude** | `https://api.anthropic.com` | Bearer (API key) | `ANTHROPIC_API_KEY` | [backend/services/claude_client.py](backend/services/claude_client.py), [backend/pipeline/claude_explainer.py](backend/pipeline/claude_explainer.py), [backend/api/search.py](backend/api/search.py) |
| **Google Gemini** | `https://generativelanguage.googleapis.com` (via `google-genai` SDK) | API key | `GEMINI_API_KEY` (not in `.env.example`) | [backend/pipeline/side_effects_store.py](backend/pipeline/side_effects_store.py) |

All HTTP clients use `httpx` or `aiohttp` with 5–15s timeouts and degrade gracefully (return empty data) on failure.

## LLM Call Sites

Every place the codebase invokes a Claude or Gemini model.

| File | Function | Model | System prompt (1-line) | Response usage |
|---|---|---|---|---|
| [backend/services/claude_client.py](backend/services/claude_client.py) | `generate_ai_answer` | `claude-sonnet-4-20250514` | Intent-aware FDA-grounded answer generator that emits VERDICT / ANSWER / WARNING / DETAILS / ACTION / ARTICLE / CONFIDENCE / SOURCES sections. | Primary patient-facing answer string. |
| [backend/services/claude_client.py](backend/services/claude_client.py) | `validate_ai_answer` | `claude-haiku-4-5-20251001` | Post-generation consistency check: verifies VERDICT matches answer body and SAFE→CAUTION→AVOID hierarchy. | Re-writes answer when verdict mismatches; returned as the final answer text. |
| [backend/services/claude_client.py](backend/services/claude_client.py) | `is_valid_pharmacy_question` | `claude-haiku-4-5` | Yes/No classifier: is this a real medication FAQ worth saving to DB? | Gates `_save_question_to_db` self-learning insert. |
| [backend/services/claude_client.py](backend/services/claude_client.py) | `get_best_category` | `claude-haiku-4-5` | Multi-class classifier into `PHARMACY_CATEGORIES`. | Sets `category` column when persisting a new question. |
| [backend/pipeline/claude_explainer.py](backend/pipeline/claude_explainer.py) | `generate_explanation` | `claude-opus-4-1` (with Haiku fallback) | Explanation-only writer that must respect a verdict already locked by the decision engine. | 1-2 sentence answer + warning + 2-3 actions for the v2 pipeline. |
| [backend/pipeline/side_effects_store.py](backend/pipeline/side_effects_store.py) | `parse_label_with_gemini` | `gemini-2.0-flash` (via `google.genai`) | Structured-JSON parser that converts FDA `adverse_reactions` text into frequency-tier records (very_common / common / uncommon / rare / serious) with confidence scores and red-flag flags. | Persisted as rows in `drug_side_effects` and metadata in `drug_se_meta`. |
| [backend/api/search.py](backend/api/search.py) | `chat_v2` | `claude-haiku-4-5-20251001` | "Talk like the user is 15 years old" persona with ≤30-word, plain-text replies, scoped to a single drug. | Returned to the frontend chat widget; cached in `drug_chat_cache`. |

## Database Tables

Source of truth: SQLAlchemy `Table` objects in [backend/core/db.py](backend/core/db.py) plus `CREATE TABLE IF NOT EXISTS` and runtime `ALTER TABLE` statements in [backend/pipeline/side_effects_store.py](backend/pipeline/side_effects_store.py). **There are no Alembic migrations.**

### `questions`
| Column | Type | Notes |
|---|---|---|
| `id` | `Integer` | PK |
| `question` | `Text` | NOT NULL |
| `category` | `String(50)` | nullable |
| `tags` | `ARRAY(Text)` | Postgres-specific |
| `answer` | `Text` | nullable |
| `created_at` | `DateTime(timezone=True)` | nullable |

No FKs, no indexes declared.

### `search_logs`
| Column | Type | Notes |
|---|---|---|
| `id` | `Integer` | PK |
| `query` | `Text` | NOT NULL |
| `matched_question_id` | `Integer` | FK → `questions.id` (nullable) |
| `clicked` | `Boolean` | default `false` |
| `session_id` | `String(100)` | nullable |
| `searched_at` | `DateTime(timezone=True)` | NOT NULL |

### `drug_chat_cache`
| Column | Type | Notes |
|---|---|---|
| `id` | `Integer` | PK, autoincrement |
| `drug_name` | `Text` | NOT NULL |
| `question` | `Text` | NOT NULL |
| `answer` | `Text` | NOT NULL |
| `created_at` | `DateTime` | server_default `now()` |

No unique constraint on `(drug_name, question)` — duplicate rows are possible if cache lookup misses.

### `drug_se_meta` (created on demand by `side_effects_store.ensure_tables()`)
| Column | Type | Notes |
|---|---|---|
| `drug_generic_name` | `VARCHAR(255)` | PK |
| `brand_names` | `TEXT[]` | |
| `generic_name_label` | `VARCHAR(255)` | |
| `boxed_warnings` | `TEXT[]` | |
| `moa_summary` | `TEXT` | |
| `moa_detail` | `TEXT` | |
| `pharmacologic_class` | `VARCHAR(500)` | |
| `molecular_targets` | `TEXT[]` | |
| `dailymed_url` | `TEXT` | |
| `fda_url` | `TEXT` | |
| `label_date` | `VARCHAR(20)` | |
| `overall_confidence` | `FLOAT` | |
| `parsed_at` | `TIMESTAMP WITH TIME ZONE` | |

### `drug_side_effects` (created on demand)
| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL` | PK |
| `drug_generic_name` | `VARCHAR(255)` | NOT NULL |
| `display_name` | `VARCHAR(500)` | NOT NULL |
| `frequency_category` | `VARCHAR(20)` | very_common / common / uncommon / rare / serious |
| `frequency_percent` | `FLOAT` | |
| `confidence_score` | `FLOAT` | |
| `severity` | `VARCHAR(20)` | |
| `patient_description` | `TEXT` | |
| `onset_days` | `INT` | |
| `resolution_days` | `INT` | |
| `management` | `VARCHAR(100)` | |
| `red_flag` | `BOOLEAN` | |
| `red_flag_reason` | `TEXT` | |
| `evidence_tier` | `VARCHAR(20)` | |
| `source_section` | `VARCHAR(100)` | |
| `source_quote` | `TEXT` | |
| `updated_at` | `TIMESTAMP WITH TIME ZONE` | |

UNIQUE constraint on `(drug_generic_name, display_name)`. Several columns are added retroactively via runtime `ALTER TABLE ADD COLUMN IF NOT EXISTS` for backwards compatibility.

## Frontend Pages

The frontend is Next.js 16 (Pages Router), React 19, Tailwind CSS 4. There are exactly three page modules.

| Route | File | Backend endpoints called | Auth |
|---|---|---|---|
| `/` (home) | [frontend/pages/index.js](frontend/pages/index.js) | None directly — submits navigate to `/results?q=…` | No |
| `/results` | [frontend/pages/results.js](frontend/pages/results.js) | `POST /v2/search`, `POST /v2/search/stream`, `POST /v2/chat`, `GET /drug-image`, `GET /pill-image`, `GET /api/drugs/{drug}/side-effects` | No |
| `_app` | [frontend/pages/_app.js](frontend/pages/_app.js) | Global app wrapper (no API calls) | No |

The home page is purely client-side: it gathers a query (text or Web Speech API voice), then routes the user to `/results`, which is where every backend interaction happens.

## Known Hot Paths

Inferred from frontend usage and the rate-limit annotations.

1. **`POST /v2/search`** — every form submission, autocomplete, and streaming search lands here. Cached at two levels: in-memory cache in [backend/pipeline/cache.py](backend/pipeline/cache.py) (key = normalized query), and the legacy `questions` table acts as a self-learning cache (Claude answers are persisted on first miss). **Status: partially cached** (in-memory + DB write-through; no Redis in production despite `REDIS_URL` setting existing).
2. **`GET /drug-image`** — invoked once per drug card on the results page. Just maps drug → category → static SVG; effectively O(1). **Status: not cached** (and doesn't need to be — pure computation).
3. **`POST /v2/chat`** — chat widget on results page. Reads `drug_chat_cache` table for an exact `(drug, question)` hit before calling Claude Haiku. **Status: cached** (DB-backed, no TTL — entries are forever unless manually purged).

Secondary hot paths: `GET /api/drugs/{drug}/side-effects` (called once per result), `GET /pill-image` (RxImage lookup, no caching).

## Open Questions

- [ ] **`/admin/*` is unauthenticated.** Anyone on the internet can hit `/admin/cache-stats` and `/admin/refresh-label`. Decide: drop a shared-secret header, IP-allowlist, or remove from prod.
- [ ] **`OPENFDA_API_KEY` and `GEMINI_API_KEY` are missing from [.env.example](.env.example).** Devs spinning up locally have no signal that Gemini parsing requires a key.
- [ ] **No Alembic migrations.** Schema evolves via `ensure_tables()` and runtime `ALTER TABLE` calls. Risk: drifting schemas across environments and no rollback path.
- [ ] **Two duplicate `/health` routes** — one on the FastAPI app, one on the router. Harmless, but noise.
- [ ] **Two duplicate `rxnorm_client` modules** — [backend/rxnorm_client.py](backend/rxnorm_client.py) and [backend/services/rxnorm_client.py](backend/services/rxnorm_client.py). Unclear which is canonical.
- [ ] **`drug_chat_cache` has no TTL or unique constraint.** Stale Claude answers will be served indefinitely; duplicate rows accumulate on race.
- [ ] **`REDIS_URL` is in `core.config.Settings` but unused** in any code path I could find. Either wire it up or remove the setting.
- [ ] **`backend/dashboard/`** and the Streamlit dashboard mentioned in the README don't appear to be referenced from any deploy config. Likely abandoned.
- [ ] **`backend/ml/knn_search.py` and the `engine: "knn"` parameter** on `/search` — no frontend page sends `engine: "knn"`. Probably dead surface area.
- [ ] **Two intent classification paths** — `_classify_query_intent` (in `api/search.py`) and `pipeline/classifier.py:classify_fast`. Drift risk; pick one.
- [ ] **Tests live in [backend/tests/](backend/tests/) and [tests/](tests/)** but no CI workflow runs them yet (this is what Phase 1.5 starts to fix).
- [ ] **`Weekly_Planner.xlsx` and `.~lock.Weekly_Planner.xlsx#`** are committed at the repo root. Almost certainly accidental; not referenced anywhere in code.
- [ ] **Hardcoded CORS origin list** in [backend/main.py:46](backend/main.py:46) duplicates the regex allow `https://*.vercel.app$`. Pick one source of truth.
- [ ] **`/api/drugs/{drug}/parse-label` is rate-limited per-drug to 2/hour but is otherwise unauthenticated.** A motivated attacker can drive Gemini cost by enumerating drug names.
