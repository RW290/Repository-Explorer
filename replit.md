# Replit

Live at [repository-explorer.replit.app](https://repository-explorer.replit.app).

## Workspace

The **Start application** workflow runs `start.sh`:

- FastAPI backend on port 8000, with auto-reload
- Vite frontend on port 5000, proxying `/api` and `/health` to the backend

Dependencies are installed directly in the Replit environment; no virtual
environment is used.

## Secrets

| Secret | Purpose |
|---|---|
| `OLLAMA_API_KEY` | Model calls (Ollama Cloud) |
| `GITHUB_TOKEN` | GitHub access; required on Replit, since no `gh` login exists there |

The fixture demo runs without secrets.

## Publishing

Single Autoscale service:

- Build: `cd frontend && npm ci && npm run build`
- Run: FastAPI serves the built frontend and API on port 5000

The deployment type is selected in the Deploy panel. Pushing to GitHub does
not update the published site; pull in the workspace and republish.
