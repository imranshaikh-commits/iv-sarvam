# Sprint: Open WebUI Logo Branding Fix

**Priority:** Critical (post-pilot, pre-deployment)
**Status:** Not started — root cause identified, fix approach validated pending implementation
**Owner:** Imran (lead) / agent
**Last updated:** 2026-07-17 (IST)

## Problem

Open WebUI (the public chat frontend on the Sarvam EC2 host, port 8080) does not
display the Inspirit Vision logo in-app. The browser **tab title** updates
correctly to "Sarvam AI - Inspirit Vision Proposal Architect" and the **tab
favicon** is the IV logo, but the **in-app logo** (sidebar logo when logged in,
and the centered sign-in page logo) does not render — Open WebUI's default
branding shows instead.

This has been attempted at least twice (Sprint 3 docker-run era, and again on
2026-07-17) without success.

## What is already working (verified live 2026-07-17)

- `deploy/Dockerfile.webui` bakes the IV logo into the image: `assets/iv-logo.png`
  -> `/app/backend/open_webui/static/favicon.png`, `logo.png`,
  `favicon-96x96.png`, `favicon-dark.png`, `favicon.ico`.
- `deploy/patch-webui.py` runs at image build time and (a) strips the forced
  " (Open WebUI)" suffix from `WEBUI_NAME`, and (b) forces
  `auth_logo_position='center'` so the sign-in page renders a centered logo.
- `/api/config` returns `name: "Sarvam AI - Inspirit Vision Proposal Architect"`
  and `auth_logo_position: "center"` — both patches applied.
- The server serves the IV logo at `/static/favicon.png` (21,666 bytes, 512x512
  PNG) and `/static/logo.png` (5,367 bytes, 500x500 PNG).
- The served HTML references `<link rel="icon" href="/static/favicon.png">`, so
  the browser **tab favicon** is the IV logo.
- Confirmed NOT a browser-cache issue (reproduced in a fresh incognito window).

## Root cause

OWUI's **in-app logo** (sidebar + sign-in page image) is driven by the OWUI
"favicon URL" config value, which is **not set** — `/api/config` returns no
standalone `logo` URL field. OWUI therefore falls back to its bundled default
logo. The Dockerfile replaces the static *files* but does not set the OWUI
*config value* that selects the logo, so the in-app logo stays default.

Secondary path: the compiled frontend at `/app/build/static/` ships its own
`favicon.png` / `logo.png` (Open WebUI defaults) that `Dockerfile.webui` does
**not** replace — an additional override path to close.

## Fix (validated approach)

1. Set `WEBUI_FAVICON_URL=/static/favicon.png` in the `open-webui`
   `environment:` block of `deploy/docker-compose.yml` (persistent across
   rebuilds). Recreate with `docker compose up -d open-webui` (no rebuild).
2. If the sidebar/sign-in logo still defaults, also copy the IV logos into the
   compiled frontend: extend `Dockerfile.webui` to `COPY assets/iv-logo*.png`
   into `/app/build/static/` in addition to `/app/backend/open_webui/static/`,
   then `docker compose up -d --build open-webui`.
3. Alternative / complementary: set the logo via the OWUI Admin Panel ->
   Settings -> Images (DB-backed, applies immediately, but not portable across
   image rebuilds — prefer the env/Dockerfile approach for persistence).

## Acceptance criteria

- [ ] IV logo renders in the sidebar (logged in).
- [ ] IV logo renders centered on the sign-in page (logged out).
- [ ] Browser tab favicon is the IV logo.
- [ ] Change survives `docker compose up -d --build open-webui` and
      `git pull` + redeploy (committed to the repo, not a live-only edit).
- [ ] No secrets, EC2 IPs, or Supabase refs in committed changes.

## When

After the Phase 6 pilot (post-pilot, pre-deployment hardening) — unless the
missing branding blocks pilot UX, in which case escalate before pilot.
