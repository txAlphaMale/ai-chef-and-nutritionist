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
- [ ] **Phase 1 — Core data layer.** SQLAlchemy models: inventory items, recipes, meal plans, user/household preferences, body metrics + bloodwork trend entries, chat history, kitchen/equipment profiles. Alembic migrations. Seed defaults (household size = 2).
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

## Session log

- **2026-07-30**: Reviewed prior prototype bookmark file (now deleted, insights preserved here and in Claude's memory), confirmed architecture decisions (FastAPI + React + SQLite), created 11-phase task list, scaffolded Phase 0 (repo structure, docker-compose, backend/frontend skeletons). Created GitHub repo `txAlphaMale/chef` (public) via browser automation, configured local `origin` remote and `main` branch. Author still needs to run `git push -u origin main` locally to actually push the commit (Claude cannot authenticate git pushes from its sandbox).
