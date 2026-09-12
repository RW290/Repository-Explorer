# Running on Replit

Use the **Start application** workflow. It starts:

- the FastAPI backend on port 8000
- the React/Vite frontend on port 5000

The Vite development server proxies `/api` and `/health` to the backend, so
frontend requests work through the Replit preview without hardcoded hostnames.

The fixture demo works without credentials. To analyze a new GitHub repository,
add `GEMINI_API_KEY` in Replit Secrets. `GITHUB_TOKEN` is optional for public
repositories when the local GitHub CLI is authenticated, but should be supplied
as a secret for reliable hosted use.

Do not create a Python virtual environment; dependencies are installed directly
in the Replit environment.