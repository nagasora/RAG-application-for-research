# PaperPilot frontend (local use)

The frontend is intended to run on the same computer as the PaperPilot API.
For local development, Next.js proxies same-origin `/api` requests to
`http://localhost:8000`; it uses the local development identity
`paperpilot-local-user` unless you explicitly override it.

```powershell
cd frontend
corepack pnpm install --frozen-lockfile
corepack pnpm dev
```

Open <http://localhost:3000>.  Start the backend and its local PostgreSQL
database first; the root [README](../README.md) has the complete startup
sequence.  Data is stored by the backend, so restarting this Next.js process
does not remove papers, workspaces, or graph data.

## Optional configuration

You normally do not need a frontend environment file. To use a different
identity, copy `.env.local.example` to `.env.local` and change only the
values you need:

```text
NEXT_PUBLIC_AUTH_MODE=dev
NEXT_PUBLIC_DEV_USER=paperpilot-local-user
```

Set `NEXT_PUBLIC_API_URL=https://api.example.com` only when the frontend must
call an external API directly; doing so disables the local `/api` proxy.

OIDC remains available for a separately configured environment: set
`NEXT_PUBLIC_AUTH_MODE=oidc` and provide the three `NEXT_PUBLIC_AUTH0_*`
values shown in `.env.local.example`.  These values are public SPA settings;
do not put secrets in `.env.local` or commit it.
