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

1. Copy `.env.example` to `.env` and fill in your values:
   ```
   cp .env.example .env
   ```
2. Set `OLLAMA_BASE_URL` in `.env` to wherever Ollama is reachable from
   inside a container (on Linux, your host's LAN IP; on Docker Desktop,
   `http://host.docker.internal:11434` usually works out of the box).
3. Add your `TAVILY_API_KEY`.
4. Build and start:
   ```
   docker compose up --build
   ```
5. Frontend: http://localhost:5173  Backend health check: http://localhost:8095/health

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
