# Repository Guidelines

## Project Structure & Module Organization
`server.py` is the entire FastAPI backend, so keep helpers, auth logic, and route groups organized in the existing section order. `templates/` contains Jinja2 views for the public index, login flow, pending page, and dashboard. Published content lives in `pages/<slug>/index.html`; `pages/index.html` is regenerated from metadata in `data/index.json`. Runtime state is stored in JSON files under `data/` such as `visitors.json` and `users.json` when registration is used. Utility scripts live in `scripts/`, and OpenClaw integration helpers live in `openclaw-skill/scripts/`.

## Build, Test, and Development Commands
Create a local environment with `python3 -m venv venv && source venv/bin/activate`, then install dependencies with `pip install -r requirements.txt`. Initialize config with `cp config.example.yaml config.yaml` and set `admin_token`, `server.session_secret`, and `server.base_url`. Run the app with `python3 server.py` for a direct FastAPI start, or `bash start.sh` to reuse the local venv and restart any old process on port `8888`. Use `sudo bash deploy.sh your-domain.com` only for server provisioning on Ubuntu or Debian hosts.

## Coding Style & Naming Conventions
Follow the existing Python style: 4-space indentation, `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for module constants, and short docstrings on non-obvious helpers. There is no configured formatter or linter in this snapshot, so match the current PEP 8 oriented style manually. Preserve UTF-8 handling and existing Chinese user-facing copy when editing templates or API messages.

## Testing Guidelines
There is no formal `pytest` suite yet. Before opening a PR, start the server locally and manually verify the affected flows through the API or dashboard, especially publish, update, delete, login, and approval paths. If you touch OpenClaw delivery, run `bash scripts/test_gateway_chat_send.sh` with the required `OPENCLAW_*` environment variables set. Add focused automated tests if you introduce reusable logic that can be isolated from HTTP handlers.

## Commit & Pull Request Guidelines
This workspace snapshot does not include `.git` history, so follow concise imperative commit messages such as `fix(auth): reject invalid session cookies` or `docs(readme): clarify config setup`. Keep commits scoped to one change. PRs should describe the user-visible impact, note config or migration implications, link related issues, and include screenshots for dashboard or template changes.

## Security & Configuration Tips
Do not commit real secrets from `config.yaml`, OAuth credentials, or generated `data/` contents. Treat `pages/` as user-supplied HTML: validate any new upload or rendering behavior carefully and avoid introducing server-side execution paths.
