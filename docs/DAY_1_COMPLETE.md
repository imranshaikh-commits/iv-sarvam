# Day 1 — Complete

**Date:** July 8, 2026
**Duration:** ~4 hours (planning + build + AWS + Docker)

---

## What's live right now

| System | Status | Where |
|---|---|---|
| GitHub repo | ✅ Live | [imranshaikh-commits/iv-sarvam](https://github.com/imranshaikh-commits/iv-sarvam) |
| Supabase DB | ✅ Ready | `imranshaikh-iv-sarvam` — 7 tables, RLS, pgvector, storage buckets |
| AWS EC2 server | ✅ Running | `13.206.20.25` — Ubuntu 24.04 ARM, Docker installed |
| Open WebUI ("Sarvam") | ✅ Reachable | http://13.206.20.25:8080 |

**Admin account:** Imran's IV email + password (stored in password manager)

---

## Docker container running

```
Container: sarvam-webui
Image: ghcr.io/open-webui/open-webui:main
Restart: unless-stopped (survives server reboots)
Data volume: sarvam-webui-data (chat history persists)
Port: 8080 → public
```

**Useful commands** (on the server via SSH):

```bash
# Check status
docker ps

# View logs
docker logs -f sarvam-webui

# Restart the container
docker restart sarvam-webui

# Stop it (won't delete data)
docker stop sarvam-webui

# Start again
docker start sarvam-webui
```

---

## Tomorrow's plan — Day 2

Goal: plug the brain into the UI. By end of Day 2, Sarvam should respond to chat messages with real content from your 10 sample proposals.

### Session tasks (~90 min)

1. **Sign up for OpenRouter** (~10 min)
   - Go to openrouter.ai → sign up → add $5 credit → generate API key
   - Set spending limit to $10/month cap
2. **Sign up for OpenAI Platform** (~10 min)
   - Go to platform.openai.com → sign up → add $5 credit → generate API key
   - Set hard limit to $10/month
3. **Ingest 10 sample proposals into Supabase** (~30 min, mostly automated)
   - Upload proposals to a folder on the server
   - Run `scripts/ingest_proposals.py`
   - Each proposal becomes ~78 searchable chunks with embeddings
4. **Deploy Hermes agent container** (~20 min)
   - Docker compose bringing up Hermes + Redis
   - Load Sarvam persona spec
   - Connect to Supabase + OpenRouter + OpenAI
5. **Connect Open WebUI to Hermes** (~10 min)
   - Point Open WebUI at Hermes's API endpoint
   - Test with a chat message
6. **First real test** (~10 min)
   - Ask Sarvam: "What proposals have we done with ForgeRock?"
   - Should return relevant sections from the 10 samples with citations

---

## Things Imran needs to do outside sessions

1. **Get more proposals from teammate** — targeting ~50-100 for real coverage
2. **Delete unused IAM access key** `AKIAWIRYYQK6HHTMC5W7` when convenient
3. **(Optional)** Move `sarvam-server-key.pem` from `~/Downloads/` to `~/aws-keys/` for safekeeping
4. **(Optional)** Add subdomain `sarvam.inspiritvision.com` → `13.206.20.25` at DNS provider

---

## Cost so far

**Actual spend today:** $0
- AWS: free tier
- Supabase: free tier
- GitHub: free tier
- All API services: not signed up yet

**Projected Day 2 spend:** ~$10 total (one-time credits, not monthly)

---

## Key learnings from Day 1

- User needs slower step-by-step for AWS Console work — full command blocks worked when broken down clearly
- SSH from Mac to EC2 worked first try after `chmod 400` on the .pem
- Docker install ran cleanly on ARM Ubuntu 24.04
- Path B (secure credential form) doesn't work for AWS SigV4 — proxy limitation, not a Perplexity bug — **for future sessions, guided AWS Console clicks or CloudShell are the safe patterns**
- Open WebUI branding via `WEBUI_NAME` env var worked without customization

---

## Wake-up checklist for Day 2

Before we start Day 2, verify:

```bash
# SSH in
ssh -i ~/Downloads/sarvam-server-key.pem ubuntu@13.206.20.25

# Confirm container still running
docker ps

# Confirm webui reachable
curl http://localhost:8080/
```

If all three show green, we're ready.
