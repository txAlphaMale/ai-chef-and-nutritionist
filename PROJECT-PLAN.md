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
- [x] **Phase 2 — AI integration.** Ollama/Tavily clients, DB-backed settings + encryption, read-only /api/system/* router. (2026-07-30)
- [x] **Phase 3 — Inventory management.** CRUD API + GUI, urgency scoring, vision photo intake, deduction primitive. (2026-07-30)
- [x] **Phase 4 — Recipe management.** CRUD API + GUI, servings scaling, ratings/staple flag, AI import from text/PDF/photo. (2026-07-30)
- [x] **Phase 5 — Meal planning engine.** Weekly generation balancing nutrition with an occasional indulgence; household-size aware; prioritizes expiring/long-unused ingredients; boosts user-flagged priority ingredients (e.g. "use up the lentils"); aware of available equipment/kitchen setup (home, camping, RV, short-term rental); per-meal tags (quick, portable, non-refrigerated, dutch-oven-only, backpacking, etc.); grocery list generation (meal plan minus current inventory). (2026-07-30)
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

## Phase 3 notes: inventory

Backend (`backend/app/routers/inventory.py`, `app/services/inventory_service.py`, `app/schemas/inventory.py`):

- Full CRUD: `GET/POST /api/inventory`, `GET/PATCH/DELETE /api/inventory/{id}`, with `category`/`is_priority`/`search` filters on the list endpoint.
- **Urgency scoring** (`compute_urgency`): points for expired (100) / expiring ≤3d (80) / ≤7d (50) / ≤14d (20) days, staleness (unused >60d: 30, >30d: 15), and the `is_priority` flag (25) — plus human-readable reasons. `GET /api/inventory/priority-suggestions` returns items ranked by this score; the meal-planning engine (Phase 5) is meant to consume the same function directly, not just the endpoint.
- **Vision photo intake**: `POST /api/inventory/vision-intake` sends the uploaded image + a JSON-array-only prompt to the configured Ollama vision model and returns a *preview* (nothing written to inventory yet). `POST /api/inventory/vision-intake/confirm` bulk-creates from a (user-reviewed/edited) item list. Response parsing (`parse_vision_response`) is defensive — tries strict JSON, falls back to extracting the first `[...]` block — because real vision-model output often wraps JSON in prose/markdown fences; unit-tested against both cases plus garbage input and an invalid category value.
- **Deduction primitive** (`deduct_by_name`): case-insensitive exact-then-substring match against inventory by name, decrements quantity floored at 0, stamps `last_used_date`. This is what Phase 5 (confirming a meal-plan entry) and Phase 7 (chat: "we made X" / "we're out of Y") will call — matching recipe ingredient names to inventory names is inherently fuzzy; a smarter matcher (aliases, unit conversion) is a documented future improvement, not attempted here.

Frontend (`frontend/src/pages/InventoryPage.jsx`, `components/InventoryItemForm.jsx`): category filter, add/edit form (shared component), delete, and a photo-upload flow that shows the detected-items preview before anything is committed. Rows are color-coded by urgency score using the same reasons the backend computed. Switched to `HashRouter` (was a single static page) with a nav bar for Home/Inventory.

**Known simplification: frontend/backend origins.** Docker Compose runs `frontend` (5173) and `backend` (8095) as separate containers with no reverse proxy between them. In dev (`npm run dev`), the vite proxy in `vite.config.js` makes `/api/*` and `/health` same-origin. In the production Docker build, `frontend/src/api.js` falls back to hitting the backend directly at `${hostname}:8095` (hardcoded port, matching `.env`'s `BACKEND_PORT` default) using the backend's wide-open CORS. This works for the common single-host case but is fragile if `BACKEND_PORT` is changed without updating `api.js` too, and doesn't support serving over HTTPS/a real domain cleanly. A shared reverse proxy (nginx/Caddy sidecar, single external port) is a reasonable Phase 9 packaging improvement.

**Verification:** unit tests for `parse_vision_response` (4 cases) and `deduct_by_name` (exact match, fuzzy match, no match, floor-at-zero) run directly against the service module; full CRUD + priority-suggestions + bulk vision-confirm exercised via curl against a live local uvicorn instance; `vision-intake` itself confirmed to fail cleanly with a 502 (no live Ollama in the sandbox) rather than crashing. Frontend: `npm run build` succeeds; ran the real Vite dev server against the real backend together and hit `/health` and `/api/inventory` (including a POST) through the actual proxy path the browser will use, not just each side in isolation.

## Operational note: bash sandbox / native file coherence

Claude's bash sandbox sees this folder through a FUSE mount that can cache stale file sizes -- if Claude edits a file with the Read/Write/Edit tools and then reads/git-adds/executes it via bash in the same session, the bash side can occasionally get truncated/stale content instead of the true file (documented and verified in the sibling Fiduciary project, `docs/COWORK-FUSE-COHERENCE.md`). Checked on 2026-07-30: every file that had been through this mixed edit pattern so far (`config.py`, `main.py`, `.gitignore`, `README.md`, `.env.example`, `seed.py`, this file) was verified intact -- Read tool content matched `git show HEAD:<path>` exactly, no corruption found. Going forward, prefer a single writer surface (bash heredoc/sed, or the Edit/Write tool) per file for its whole edit-test-commit cycle, and spot-check with the Read tool before trusting a bash-side commit of a file that was just edited natively.

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

## Phase 4 notes: recipes

Backend (`backend/app/routers/recipes.py`, `app/services/recipe_service.py`, `app/schemas/recipe.py`):

- Full CRUD: `GET/POST /api/recipes`, `GET/PATCH/DELETE /api/recipes/{id}`, with `is_staple`/`tag`/`search` filters. `POST /{id}/rating` validates 1–5 (422 outside that range).
- **Servings scaling**: `GET /api/recipes/{id}?servings=N` returns ingredient quantities scaled by `N / default_servings` (`scale_ingredients`) — nutrition is stored per-serving already (see Phase 1 schema notes) so it's returned as-is, not multiplied. `None`-quantity ingredients (e.g. "salt to taste") pass through unscaled rather than erroring.
- **Tags**: get-or-create by name (`resolve_tags`) — reusing an existing tag name (e.g. one of the 10 seeded in Phase 1) does not create a duplicate `MealTag` row, verified directly against the DB.
- **AI-assisted import** (`POST /api/recipes/import`, multipart): accepts a `text` form field OR an uploaded file, dispatching by content type — images go to the Ollama vision model, PDFs are text-extracted via `pypdf` then sent to the chat model, plain text files are decoded and sent to the chat model. Returns a *preview* (`RecipeImportResponse`), nothing is saved until the user reviews/edits and the frontend POSTs to the normal create endpoint — same preview-then-confirm pattern as Phase 3's vision intake. `parse_recipe_response` defensively extracts a JSON object from real-world model output (prose/markdown-wrapped or strict), returning `None` (→ 422) only if no usable title is found.
- **Bug caught and fixed during testing**: the `/import` endpoint mixes an `UploadFile` with a plain `text` parameter, which requires `text: str | None = Form(None)` — without explicit `Form()`, FastAPI silently treats `text` as a query parameter and never reads it from multipart form data. Caught by an actual failing curl test (400 "provide text or file" despite sending text), not by inspection.

Frontend (`frontend/src/pages/RecipesPage.jsx`, `RecipeDetailPage.jsx`, `components/RecipeForm.jsx`): list with search/staple filters, a shared add/edit form (dynamic ingredient and instruction rows, comma-separated tags, optional per-serving nutrition), an import panel (paste text or upload photo/PDF, review the parsed preview before saving), and a detail page with a live servings input that re-fetches scaled quantities, a rating dropdown, and a staple toggle. Routing extended with `/recipes` and `/recipes/:id`.

**Verification:** unit tests for `parse_recipe_response` (strict JSON, markdown-wrapped, garbage, missing-title) and `scale_ingredients` (up-scale, down-scale, no-op, `None` quantity untouched) run directly; full CRUD, rating validation, staple/tag filters, and tag deduplication exercised via curl against a live backend; the `Form()` bug found and fixed via a live request, then re-verified; frontend build succeeds; ran the real Vite dev server against the real backend and created/scaled a recipe through the actual proxy path.

## Phase 4.1 notes: recipe chat, citations, URL import, tips

Added after Phase 4 shipped, in response to a specific follow-up request: an ephemeral chat for substitution questions while cooking, source citation capture for copyright respect, URL import, and a way to retain genuinely useful asides (substitutions/variations) from imported sources while discarding ads/stories/boilerplate.

**Schema** (migration `957de2f3c51d`): `Recipe` gained `source_url` (String 1000, nullable), `source_name` (String 200, nullable), `source_author` (String 200, nullable), and `tips` (JSON list, default empty). `RecipeBase`/`RecipeUpdate` schemas extended to match; `RecipeIngredientRead.id` changed to `int | None` since scaled (non-persisted) ingredient views don't have a real row id.

**URL import**: `recipe_service.extract_url_content(url)` splits into `fetch_html()` (network call via `trafilatura.fetch_url`) and `extract_content_from_html()` (pure function over HTML using `trafilatura.bare_extraction`, `favor_recall=True`) so the extraction logic is unit-testable without a live network call. `trafilatura` does the mechanical boilerplate removal (ads/nav) and captures byline/sitename metadata in the same pass — that metadata becomes the citation (`source_url`/`source_name`/`source_author`) rather than being discarded. `POST /api/recipes/import` now accepts `text`, `file`, or `url` (exactly one; `url` is checked first) as multipart form fields.

**Copyright-respecting parsing**: `RECIPE_IMPORT_PROMPT` explicitly instructs the model to extract only factual/functional recipe data (what to buy, what to do, timing) and NOT reproduce narrative prose, personal stories, ads, or other copyrightable writing — summarizing functionally instead of quoting at length. A new `tips` array in the prompt output captures genuinely useful asides (substitutions, optional variations, storage/make-ahead notes, equipment alternatives) as short paraphrases, omitted entirely if the source has nothing like that. Verified with a synthetic HTML fixture containing a fake ad, nav, personal story, and an embedded substitution tip: ad/nav were stripped by trafilatura, the substitution text survived into extracted text, and title/author/sitename metadata were captured correctly.

**Bug fixed**: `parse_recipe_response` previously hardcoded `"source": "import_text"` in its return dict regardless of the actual import method, mislabeling image/PDF/URL imports. Fixed by removing that key from the service function entirely; the router now sets `parsed["source"]` based on which import branch actually ran (`import_text` / `import_file` / `import_image` / `import_url`).

**Ephemeral recipe-scoped chat** (`POST /api/recipes/{id}/chat`): for in-the-moment questions like "I'm out of buttermilk, what can I use?" while cooking. Deliberately NOT persisted to the `chat_messages` table — that's reserved for the Phase 7 persistent/background chat system, a separate concern. The client (`RecipeChat.jsx`) holds conversation history only in React state and resends it each turn to this stateless endpoint. `recipe_service.build_recipe_chat_context()` builds a context block (title, ingredients at the currently-viewed serving size, numbered instructions, known tips) that's appended to the active `main_chef` system prompt, so suggestions are grounded in the actual recipe rather than generic. Widget is collapsed by default on the recipe detail page, Enter-to-send (Shift+Enter for newline).

**Frontend**: `RecipeForm.jsx` gained editable tips list and source fields (auto-filled on import, editable manually); `RecipesPage.jsx` gained a URL input alongside the existing text/photo/PDF import controls, with a hint explaining the ad/story filtering behavior; `RecipeDetailPage.jsx` displays tips and a source citation (linked if a URL is present) below the nutrition block, and mounts `RecipeChat` at the bottom of the page; `theme.css` gained matching styles for the chat widget and citation text.

**Verification**: backend — unit tests for `extract_content_from_html` against the synthetic HTML fixture described above, `build_recipe_chat_context` against a mocked recipe (asserts title/ingredient text present in the built context), and the full import/chat flow via curl against a live local uvicorn instance (mocked `ollama_client.chat`/`describe_image` since no live Ollama reaches the sandbox) covering url/text/file import dispatch and the new `source`/citation labeling; a nested-f-string Python 3.10 SyntaxError in `build_recipe_chat_context` was caught by `py_compile` before runtime testing and fixed by extracting a plain-concatenation helper (`_format_ingredient_line`). Frontend — `npm run build` succeeds cleanly; ran the real Vite dev server against a real backend (fresh SQLite DB, real Alembic migration + seed, mocked Ollama chat response) together and exercised `/health`, recipe create, servings-scaled GET (confirmed tips/citation fields and scaled quantities round-trip correctly), and `POST /{id}/chat` all through the actual browser-facing Vite proxy path — not just each side tested in isolation.

## Phase 5 notes: meal planning engine

Backend (`backend/app/routers/meal_plan.py`, `app/routers/kitchen.py`, `app/services/meal_plan_service.py`, `app/schemas/meal_plan.py`, `app/schemas/kitchen.py`):

**Schema** (migration `67a6d7e5b6e5`): `MealPlanEntry` gained `requested_tags` (JSON list -- the guiding tags a slot was planned against, e.g. `["portable", "non_refrigerated"]` for a picnic) and `is_indulgence` (marks the week's occasional treat meal, separate from `HouseholdPreferences.indulgence_frequency` which just sets the target cadence). `GroceryListItem` gained `category` (nullable, mirrors `InventoryItem` categories, for a future group-by-aisle view). `MealPlan`/`MealPlanEntry`/`GroceryListItem` tables themselves already existed from the Phase 1 schema but had never been wired to an API until now.

**Kitchen profiles** (`routers/kitchen.py`, new): simple CRUD the project brief calls for but no earlier phase built -- `GET/POST /api/kitchen-profiles`, `PATCH/DELETE /api/kitchen-profiles/{id}`. Only one profile is active at a time; setting one active clears the flag on the others (same singleton-ish pattern as `HouseholdPreferences`). This is what lets the meal planner adapt to a home kitchen vs. camping/RV/rental with limited equipment.

**Generation** (`POST /api/meal-plans/generate`): same preview-then-confirm pattern as recipe import -- returns a `MealPlanCreate`-shaped preview, nothing persisted until the user reviews/edits and `POST`s to `/api/meal-plans`. `meal_plan_service.gather_generation_context()` pulls together everything the model needs to ground a sensible plan: household size/dietary restrictions/goals/indulgence frequency, the active (or requested) kitchen profile's equipment list, `inventory_service.get_priority_suggestions()` (the same urgency-scoring function built in Phase 3, reused directly rather than reimplemented) for what to prioritize using up, and a compact recipe catalog (staples and highly-rated first) so the model can reuse an existing recipe instead of inventing one for every slot. The prompt instructs the model to strongly prefer an existing catalog `recipe_id` and only propose a `new_recipe` (same shape as recipe import, via the now-shared `recipe_service.coerce_recipe_fields()`) when nothing fits. Per-slot guidance (e.g. "Saturday lunch needs to be portable for a picnic") and free-text notes (e.g. "going camping this weekend") can steer generation.

**Defensive parsing carries a validation step recipe import didn't need**: `parse_meal_plan_response()` coerces fields the same defensive way as recipe import (strict JSON first, then extract the first `{...}` block), but a meal plan additionally references *existing* recipe ids the model was shown -- and models sometimes hallucinate one that wasn't in the catalog. `validate_entries_against_catalog()` nulls out any `recipe_id` not actually present in the catalog that was sent, rather than trusting it; the slot is then left recipe-less for the user to fix in the review step, same as any other AI-preview-then-confirm flow here. Verified with a live curl test using a mocked Ollama response that deliberately includes a fabricated id (999) alongside two valid slots (one reusing a catalog recipe, one proposing a `new_recipe`) -- confirmed the hallucinated id came back `null` while the other two passed through correctly.

**Grocery list derivation**: split the same way as recipe import's URL extraction -- a pure aggregation/subtraction pair (`aggregate_ingredients`, `subtract_inventory`) that's unit-testable without a DB, plus a thin `compute_grocery_list()` wrapper that loads a persisted plan's entries/inventory. Ingredients are scaled to each entry's servings (reusing `recipe_service.scale_ingredients`), summed across all non-skipped entries keyed by `(name, unit)`, then matched against current inventory the same case-insensitive exact-then-substring way as `inventory_service.deduct_by_name` and subtracted; only the remaining (still-needed) quantity is listed. An ingredient with no stated quantity (e.g. "salt to taste") is only listed if nothing matching exists in inventory at all, rather than always appearing. Recomputation happens automatically on plan creation, on any entry edit/skip (`_persist_grocery_list()` replaces `source="auto"` rows only, leaving manually-added items alone), and on-demand via `POST /{plan_id}/grocery-list/regenerate` (e.g. after a shopping trip). Verified: confirming a meal deducts its scaled ingredients from inventory (reusing `inventory_service.deduct_by_name` once per ingredient) and a subsequent grocery-list regeneration correctly reflects the now-lower on-hand quantity -- i.e. the grocery list always answers "what do I still need to buy for everything not yet skipped," not a frozen snapshot from generation time.

**New-recipe creation on confirm**: `recipe_service.create_recipe_from_parsed()` (new, shared) creates a `Recipe` + ingredients + tags from a `coerce_recipe_fields()`-shaped dict without duplicating `routers/recipes.py`'s creation wiring wholesale -- used when `POST /api/meal-plans` persists an entry whose slot had no catalog match, tagging the new recipe `source="ai_generated"`. Verified live: a generated plan's `new_recipe` entry actually appears in `GET /api/recipes` afterward with the correct source label and is immediately available for future weeks' catalogs.

**Confirm/skip**: `POST /{plan_id}/entries/{entry_id}/confirm` marks a meal as made and deducts its scaled ingredients from inventory (best-effort -- an ingredient with no inventory match is silently skipped rather than failing the whole confirmation); `/skip` marks it skipped with no deduction. Both are blocked once an entry is already confirmed (400), and skip is blocked on an already-confirmed entry, verified via live curl including the double-confirm 400 case. Deleting a plan cascades to its entries (existing `cascade="all, delete-orphan"` relationship from Phase 1), verified live.

Frontend (`frontend/src/pages/MealPlanPage.jsx`, `components/MealPlanEntryRow.jsx`, `components/GroceryListPanel.jsx`): a plan picker (dropdown over existing plans by week), a generate form (week start date, household size override, kitchen profile select, meal-type checkboxes, free-text notes, and a collapsible per-day tags/notes guidance section), an editable review step after generation (swap any slot between the AI's proposed new recipe and any catalog recipe, adjust servings/tags/indulgence flag, nothing saved until confirmed), a weekly grid for the persisted plan (recipe swap, servings, "we made this"/skip actions, linking through to the recipe detail page), and a self-contained grocery list panel (checkbox to mark purchased, manual add, delete, recompute-from-inventory). Nav/routing extended with `/meal-plan`. `theme.css` gained matching styles for the grid, preview rows, and grocery list, following the existing "single source of truth" CSS-variable convention.

**Verification**: unit tests for the pure functions (`parse_meal_plan_response` against strict/markdown-wrapped/garbage/invalid-day input, `validate_entries_against_catalog`, `aggregate_ingredients`, `subtract_inventory`, `build_generation_prompt`) run directly against the service module; a comprehensive live curl sequence against a fresh local backend (real Alembic migration + seed, mocked `ollama_client.chat` returning a canned plan with a catalog-reuse slot, a new-recipe slot, and a deliberately hallucinated recipe id) covering generation, plan creation (including new-recipe auto-creation), grocery-list aggregation/subtraction math (hand-verified the exact expected quantities at each step), confirm-triggered inventory deduction, skip, manual grocery items, regeneration, entry PATCH re-syncing the grocery list, 404s, and cascade delete; kitchen-profile CRUD and active-exclusivity verified live; frontend `npm run build` succeeds cleanly; a full live Vite-dev-server + real-backend pass exercised health, kitchen-profiles, recipe creation, plan generation, plan creation, grocery-list retrieval, and entry confirmation all through the actual browser-facing proxy path.

**Known simplification, documented for a future pass**: ingredient aggregation/subtraction matches by name (case-insensitive exact-then-substring, same approach used throughout this app since Phase 3) and does not attempt unit conversion -- "2 cup flour" and "1 lb flour" stay as separate grocery-list lines rather than being reconciled. A real unit-conversion layer is a reasonable future improvement once real-world usage surfaces how much it actually matters.

## Session log

- **2026-07-30**: Reviewed prior prototype bookmark file (now deleted, insights preserved here and in Claude's memory), confirmed architecture decisions (FastAPI + React + SQLite), created 11-phase task list, scaffolded Phase 0 (repo structure, docker-compose, backend/frontend skeletons). Created GitHub repo (public) via browser automation, configured local `origin` remote and `main` branch, author pushed successfully. Renamed repo to `ai-chef-and-nutritionist` at author's request. Noted author's Docker host is WSL2 Debian, project folder reachable there at `/mnt/c/Users/JBentley/Claude/Projects/chef`. Completed Phase 1: 15-table SQLAlchemy schema across 8 model files, Alembic migration, idempotent seed script, wired into `docker-entrypoint.sh` so the DB bootstraps automatically on container start. Verified end-to-end locally (fresh DB → migrate → seed → tables confirmed) outside Docker since no Docker daemon is available in Claude's sandbox. Author requested DB-backed settings/secrets (not `.env`) with encryption matching the Fiduciary project's `secrets_crypto.py` — connected the Fiduciary folder, read that module directly, ported it into `backend/app/services/secrets_crypto.py` + a new `settings_service.py`, rewrote `app/config.py`/`seed.py`/`.env.example`/`README.md` accordingly, and built `ollama_client.py`/`tavily_client.py` + a read-only `/api/system/*` router on top (Phase 2 start). Verified end-to-end: fresh DB, `TAVILY_API_KEY` seeded from env comes out encrypted in the raw SQLite row, decrypts correctly through the settings service, `secrets.key` has 0600 perms, and all `/api/system/*` endpoints return correct data through a live local uvicorn instance (Ollama itself unreachable from the sandbox, as expected — `ollama_reachable: false` is the correct result here, not a bug). Found and applied FUSE-mount-coherence guidance from the Fiduciary project's docs (see the operational note above). Completed Phase 3: inventory CRUD, urgency scoring, vision photo intake (preview-then-confirm), and an ingredient-deduction primitive for later phases, plus a matching Inventory page in the frontend with routing/nav added. Fixed a latent Phase 0 bug in the process — the frontend's health check was calling the wrong path — and hardened `api.js` for the fact that frontend/backend are separate origins in the production Docker build, not just in dev. Completed Phase 4: recipe CRUD, servings scaling, ratings/staple flag, and AI-assisted import from text/PDF/photo, plus a matching Recipes list/detail UI. Caught and fixed a real `Form()`-vs-query-param FastAPI bug in the import endpoint via a failing live test rather than by inspection. Added Phase 4.1 (see notes above): ephemeral recipe-scoped "Ask the Chef" chat for substitution questions while cooking (not persisted — separate from the Phase 7 persistent chat), source-citation capture on import (`source_url`/`source_name`/`source_author`), URL import via `trafilatura`, and a `tips` field retaining useful substitutions/variations while import parsing discards ads/stories/boilerplate. Fixed a latent Phase 4 bug where imported recipes were always labeled `source: import_text` regardless of actual import method. Verified backend via unit tests + live curl against a fresh local instance, and frontend via `npm run build` plus a full live Vite-dev-server + real-backend end-to-end pass through the actual proxy path (recipe create, servings-scaled GET, and the new chat endpoint all confirmed working). Completed Phase 5 (see notes above): AI-assisted weekly meal-plan generation grounded in inventory urgency/priority (reusing Phase 3's scoring), household preferences, and kitchen equipment; a new kitchen-profiles CRUD router that no earlier phase had built; preview-then-confirm plan creation with automatic new-recipe creation for slots the catalog can't fill; a grocery list derived from planned ingredients minus current inventory, kept in sync as entries are edited/confirmed/skipped; confirm/skip actions with inventory deduction on confirm; and a matching Meal Plan page (generate form with per-day guidance, editable review step, weekly grid, grocery list panel). Verified extensively: unit tests for every pure service function, a live curl sequence against a fresh local backend with mocked Ollama covering generation (including a deliberately hallucinated recipe id, correctly nulled out), plan creation, grocery-list math (hand-verified quantities), confirm/skip/regenerate/PATCH-resync, 404s, and cascade delete; frontend `npm run build` clean; full live Vite-dev-server + real-backend pass through the actual proxy path.
