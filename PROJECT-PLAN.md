# Chef App — Project Plan & Grounding Notes

Living document. Read this first in any new session to recover context. Update it whenever a phase completes, a decision is made, or something is discovered that the next session needs to know.

## What this is

A locally hosted, AI-driven, web-connected meal planning, food inventory, and recipe management app for a household (default 2 people). Runs via Docker Compose. Ships on GitHub so others can pull it down and self-host. Uses Ollama for local LLM inference and Tavily for web search grounding.

## Origin note

This project supersedes an earlier prototype built inside a personal "Local AI" Open WebUI stack (Ollama + mcpo + ComfyUI + n8n + a `chef-api` container, Excel workbook as datastore). That prototype is not part of this repo. Some of its design patterns (interactive tool surface, autonomous weekly meal-plan job, avoiding spreadsheet-as-database) were salvaged and are reflected in the decisions below.

## Author's environment

- CPU: Intel 6700K, Z170A chipset motherboard
- GPU: dual GTX 1080 Ti (second card runs headless — usable for compute/second Ollama model or ComfyUI-style workloads, not display)
- Ollama: already available in the environment (assume host-installed unless told otherwise)
- Tavily: API key already held by the author
- Docker host: WSL2 Debian. The project folder (`C:\Users\JBentley\Claude\Projects\Chef` on Windows) is reached from the WSL Debian docker host at:
  ```
  /mnt/c/Users/JBentley/Claude/Projects/chef
  ```
  Run `docker compose` commands from that path inside WSL, not from a Windows shell.

## Architecture decisions (confirmed 2026-07-30)

| Decision | Choice | Why |
|---|---|---|
| Backend | Python + FastAPI | Best local-LLM/Ollama ecosystem support, async, auto API docs, matches prior prototype's language |
| Frontend | React (Vite) SPA | Needed for a chat panel that stays alive across page navigation; richer interactivity for inventory/recipe editing |
| Database | SQLite (file-backed, via SQLAlchemy + Alembic) | "Lightweight local DB" per project brief; avoids the Excel file-locking problem hit in the prior prototype |
| Styling | Single centralized CSS (CSS variables / design tokens) imported app-wide | Project brief requires one-source theming |
| Containerization | Docker Compose: `backend`, `frontend` services, SQLite + uploaded images on a named volume for persistence across rebuilds | Required by project brief |
| LLM | Ollama, base URL configurable via `.env` / Settings GUI (defaults to `http://host.docker.internal:11434`, override for a containerized Ollama) | Author already runs Ollama in his environment |
| Web search | Tavily API, key entered via Settings GUI or `.env`, never hardcoded | Author has an existing key |
| Settings/secrets storage | Database (`AppSetting` table), not `.env`. `.env` only for infra bootstrap (`DATABASE_URL`, ports) plus optional first-run seed convenience. Secrets encrypted at rest with Fernet, key rotation supported | Author's explicit preference (2026-07-30), matching the pattern already validated in the sibling Fiduciary project — see "Settings & secrets storage" section below |

Open question for a future session: does the author want Ollama itself running inside this repo's Docker Compose (with GPU passthrough configured for the 1080 Tis), or does it stay a separate/host service that this app just points at? Scaffolding currently assumes the latter (external Ollama) since "Ollama is available in the environment" was stated as a given. Revisit if the author wants it bundled.

## Repo layout

```
Chef/
  PROJECT-PLAN.md        <- this file, keep current
  README.md               <- setup instructions for external users
  .env.example
  docker-compose.yml
  backend/
    Dockerfile
    requirements.txt
    app/
      main.py            <- FastAPI app entrypoint
      config.py          <- settings (pydantic-settings, reads .env)
      database.py        <- SQLAlchemy engine/session
      models/            <- ORM models (Phase 1+)
      routers/           <- API routers (Phase 1+)
      services/          <- business logic, Ollama/Tavily clients (Phase 2+)
  frontend/
    Dockerfile
    package.json
    index.html
    vite.config.js
    src/
      main.jsx
      App.jsx
      styles/theme.css   <- centralized design tokens
      pages/
      components/
  data/                  <- gitignored; SQLite file + uploaded images live here, volume-mounted
```

