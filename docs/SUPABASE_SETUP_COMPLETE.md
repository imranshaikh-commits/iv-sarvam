# Supabase Setup — DONE

**Status:** ✅ Complete. Database is live and secured.
**Date:** July 8, 2026

---

## Your Project

| Field | Value |
|---|---|
| Project name | `imranshaikh-iv-sarvam` |
| Project ID | `jthrjmiulefmyrqtwsnz` |
| Region | `ap-northeast-1` (Tokyo) |
| API URL | `https://jthrjmiulefmyrqtwsnz.supabase.co` |
| Postgres | 17.6.1 |
| pgvector | 0.8.2 ✅ installed |
| Status | ACTIVE_HEALTHY |

---

## What was applied

### Migration 1 — Schema (`sarvam_001_schema`)
Created 7 tables (all with Row Level Security enabled):

| Table | Purpose |
|---|---|
| `organizations` | Multi-tenant boundary (Inspirit Vision org seeded ✅) |
| `org_members` | Who belongs to which org |
| `profiles` | User profile extension of `auth.users` |
| `proposals` | Metadata for each source proposal document |
| `proposal_chunks` | Text chunks with `VECTOR(1536)` embeddings + HNSW index |
| `generated_proposals` | Sarvam's output drafts |
| `architecture_diagrams` | HITL-approved architecture diagrams |

Extensions installed: `vector`, `pgcrypto`
Seeded: `Inspirit Vision` organization row

### Migration 2 — Retrieval function (`sarvam_002_retrieval_function`)
`match_proposal_chunks(...)` — the pgvector similarity search function Hermes will call at runtime. Supports filters by org, section type, vendor, proposal type, and outcome.

### Migration 3 — RLS policies (`sarvam_003_rls_policies`)
Every table locked down. Users can only see rows belonging to orgs they are members of. `is_org_member(uuid)` helper function created.

### Migration 4 — Security hardening (`sarvam_004_harden_functions`)
- Locked `search_path` on both SECURITY DEFINER functions
- Revoked anon EXECUTE on internal helpers
- Cleared 6 of 9 security-advisor warnings

---

## Credentials — where they live

**Do NOT paste these into chat or commit them.** Get them from:

1. **Supabase Dashboard → Project → Settings → API**
   - Project URL: `https://jthrjmiulefmyrqtwsnz.supabase.co`
   - Publishable key (for frontend): `sb_publishable_kKGhPJgQfEGmN5Fns_YaYA_BUh7Rve2` — safe to embed in browser code
   - Anon (legacy JWT): kept for backward compatibility, avoid using
   - **Service role key**: fetch from dashboard when needed — NEVER commit

2. **Copy them into `backend/hermes/.env`** (never `.env.example`) — see next section.

---

## Next: create `backend/hermes/.env`

On your machine (never commit this file — `.gitignore` already blocks it):

```env
# Supabase
SUPABASE_URL=https://jthrjmiulefmyrqtwsnz.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_kKGhPJgQfEGmN5Fns_YaYA_BUh7Rve2
SUPABASE_SERVICE_ROLE_KEY=<paste from dashboard>
SUPABASE_ORG_ID=<will be filled after we create your auth user>

# OpenRouter (create later — Sprint 2)
OPENROUTER_API_KEY=

# Embeddings (later — Sprint 1)
OPENAI_API_KEY=
```

---

## Manual step remaining in Supabase Dashboard

Storage buckets can't be created via SQL. In the Supabase dashboard:

**Storage → New bucket** — create these four, all **private**:

1. `source-proposals` — raw DOCX/PDFs
2. `proposal-images` — extracted images
3. `generated-drafts` — Sarvam's output
4. `diagram-renders` — approved architecture SVGs

For each bucket, add a policy:
- **Policy name:** `Org members can read their bucket files`
- **Allowed operations:** SELECT
- **Definition:**
```sql
bucket_id = '<bucket_name>'
AND (storage.foldername(name))[1] IN (
    SELECT org_id::text FROM org_members WHERE user_id = auth.uid()
)
```

I'll walk you through the buckets in the dashboard when you're ready. Ping me.

---

## Remaining security-advisor warnings — acknowledged

Three warnings are by design and safe:
- `handle_new_user` is a Supabase-provided function for auto-creating profiles — leave as-is
- `is_org_member` and `match_proposal_chunks` are SECURITY DEFINER because RLS itself enforces access control. This is the recommended Supabase pattern.

One low-priority warning:
- `vector` extension is in the `public` schema. Standard pattern; safe to keep. Moving it to a separate schema is a future optimization.
