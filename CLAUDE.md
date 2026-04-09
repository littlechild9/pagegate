# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HTML Hub is a self-hosted Python server for publishing and sharing AI-generated HTML pages. It supports public access, approval-based access (via DingTalk/WeChat OAuth login), and an admin dashboard. Written in Chinese for a Chinese-speaking user base.

## Running the Server

```bash
# Install dependencies (Python 3.9+, uses venv/)
pip install -r requirements.txt

# Start server (runs on http://0.0.0.0:8888)
python3 server.py
```

Configuration lives in `config.yaml` (copy from `config.example.yaml`). Key settings: `admin_token`, `server.session_secret`, `server.base_url`.

## Architecture

**Single-file backend**: `server.py` is the entire backend — a FastAPI app serving APIs, OAuth flows, page rendering, and static files. There is no database; all state is stored in JSON files under `data/`.

**Data flow**:
- `data/index.json` — page metadata index (slug, title, category, access mode)
- `data/visitors.json` — visitor records (identity, approved/pending pages)
- `pages/{slug}/index.html` — actual HTML page content
- `pages/index.html` — auto-generated public directory (regenerated on publish/update)

**Three access modes**: `public` (open), `approval` (requires OAuth login + admin approval), `private` (admin-only via token).

**Auth**: Visitor sessions use signed cookies (`itsdangerous.TimestampSigner`). OAuth callbacks for both DingTalk and WeChat share a common `_register_visitor_and_redirect()` flow.

**SSE event system**: `EVENT_SUBSCRIBERS` (set of asyncio queues) + `EVENT_HISTORY` for broadcasting approval requests to connected watchers via `GET /api/events/stream`.

**Templates**: Jinja2 templates in `templates/` — `index.html` (public listing), `dashboard.html` (admin), `login.html` (OAuth prompt), `pending.html` (waiting for approval).

**OpenClaw integration** (`openclaw-skill/`): A companion skill for the OpenClaw chat platform that enables conversational approval. Includes:
- `scripts/htmlhub_client.py` — zero-dependency CLI client covering all management APIs
- `scripts/htmlhub_watch.py` — SSE watcher that connects to the event stream and forwards notifications

## Key Route Ordering

The catch-all `GET /{slug}` route is intentionally placed last in `server.py` to avoid shadowing `/dashboard`, `/auth/*`, and `/api/*` routes.

## API Auth Pattern

All admin APIs use `Depends(verify_admin)` which accepts either `Authorization: Bearer <token>` header or `?token=<token>` query parameter.