## Phased task list (priority order)

- [x] **Phase 0 — Scaffolding.** Repo structure, docker-compose skeleton, backend/frontend hello-world, this plan doc. (2026-07-30)
- [x] **Phase 1 — Core data layer.** SQLAlchemy models, Alembic migrations, seed script. (2026-07-30)
- [ ] **Phase 2 — AI integration.** Ollama client wrapper; editable main Chef system prompt (stored in DB, editable via GUI); follow-on dietary-preferences onboarding prompt/flow; Tavily web search integration; vision-capable model wiring for inventory photo intake.
- [ ] **Phase 3 — Inventory management.** CRUD API + GUI for pantry/fridge/produce/spices with qty + expiration; image-upload vision parsing (detect items, estimate qty/expiration); expiration + "forgotten item" scoring; natural-language chat updates (deduct ingredients on confirmed meals, handle skipped meals).
- [ ] **Phase 4 — Recipe management.** Recipe model (ingredients, steps, prep/cook time, nutrition, calories); servings-based scaling (default = household size); import from file/image/text; ratings + "staple" flag.
- [ ] **Phase 5 — Meal planning engine.** Weekly generation balancing nutrition with an occasional indulgence; household-size aware; prioritizes expiring/long-unused ingredients; boosts user-flagged priority ingredients (e.g. "use up the lentils"); aware of available equipment/kitchen setup (home, camping, RV, short-term rental); per-meal tags (quick, portable, non-refrigerated, dutch-oven-only, backpacking, etc.); grocery list generation (meal plan minus current inventory).
- [ ] **Phase 6 — Health & nutrition tracking.** Body metrics (age/height/weight) feeding BMI-aware plan steering; weight/cholesterol/bloodwork trend tracking with charts; nutritionist knowledge-file import to ground the AI and guide chat.
- [ ] **Phase 7 — Persistent chat system.** Chat history storage; chat keeps running/visible in the background while navigating the app; chat-driven inventory/meal-plan actions via natural language.
- [ ] **Phase 8 — Theming, settings GUI, persistence.** Centralized CSS theming; Settings GUI for secrets/keys (Tavily, Ollama endpoint), prompts, household size, and other user-customizable values; verify all data survives container rebuilds via volumes.
- [ ] **Phase 9 — Packaging & GitHub distribution.** Finalize docker-compose (GPU passthrough notes for dual 1080 Ti if Ollama gets containerized); README with setup instructions for external users; push to GitHub.
- [ ] **Phase 10 — Testing & QA.** Backend unit/integration tests; end-to-end smoke tests; manual QA pass against the full feature checklist in the project brief.

## Author's stated dietary/health context (for grounding meal-plan design, Phase 5/6)

- Gluten-free, celiac-friendly focus
- Quick preparations preferred, leftovers welcome
- Sedentary lifestyle, goal to reduce LDL/bad cholesterol
- Weight loss not a core goal, but both household members ~20 lbs over ideal weight
- Household size default: 2

## Notes / gotchas

- Do not reintroduce a spreadsheet as the live datastore — caused file-lock issues in the prior prototype. SQLite file lives under `data/`, only the backend container writes to it.
- Keep secrets (Tavily key, etc.) out of version control — `.env` is gitignored, `.env.example` documents required vars.
- Fiduciary Project (a sibling app the author built) may hold reusable patterns, especially for persistent background chat — not accessible in this workspace yet. Ask the author to share it if deeper inspiration is wanted.

## GitHub repository

https://github.com/txAlphaMale/ai-chef-and-nutritionist (public; renamed from the initial `chef` slug on 2026-07-30 at the author's request — GitHub repo names can't contain spaces/`&`, so "AI Chef & Nutritionist" became this slug, with the full name kept in the README title and repo description)

