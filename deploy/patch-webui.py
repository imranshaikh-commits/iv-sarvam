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
   only emitted when an Enterprise license is present. We force the metadata
   block to always emit auth_logo_position='center' (and an empty
   login_footer) WITHOUT faking license_metadata itself — so no admin-panel
   side effects, no user-count query on the public /api/config endpoint.
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

# These two lines always live inside the metadata dict. Replace their values
# so the dict no longer dereferences a possibly-None license_metadata, and so
# the logo is forced centered with no login footer.
assert "'login_footer': license_metadata.get('login_footer', '')," in s, "login_footer line not found"
s = s.replace("'login_footer': license_metadata.get('login_footer', ''),",
              "'login_footer': '',")

assert "'auth_logo_position': license_metadata.get('auth_logo_position', '')," in s, "auth_logo_position line not found"
s = s.replace("'auth_logo_position': license_metadata.get('auth_logo_position', ''),",
              "'auth_logo_position': 'center',")

# Force the metadata block's conditional to always take the dict branch.
# Matches the guard in EITHER form:
#   inline  ->  {...} if license_metadata else {}
#   multi   ->  {...}\n    if license_metadata\n    else {}
# (the user_count ternary uses `else None`, so it is never matched.)
new, n = re.subn(r"if license_metadata\s+else \{\}", "if True else {}", s)
if n == 1:
    s = new
    method = "guard rewritten to 'if True else {}'"
else:
    # Fallback: image structure differs — make license_metadata truthy so the
    # (unmodified) guard takes the dict branch. Minor admin-panel cosmetic
    # side effect only in this fallback path.
    old1852 = "license_metadata = getattr(app.state, 'LICENSE_METADATA', None)"
    assert old1852 in s, "main.py: license_metadata assignment not found (image changed?)"
    s = s.replace(old1852,
                  old1852 + " or {'auth_logo_position': 'center', 'login_footer': ''}")
    method = f"fallback (license_metadata made truthy); guard regex matched {n}"

open(MAIN, "w").write(s)
print(f"Sarvam branding patch applied OK ({method})")
