# Sarvam — Backlog / Deferred Items

Items intentionally deferred to a later phase. Not blockers.

- [ ] **Re-apply Sarvam + IV branding in Open WebUI** (deferred to final polish phase). During Sprint 3 the container was migrated from a hand-run `docker run` to Docker Compose management. User data (admin login, chat sessions) survived via the reused `open-webui` volume, but custom branding (app name "Sarvam", IV logo/colors) was reset to Open WebUI defaults. Re-configure via Admin Panel > Settings > Interface, or bake into compose env (WEBUI_NAME etc.) when we do the final UX pass. Logged 2026-07-10.
