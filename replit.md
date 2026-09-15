# Running on Replit

Live at [repository-explorer.replit.app](https://repository-explorer.replit.app).

Use the **Start application** workflow. It starts:

- the FastAPI backend on port 8000
- the React/Vite frontend on port 5000

The Vite development server proxies `/api` and `/health` to the backend, so
frontend requests work through the Replit preview without hardcoded hostnames.

The fixture demo works without credentials.

To analyze a real GitHub repository, add **both** secrets in Replit Secrets:

- `OLLAMA_API_KEY` — for file summaries and PR rationale (free key at
  ollama.com/settings/keys, no card required), used to call an open-weight
  model through Ollama Cloud.
- `GITHUB_TOKEN` — a personal access token with read access to public
  repositories.

`GITHUB_TOKEN` is **required here**, not optional. The backend can fall back to
the `gh` command-line tool's login session instead of a token, but that only
exists on a machine where someone has run `gh auth login` — which is never the
case on Replit. Without the token, analysis fails at the point where it tries to
read pull requests.

## Publishing

Publishing is configured as a single Autoscale service (scales to zero when
idle, so a sporadically-used deployment doesn't pay for 24/7 uptime the way a
Reserved VM would — set a spending limit in Replit's billing settings, since
it's opt-in and not on by default):

- Build: `cd frontend && npm ci && npm run build`
- Run: FastAPI serves the built frontend and API on port 5000

The local `Start application` workflow remains a two-process Vite plus
Uvicorn setup for fast development. The production server serves the compiled
SPA directly and returns the frontend for client-side routes.

When actually publishing, the deployment type (Autoscale vs. Reserved VM) is
also selected in Replit's Deploy panel UI — the `deploymentTarget` in
`.replit` alone doesn't switch an existing deployment's type.

Do not create a Python virtual environment; dependencies are installed directly
in the Replit environment.