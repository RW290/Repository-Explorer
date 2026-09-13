# Running on Replit

Use the **Start application** workflow. It starts:

- the FastAPI backend on port 8000
- the React/Vite frontend on port 5000

The Vite development server proxies `/api` and `/health` to the backend, so
frontend requests work through the Replit preview without hardcoded hostnames.

The fixture demo works without credentials.

To analyze a real GitHub repository, add **both** secrets in Replit Secrets:

- `HF_TOKEN` — for file summaries and PR rationale (free token at
  huggingface.co/settings/tokens), used to call an open-weight model through
  Hugging Face's hosted Inference Providers.
- `GITHUB_TOKEN` — a personal access token with read access to public
  repositories.

`GITHUB_TOKEN` is **required here**, not optional. The backend can fall back to
the `gh` command-line tool's login session instead of a token, but that only
exists on a machine where someone has run `gh auth login` — which is never the
case on Replit. Without the token, analysis fails at the point where it tries to
read pull requests.

## Publishing

Publishing is configured as a single Reserved VM service:

- Build: `cd frontend && npm ci && npm run build`
- Run: FastAPI serves the built frontend and API on port 5000

The local `Start application` workflow remains a two-process Vite plus
Uvicorn setup for fast development. The production server serves the compiled
SPA directly and returns the frontend for client-side routes.

Do not create a Python virtual environment; dependencies are installed directly
in the Replit environment.