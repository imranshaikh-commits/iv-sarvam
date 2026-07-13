#!/usr/bin/env python3
"""Sarvam branding patch — applied at image build time.

Open WebUI (v0.10.x) has no admin/DB setting for either of these, so we patch
the Python source in the base image directly:

1. env.py: strip the forced ' (Open WebUI)' suffix that Open WebUI appends to
   any custom WEBUI_NAME. With this gone, the WEBUI_NAME env var we set in
   docker-compose.yml ("Sarvam AI - Inspirit Vision Proposal Architect")
   shows cleanly on the sign-in page and browser tab.

2. main.py: the sign-in page only renders the logo centered above the title
   when config.metadata.auth_logo_position === 'center', and that field is
   only populated for Enterprise-licensed installs. We make the metadata
   block always emit auth_logo_position='center' (and an empty login_footer)
   WITHOUT faking license_metadata itself — so no admin-panel side effects,
   no user-count query on the public /api/config endpoint, no spurious
   enterprise UI.
"""
import re

ENV = "/app/backend/open_webui/env.py"
MAIN = "/app/backend/open_webui/main.py"

# --- 1. env.py: remove the forced suffix -----------------------------------
s = open(ENV).read()
suffix_block = "if WEBUI_NAME != 'Open WebUI':\n    WEBUI_NAME += ' (Open WebUI)'\n"
assert suffix_block in s, "env.py: WEBUI_NAME suffix block not found (image changed?)"
s = s.replace(suffix_block, "")
open(ENV, "w").write(s)

# --- 2. main.py: force centered auth logo ----------------------------------
s = open(MAIN).read()

# login_footer: was license_metadata.get(...) — but we drop the license guard
# below, so it must not dereference a possibly-None license_metadata.
assert "'login_footer': license_metadata.get('login_footer', '')," in s, "login_footer line not found"
s = s.replace("'login_footer': license_metadata.get('login_footer', ''),",
              "'login_footer': '',")

assert "'auth_logo_position': license_metadata.get('auth_logo_position', '')," in s, "auth_logo_position line not found"
s = s.replace("'auth_logo_position': license_metadata.get('auth_logo_position', ''),",
              "'auth_logo_position': 'center',")

# Remove the `if license_metadata else {}` guard wrapping the metadata block
# so the metadata dict is always emitted. Matches only the standalone two-line
# guard (the inline ternary `... if license_metadata else None` is untouched).
new, n = re.subn(r"\n[ \t]*if license_metadata\n[ \t]*else \{\}\n([ \t]*\)\),)", r"\n\1", s)
assert n == 1, f"main.py: expected 1 license guard to remove, found {n} (image changed?)"
s = new

open(MAIN, "w").write(s)
print("Sarvam branding patch applied OK")
