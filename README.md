# AI Chef & Nutritionist

A locally hosted, AI-driven meal planning, food inventory, and recipe
management app. Internally referred to as "Chef." Runs via Docker Compose,
uses a local LLM through [Ollama](https://ollama.com) for meal planning,
recipe parsing, vision-based inventory intake, and chat, and
[Tavily](https://tavily.com) for web-grounded recipe/nutrition lookups.
All data (inventory, recipes, meal plans, health metrics, chat history,
settings) is stored locally in SQLite on a Docker volume, so it survives
container rebuilds -- nothing leaves your network except calls you've
configured to Ollama/Tavily.

## Features

- **Inventory tracking** for pantry/fridge/freezer items with quantity,
  location, and expiration dates -- add manually or by uploading a photo
  for AI-assisted item detection.
- **Recipe management** with servings scaling, ratings, a "staple" flag
  for favorites, and AI-assisted import from pasted text, a photo, a PDF,
  or a URL (with source citation and ad/boilerplate filtering).
- **AI-generated weekly meal plans** sized to your household, aware of
  your available kitchen equipment (home, camping, RV, short-term
  rental), that favor ingredients close to expiration or sitting unused,
  and that respect per-meal guidance like "quick" or "portable."
- **Automatic grocery lists** derived from a meal plan minus what's
  already on hand, kept in sync as you confirm or skip meals.
- **Health & nutrition tracking**: body metrics (age/height/weight) feed
  BMI-aware plan steering; log weight/cholesterol/bloodwork over time and
  see trend charts; upload your own nutritionist guidance documents to
  ground the AI's suggestions.
- **Persistent chat** that stays running as you navigate the app, can
  answer cooking questions, and can propose (never silently apply)
  inventory or meal-plan changes from natural language.
- **Settings GUI** for the Ollama endpoint/models, Tavily key, system
  prompts, and household preferences -- no `.env` editing or rebuild
  required after first boot.

## Status

Phases 1-8 of the project roadmap are complete (data layer, AI
integration, inventory, recipes, meal planning, health tracking,
persistent chat, and the Settings GUI). See `PROJECT-PLAN.md` for the
full phase-by-phase roadmap, architecture notes, and what's left.

## Requirements

- Docker + Docker Compose
- [Ollama](https://ollama.com) running and reachable (host install or
  elsewhere on your network -- see below for an optional containerized,
  GPU-accelerated alternative) with a chat-capable model (e.g.
  `qwen3.5:9b`) and a vision-capable model (e.g. `qwen2.5vl:7b`) pulled
- A [Tavily](https://tavily.com) API key (free tier available)

## Setup

Settings like the Ollama URL/models, Tavily key, and household
preferences live in the database and are meant to be edited from the
Settings page after first boot -- `.env` is only for infra bootstrap plus
optional first-run convenience values.

1. Copy `.env.example` to `.env`:
   ```
   cp .env.example .env
   ```
2. (Optional) Fill in `OLLAMA_BASE_URL`/models and `TAVILY_API_KEY` in `.env`
   so they're pre-filled on first boot instead of entered later in Settings.
   `OLLAMA_BASE_URL` needs to be reachable from inside a container (on
   Linux, your host's LAN IP; on Docker Desktop,
   `http://host.docker.internal:11434` usually works out of the box).
3. Build and start:
   ```
   docker compose up --build
   ```
4. Frontend: http://localhost:5173 (this also proxies the backend health check -- http://localhost:5173/health)
5. Anything you skipped in step 2 (Ollama URL/models, Tavily key,
   household size, system prompts) can be set from the app's Settings
   page at any time -- changes take effect on the next request, no
   restart needed.

Secrets (like the Tavily key) are encrypted at rest using a key file
generated on first boot at `./data/secrets.key`. Back this up along with
the rest of `./data` -- losing it makes every encrypted setting permanently
unrecoverable.

### Optional: run Ollama in Docker with GPU passthrough

By default this stack assumes Ollama runs outside it (host-installed or
on your network). If you'd rather containerize Ollama too -- e.g. to
dedicate a GPU to it -- an opt-in override is provided:

```
docker compose -f docker-compose.yml -f docker-compose.ollama.yml up --build
```

See the comments at the top of `docker-compose.ollama.yml` for NVIDIA
Container Toolkit requirements and multi-GPU notes (including how to
dedicate a specific card if you have more than one).

### The frontend is the only thing a browser ever talks to

The frontend and backend run as separate containers, but a browser never
calls the backend directly: `frontend/server.js` reverse-proxies `/api/*`
and `/health` to the backend over Docker's own internal network. This
means any device on your LAN -- not just the machine running Docker --
only ever needs to reach and (if you set up HTTPS) trust ONE address,
the frontend's. (This used to not be the case, and caused exactly the
symptoms you'd expect on a second device: a "backend unreachable" status
and browser features like the camera silently failing depending on
which of the two origins a given device had actually trusted -- fixed
2026-08-03.)

The backend's own ports (`8095`/`8446` by default) are still published
by `docker-compose.yml` for optional direct API access or scripting, but
nothing in normal app use requires them to be reachable from outside the
backend's own container -- feel free to remove those port mappings
entirely if you don't need direct access.

If any default port (`5173`/`8095`, or their HTTPS counterparts)
conflicts with something already running on your machine, change
`BACKEND_PORT`/`FRONTEND_PORT` (or the `_HTTPS_PORT` variants) in `.env`
and run `docker compose up` again -- no `--build` needed. The frontend
container reads the current values at startup (see
`frontend/docker-entrypoint.sh`) rather than having them baked into the
built bundle, so this just works on a restart.

## Development without Docker

Backend:
```
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8095
```

Frontend:
```
cd frontend
npm install
npm run dev
```

## Project structure & roadmap

See `PROJECT-PLAN.md`.

## License

MIT -- see `LICENSE`.
