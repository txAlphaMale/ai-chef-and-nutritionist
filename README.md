# AI Chef & Nutritionist

A self-hosted, AI-driven meal planner, kitchen inventory tracker, and
recipe manager. Internally referred to as "Chef." It runs as a single
Docker container against your own [Ollama](https://ollama.com) instance,
and it keeps everything -- inventory, recipes, meal plans, health metrics,
chat history, settings, API keys -- in a SQLite database on a Docker
volume that survives rebuilds. Nothing leaves your network except calls to
services you explicitly configure.

It is built for a household that cares about what it eats: gluten-free and
allergen-aware by design, grounded in real nutrition data rather than
model guesses, and honest about the difference between the two.

---

## New user setup checklist

Chef starts and runs with nothing configured. Each step below unlocks more
of it, and you can stop at any point and come back later. The same
checklist lives in the app's built-in WIKI once you are running.

- [ ] **1. Install the prerequisites** -- Docker with Compose, and Ollama
      reachable from the container. See [Requirements](#requirements).
- [ ] **2. Pull the models** -- one chat model, one vision model, one
      embedding model. See [Choosing models](#choosing-models); this is the
      step most likely to need adjusting for your hardware.
- [ ] **3. `cp .env.example .env` and start it** -- see
      [Running it](#running-it). Open the app and confirm the **Connection
      status** strip at the top of Settings shows Ollama reachable.
- [ ] **4. Set your household** -- Health > Household: size, dietary
      restrictions, goals. This has more influence on output than anything
      else you can configure.
- [ ] **5. Turn on the knowledge files you want** -- Health > Knowledge
      files. A small public-domain nutrition reference set ships with the
      app, **inactive by default**, so nothing grounds your meal plans
      until you choose it.
- [ ] **6. Add a USDA FoodData Central key** (free, optional but strongly
      recommended) -- Settings > AI & Models. This is what moves recipe
      nutrition from *AI estimated* to *computed from real food records*.
      Without it, every nutrition number in the app is a model's guess.
- [ ] **7. Put food in the inventory** -- type it, scan a barcode,
      photograph a receipt, or import an order-history CSV. Meal planning
      is built around what you already have and what is closest to
      expiring, so an empty pantry means it is planning blind.
- [ ] **8. Set up HTTPS** if you want the camera or location features --
      Settings > Security. Browsers refuse the camera (barcode scanning)
      and geolocation (Dining Out) over plain HTTP on a LAN address.
- [ ] **9. Optional extras** -- a [Tavily](https://tavily.com) key for
      web-grounded lookups, Google or iCloud calendar sync, a password
      gate, and a mounted folder of existing recipes to bulk-import.

Everything above is configurable from the Settings GUI at any time. You
should never need to edit a file or rebuild to change a setting.

---

## What it does

**Kitchen inventory**

- Pantry / fridge / freezer / produce / spice tracking with quantity,
  package size, price, purchase date and expiry.
- Five ways in: manual entry, **barcode scanning** (camera, via Open Food
  Facts), **photo intake** (point a camera at the open fridge), **receipt
  and shopping-list import** (photo, PDF or pasted text), and
  **order-history import** (CSV/XLSX with per-retailer column profiles).
- USDA **FoodKeeper**-backed shelf-life suggestions when you add something
  without a date, urgency scoring that drives meal planning, and an
  expiring-items banner.
- **Recall awareness** -- inventory names and brands are checked against
  the USDA FSIS and openFDA recall feeds.

**Recipes**

- Import from a URL, pasted text, PDF, photo, HTML or JSON file.
  schema.org JSON-LD is parsed directly where a site publishes it, with
  two-pass AI extraction as the fallback.
- **Bulk import** from a browser bookmarks export or a mounted recipe
  folder -- resumable, batched, and safe to re-run.
- Multi-component recipes (crust and filling), servings scaling, an
  Imperial / Metric / **Weight** display toggle, ratings, staples,
  SmartTags derived from actual contents, and a print stylesheet.
- **Cook mode**: full-screen, one step at a time, screen kept awake, with
  step-linked timers that keep running as you move around the app.

**Meal planning**

- Weekly plans generated from your household size, restrictions, expiring
  inventory, kitchen equipment, saved recipes, body metrics and active
  knowledge files -- previewed and edited before anything is saved.
- Explicit **leftovers** modelling and a **prep-day / batch-cooking** mode.
- **Grocery lists** grouped by store aisle, minus what is already on hand
  and minus your pantry staples, kept in sync as meals are confirmed or
  skipped.
- **Cost per serving** and projected weekly spend from captured prices.
- **Dining out**: restaurants near you filtered against your restrictions,
  slottable into the plan so the grocery list does not over-buy.
- Calendar push-sync to **Google Calendar** or **iCloud**, plus `.ics`
  export.

**Health & nutrition**

- Household members with age, sex, height, weight and activity level,
  producing DRI-based daily nutrient targets.
- Weight, BMI, blood pressure and full lipid-panel tracking with trend
  charts. Import bloodwork from CSV, PDF, a **photo of a printed report**,
  or pasted text; import weight and steps from an Apple Health export.
- Daily and weekly nutrient roll-ups against those targets, and a
  **diet-quality estimate** modelled on HEI-2020.
- **Nutrition provenance** on every recipe: `computed` from real food
  records, `partial`, or `ai_estimated`. The app tells you which, always.
- Structured **allergen and restriction** checking -- deterministic code,
  not a model -- with a gluten **observance level** and cross-contact
  flagging for shared-equipment risks like non-certified oats.

**The AI layer**

- Persistent **chat** that stays alive as you navigate, grounded in your
  real inventory, current meal plan, retrieved recipes *with their
  ingredients*, and your active knowledge files. It **proposes** actions
  (deduct an item, confirm a meal, revise a recipe) as buttons you press;
  nothing is ever applied silently.
- **Knowledge files / RAG**: upload nutrition references and Chef
  retrieves the relevant passages per request.
- Every import and extraction **prompt is editable in the GUI**, so a
  model that produces unparseable output is a settings change, not a code
  change.
- All AI work runs through one visible, serial **background job queue** --
  progress survives navigation and page reloads.

**Platform**

- One container, one address, optional HTTPS with in-app certificate
  management (self-signed or CSR/import).
- Optional single-password gate. **No authentication by default** -- fine
  on a LAN, wrong anywhere else.
- Installable as a **PWA**, responsive from phone to desktop, with an
  accessibility pass done against WCAG 2.1 AA.
- One-click backup, schema.org JSON-LD recipe export that round-trips, and
  a searchable **in-app WIKI** that ships with the container -- no
  internet needed to read the docs.

---

## Requirements

- **Docker** with Compose.
- **[Ollama](https://ollama.com)** running and reachable from inside a
  container, with models pulled (below). It can be on the host, elsewhere
  on your LAN, or containerized alongside Chef with GPU passthrough.
- **Hardware**: this is the constraint that matters. Chef's default chat
  model is a 27B-class model, which wants roughly 20&nbsp;GB of VRAM.
  Reference deployment is 2&times;GTX&nbsp;1080&nbsp;Ti (22&nbsp;GB
  combined). With less, pick a smaller model -- see below.
- Optional keys: **[USDA FoodData Central](https://fdc.nal.usda.gov/)**
  (free; real nutrition data -- follow the API-key signup link on their
  site), **[Tavily](https://tavily.com)** (web-grounded lookups), and
  **openFDA** (higher recall-check rate limits). All are entered in the
  Settings GUI and none are required to start.

### Choosing models

Four roles, all set in Settings > AI & Models. Shipped defaults:

| Role | Default | Used for |
|---|---|---|
| Chat | `qwen3.6:27b` | Chat, meal planning, recipe generation, receipt import |
| Extraction | *(falls back to chat)* | Structured parsing |
| Vision | `qwen2.5vl:7b` | Food photos, receipts, printed lab reports |
| Embedding | `bge-m3` | Knowledge-file indexing |

Two settings matter more than they look:

- **`num_ctx` (default 8192)** -- recipe import hands the model a long
  document. Too small a context window truncates it *silently*; the
  symptom is a recipe that saves with half its ingredients missing, not an
  error.
- **`timeout` (default 600s)** -- a 27B model on consumer hardware is
  genuinely slow. This is why AI work runs as background jobs.

Smaller chat models are usable but degrade in a specific way worth
knowing: rather than answering badly, they tend to return an *empty*
response on complex prompts. If imports mysteriously produce nothing,
try a larger chat model before debugging anything else.

---

## Running it

```bash
git clone https://github.com/txAlphaMale/ai-chef-and-nutritionist.git
cd ai-chef-and-nutritionist
cp .env.example .env
docker compose up --build
```

Then open **http://localhost:5173** (health check:
`http://localhost:5173/health`) and work through the
[setup checklist](#new-user-setup-checklist).

`.env` is only for bootstrap plumbing -- ports, the database path, and
optional first-run convenience values so things are pre-filled instead of
typed in later. Everything else lives in the database and is edited from
the Settings GUI, so changing a setting never needs a rebuild.

`OLLAMA_BASE_URL` has to be reachable *from inside the container*. On
Docker Desktop, `http://host.docker.internal:11434` usually works. On
Linux, use your host's LAN IP rather than `localhost`.

> **Back up `./data`, not just the database.** Secrets are encrypted at
> rest with a key generated on first boot at `./data/secrets.key`. Copying
> `chef.db` alone leaves every encrypted setting permanently unrecoverable.

### Ports

If `5173` (HTTP) or `5174` (HTTPS) conflict with something, change
`APP_PORT` / `APP_HTTPS_PORT` in `.env` and run `docker compose up` again
-- no `--build` needed. The container reads them at startup rather than
baking them into the bundle.

### Optional: Ollama in Docker with GPU passthrough

```bash
docker compose -f docker-compose.yml -f docker-compose.ollama.yml up --build
```

See the comments at the top of `docker-compose.ollama.yml` for NVIDIA
Container Toolkit requirements and multi-GPU notes, including how to
dedicate a specific card when you have more than one.

### HTTPS

Plain HTTP is fine on a trusted LAN, but browsers refuse two features
without a secure context: the **barcode scanner** (camera) and **Dining
Out** (geolocation). Generate or import a certificate under **Settings >
Security > Certificate** and the app starts serving HTTPS on
`APP_HTTPS_PORT`, with `APP_PORT` redirecting to it so old bookmarks keep
working.

The in-app WIKI's "HTTPS / secure context" entry covers the part that is
easy to get wrong, including getting iOS to trust a self-signed
certificate.

---

## One container, one address

Chef runs as a single container serving both its API and its web UI. Any
device on your LAN reaches exactly one address and -- with HTTPS -- trusts
exactly one certificate.

It was not always this way. The UI used to run in a second container with
a reverse proxy in front of the API, which meant two origins and two
certificates per device and a lot of machinery that could fail
independently. Serving the built files from the API that already exists
does the same job with none of it. See `backend/app/static_files.py` for
the reasoning; the short version is that a single-household app whose two
halves always ship together does not benefit from being split across two
runtimes.

---

## Development without Docker

Run the two halves separately. Vite's dev server proxies `/api` and
`/health` to the backend, so the browser still sees one origin.

Backend (API only -- there is no build for it to serve in dev):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

Frontend (hot reload on http://localhost:5173; override the API port with
`DEV_API_PORT`):

```bash
cd frontend
npm install
npm run dev
```

### Before opening a PR

```bash
cd backend  && python -m pytest -q && python -m ruff check .
cd frontend && npm run lint && npm run build && npm run ssr-probe
```

CI runs all of these plus a full `docker build`.

`npm run ssr-probe` is not optional politeness. It renders every page and
every always-mounted component through `react-dom/server`, and it exists
because `eslint` and `vite build` both **pass** a component that throws on
its first render -- which is exactly how a blank Recipes page once shipped
and survived a commit. It is the only gate in this repo that executes a
component. See `frontend/scripts/ssr-probe.jsx`.

---

## Project structure & roadmap

`PROJECT-PLAN.md` holds the full history: architecture decisions, the
phase-by-phase roadmap, competitive research, every backlog item with its
rationale, and a detailed log of what was built, what broke, and why. It
is long, and deliberately so -- it is the reasoning record, not a summary.

## License

MIT -- see `LICENSE`.
