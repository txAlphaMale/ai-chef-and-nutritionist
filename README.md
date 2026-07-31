# AI Chef & Nutritionist

A locally hosted, AI-driven meal planning, food inventory, and recipe
management app. Internally referred to as "Chef." Runs via Docker Compose, uses a local LLM through
[Ollama](https://ollama.com) for meal planning/chat, and
[Tavily](https://tavily.com) for web-grounded recipe/nutrition lookups.
All data (inventory, recipes, meal plans, chat history) is stored locally
in SQLite on a Docker volume, so it survives container rebuilds.

## Status

Early scaffolding. See `PROJECT-PLAN.md` for the full roadmap, current
phase, and architecture notes.

## Requirements

- Docker + Docker Compose
- [Ollama](https://ollama.com) running and reachable (host install or
  elsewhere on your network) with a chat-capable model (e.g.
  `qwen2.5:14b`) and a vision-capable model (e.g. `llava:13b`) pulled
- A [Tavily](https://tavily.com) API key (free tier available)

## Setup

Settings like the Ollama URL/models, Tavily key, and household preferences
live in the database and are meant to be edited from the Settings UI
(Phase 8) after first boot -- `.env` is only for infra bootstrap plus
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
4. Frontend: http://localhost:5173  Backend health check: http://localhost:8095/health

Secrets (like the Tavily key) are encrypted at rest using a key file
generated on first boot at `./data/secrets.key`. Back this up along with
the rest of `./data` -- losing it makes every encrypted setting permanently
unrecoverable.

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