The author has a "GitHub Integration" connector shown as Connected in Settings, but it did not surface any tools in the session where the repo was created (2026-07-30) — connectors load at session start, so it likely needs a fresh conversation to pick up. Until confirmed working, assume no GitHub connector is available: Claude's sandbox has no route to push authenticated git operations (no `gh` CLI available — not installable, no root — and Claude does not handle GitHub tokens/passwords on the user's behalf under any circumstances). Claude created/renamed the repo via browser automation (Chrome, already-authenticated session, no credentials entered) and configured the local `origin` remote + `main` branch. **The author must run the actual `git push` locally** — local git/credential manager handles auth, Claude never sees a token. Command, run from the `Chef` folder:
```
git push -u origin main
```
After that succeeds, future commits just need `git push`.

## Phase 1 schema notes

15 tables, defined under `backend/app/models/` (one file per domain area) and imported through `app/models/__init__.py` so Alembic autogenerate and relationship string-lookups both see the full set:

- **inventory.py** — `InventoryItem` (category, quantity/unit, location, purchased/expiration/last-used dates, `is_priority` flag + note for "use this up" boosting, source of entry: manual/vision/chat).
- **kitchen.py** — `KitchenProfile` (name, JSON equipment list, `is_active`) for home vs. camping/RV/rental setups.
- **recipe.py** — `Recipe` (default_servings for scaling, JSON instructions list, JSON nutrition dict, rating, `is_staple`, source), `RecipeIngredient`, `MealTag` + `recipe_tag_links` join table (seeded tags: quick, portable, non_refrigerated, dutch_oven_only, backpacking, one_pot, make_ahead, freezer_friendly, kid_friendly, gluten_free).
- **meal_plan.py** — `MealPlan`, `MealPlanEntry` (day/meal_type/recipe/servings, `is_confirmed` is the hook that should trigger inventory deduction in Phase 3, `is_skipped` for chat-driven skip handling), `GroceryListItem`.
- **household.py** — `HouseholdPreferences` (singleton row, household_size default 2, dietary_restrictions JSON, goals, indulgence_frequency), `HouseholdMember` (age/height/sex/activity_level for BMI-aware planning in Phase 6).
- **health.py** — `HealthMetricEntry` (weight, BMI, LDL/HDL/total cholesterol, triglycerides, blood pressure, glucose) tied to a household member, for Phase 6 trend charts.
- **chat.py** — `ChatMessage` (session_id, role, content, timestamp) backing the persistent/background chat in Phase 7.
- **settings.py** — `AppSetting` (key/value/is_secret, for the Settings GUI in Phase 8), `SystemPrompt` (prompt_key: `main_chef` / `dietary_onboarding`, editable content — Phase 2 wires these into the Ollama client), `KnowledgeFile` (imported nutritionist grounding docs, Phase 6).

**Seed data is deliberately generic**, not the author's personal profile — `HouseholdPreferences` seeds with household_size=2 and no dietary restrictions pre-filled, since this repo is meant for other households to pull down and configure for themselves via onboarding/Settings. The `main_chef` and `dietary_onboarding` system prompts are seeded with real content per the project brief (nutritionist-chef persona, confirm-before-write, expiring/priority-ingredient bias, occasional indulgence).

`backend/docker-entrypoint.sh` runs `alembic upgrade head` then `python -m app.seed` (both idempotent) before starting uvicorn, so the DB bootstraps itself on first `docker compose up` and stays current on every restart without manual steps.

## Settings & secrets storage

