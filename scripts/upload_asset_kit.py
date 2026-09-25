#!/usr/bin/env python3
"""Upload the image kit (asset_selection.KIT) to the asset bucket under kit/.

The kit is IV's fixed image set: house slides that go in every proposal and
each vendor's analyst views, product slides and reference architectures. The
manifest lives in backend/brain/asset_selection.py; this only puts the files
where the brain downloads them from. Re-running overwrites (upsert).

Run inside the brain container, which already has SUPABASE_URL/SUPABASE_KEY:
    docker cp ~/asset_kit sarvam-brain:/tmp/asset_kit
    docker exec -i sarvam-brain python3 - /tmp/asset_kit < scripts/upload_asset_kit.py
"""
import os
import sys

import requests

url, key = os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_KEY"]
bucket = os.environ.get("SHILPI_ASSET_BUCKET", "visual-assets")
folder = sys.argv[1] if len(sys.argv) > 1 else "asset_kit"

names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".png"))
failed = 0
for name in names:
    with open(os.path.join(folder, name), "rb") as fh:
        resp = requests.post(
            f"{url}/storage/v1/object/{bucket}/kit/{name}",
            headers={"apikey": key, "Authorization": f"Bearer {key}",
                     "Content-Type": "image/png", "x-upsert": "true"},
            data=fh.read(), timeout=120)
    ok = resp.status_code in (200, 201)
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} kit/{name}" + ("" if ok else f"  HTTP {resp.status_code} {resp.text[:120]}"))
print(f"{len(names) - failed}/{len(names)} uploaded to {bucket}/kit/")
sys.exit(1 if failed else 0)
