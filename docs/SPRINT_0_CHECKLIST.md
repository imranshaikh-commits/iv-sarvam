# Sprint 0 — Foundation & Account Setup Checklist

**Owner:** Imran
**Timeline:** 3–5 days
**Blocker for:** Sprint 1 (Supabase project needed), Sprint 3 (AWS EC2 needed)

Tick items off as complete. Add credentials to shared password vault (never commit here).

## Accounts to create

- [ ] **AWS account** (aws.amazon.com/free)
  - Region: `ap-south-1` (Mumbai)
  - New accounts get $200 credit over 6 months (post-July-2025 terms)
  - Set up MFA immediately
  - Create IAM user `sarvam-admin` with programmatic access (never use root for daily work)
  - Add IAM credentials to password vault

- [ ] **Cloudflare account** (cloudflare.com)
  - Sign up with IV email
  - Add `inspiritvision.com` as a zone (if not already there)
  - Note the API token permissions needed for Sprint 8: `Workers Scripts:Edit`, `Pages:Edit`, `DNS:Edit`

- [ ] **Supabase account** (supabase.com)
  - Sign up via GitHub OAuth
  - Create organisation: "Inspirit Vision"
  - Create project: `sarvam-prod`, region **Mumbai (ap-south-1)** or **Singapore (ap-southeast-1)**
  - Save database password to vault
  - Note down: Project URL, `anon` key, `service_role` key, JWT secret

- [ ] **OpenRouter account** (openrouter.ai)
  - Fund with $20 starter credit
  - Generate API key labelled `sarvam-dev`
  - Save to vault

- [ ] **OpenAI account** (platform.openai.com) — only for embeddings
  - Add $10 credit
  - Generate API key labelled `sarvam-embeddings`
  - Save to vault
  - (Alternative: use Voyage AI, cheaper but requires separate account)

- [ ] **GitHub repo**
  - Create private repo `inspirit-vision/sarvam` (or personal namespace)
  - Add Ashish as collaborator
  - Push the starter scaffold I've provided

## DNS setup (for later — Sprint 7)

- [ ] Create DNS record: `sarvam.inspiritvision.com` (CNAME to Cloudflare Pages, will configure in Sprint 7)

## Verification

- [ ] All 5 API credentials in shared password vault
- [ ] AWS Budget alert set at $10/month with email notification
- [ ] Supabase project reachable via dashboard
- [ ] OpenRouter test call successful (`curl` to `/api/v1/chat/completions`)
- [ ] GitHub repo pushed with starter scaffold

## After completion

Message Imran/team channel: **"Sprint 0 done — ready for Sprint 1."**

Then:
1. Copy your 100+ proposal files into `data/raw/` locally (never commit).
2. Run `python scripts/ingest_proposals.py --input data/raw --output data/processed`.
3. Start filling in `data/tagging/tagging_template.csv` with metadata for each proposal.

Sprint 1 kickoff meeting: within 48h of Sprint 0 completion.