Ported from `Fiduciary/portfolio-api/secrets_crypto.py` (a validated, tested module in the author's sibling project, read directly on 2026-07-30 — not reinvented from scratch) into `backend/app/services/secrets_crypto.py`:

- Fernet (AES-128-CBC + HMAC, via the `cryptography` package) symmetric encryption for any `AppSetting` row where `is_secret=True` (currently just `tavily_api_key`).
- Key file at `/app/data/secrets.key` (same Docker volume as the SQLite DB), generated on first run, written atomically with 0600 permissions. **Losing this file makes every encrypted value permanently unrecoverable** — back it up alongside `./data`.
- Versioned envelope encryption from day one so key rotation is possible later without a bulk re-encrypt: unrotated deployments produce unprefixed ciphertext (the common case); `rotate_key()` adds a new key version to `/app/data/secrets_keyring.json`, all new encrypts use it, old ciphertext keeps decrypting under its original key forever.
- `decrypt_or_legacy()` never raises — falls back to returning the raw value if it isn't a valid token, for defensive handling of pre-encryption data.

`backend/app/services/settings_service.py` wraps this with a `SETTING_SPECS` registry (key, label, `is_secret`, default, description, optional `.env` fallback name) and `get_setting()`/`set_setting()`/`list_settings_for_display()` (secrets masked as `********` for API responses). `app/seed.py` uses this to seed each setting once — from an optional `.env` value if present, otherwise the spec default — and never overwrites a value that already exists, whether set via `.env` or later via the Settings UI (Phase 8).

`app/config.py` (pydantic `Settings`) now holds ONLY true infra bootstrap values (`database_url`, `backend_port`) that must exist before the DB is reachable. Everything else moved out of it: Ollama base URL/models and the Tavily key are DB-backed via `settings_service`; household size lives in `HouseholdPreferences` (already the case since Phase 1).

`app/services/ollama_client.py` and `app/services/tavily_client.py` are thin wrappers that pull their config from `settings_service` per-call (not cached at import time), so a Settings UI edit takes effect on the next request without a restart.

**Known sandbox-only quirk (not a bug):** importing the `ollama` package fails inside Claude's sandbox because ambient `ALL_PROXY=socks5h://...` env vars (from the sandbox's own network setup) break `ollama`'s eager default-client construction at import time. Confirmed this doesn't happen with those proxy vars unset, and the real Docker deployment won't have them set. Local verification of `ollama_client.py`/`tavily_client.py` therefore ran with `env -u ALL_PROXY -u ...`; no code changes were needed to work around it.

## Session log

- **2026-07-30**: Reviewed prior prototype bookmark file (now deleted, insights preserved here and in Claude's memory), confirmed architecture decisions (FastAPI + React + SQLite), created 11-phase task list, scaffolded Phase 0 (repo structure, docker-compose, backend/frontend skeletons). Created GitHub repo (public) via browser automation, configured local `origin` remote and `main` branch, author pushed successfully. Renamed repo to `ai-chef-and-nutritionist` at author's request. Noted author's Docker host is WSL2 Debian, project folder reachable there at `/mnt/c/Users/JBentley/Claude/Projects/chef`. Completed Phase 1: 15-table SQLAlchemy schema across 8 model files, Alembic migration, idempotent seed script, wired into `docker-entrypoint.sh` so the DB bootstraps automatically on container start. Verified end-to-end locally (fresh DB → migrate → seed → tables confirmed) outside Docker since no Docker daemon is available in Claude's sandbox. Author requested DB-backed settings/secrets (not `.env`) with encryption matching the Fiduciary project's `secrets_crypto.py` — connected the Fiduciary folder, read that module directly, ported it into `backend/app/services/secrets_crypto.py` + a new `settings_service.py`, rewrote `app/config.py`/`seed.py`/`.env.example`/`README.md` accordingly, and built `ollama_client.py`/`tavily_client.py` + a read-only `/api/system/*` router on top (Phase 2 start). Verified end-to-end: fresh DB, `TAVILY_API_KEY` seeded from env comes out encrypted in the raw SQLite row, decrypts correctly through the settings service, `secrets.key` has 0600 perms, and all `/api/system/*` endpoints return correct data through a live local uvicorn instance (Ollama itself unreachable from the sandbox, as expected — `ollama_reachable: false` is the correct result here, not a bug).
