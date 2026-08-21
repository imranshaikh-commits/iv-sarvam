#!/usr/bin/env python3
"""Build an offline contact sheet for approving the reusable asset library.

WHY A CONTACT SHEET
-------------------
343 assets (237 corporate, 106 product) need a human decision before any of
them can appear in a client document. Reviewing them one at a time in a
database UI is the kind of gate people click through without looking, which is
worse than no gate: it produces the paperwork of review without the review.

So: one self-contained HTML file, every image visible at once, everything
pre-selected, and the reviewer's job is to UNCHECK what should not be used.
That is the fast direction -- most of these are IV's own brand and methodology
material and are obviously fine; the handful that are not stand out visually.

`architecture` assets are not included at all. They depict a specific client's
estate and are excluded in code from ever being placed, so approving them would
be meaningless.

The HTML embeds thumbnails as base64 and needs no server, no signed URLs and no
network once written. Reviewing it changes nothing by itself; it produces a
list of ids, and a second command applies them.

USAGE
    python3 scripts/review_assets.py                  # writes asset_review.html
    open asset_review.html                            # review, then Copy
    python3 scripts/review_assets.py --approve ids.txt
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import os
import re
import sys

import requests

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
BUCKET = os.getenv("SHILPI_ASSET_BUCKET", "visual-assets")
# 260px thumbnails hid the problem they existed to catch. Run 8 placed a
# Microsoft Project Gantt chart showing another client's task names, durations
# and resource assignments into a proposal -- and it passed this review because
# at 260px the task names were illegible. A gate you cannot read through is not
# a gate.
THUMB_PX = 720


def sb_headers(extra: dict | None = None) -> dict:
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    if extra:
        h.update(extra)
    return h


def fetch_assets() -> list[dict]:
    """Placeable assets only, ordered so similar things sit together."""
    rows, offset = [], 0
    while True:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/visual_assets",
            headers=sb_headers({"Range": f"{offset}-{offset + 499}"}),
            params={"select": "id,storage_path,asset_kind,vision_description,"
                              "width,height,size_bytes,approved",
                    "asset_kind": "in.(corporate,product)",
                    "order": "asset_kind,size_bytes.desc"},
            timeout=60)
        resp.raise_for_status()
        batch = resp.json() or []
        rows.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    return rows


def download(storage_path: str) -> bytes | None:
    resp = requests.get(
        f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{storage_path}",
        headers=sb_headers(), timeout=60)
    return resp.content if resp.status_code == 200 else None


def thumbnail(blob: bytes) -> str | None:
    """Small base64 PNG. Keeps the sheet openable offline and under ~15 MB."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(blob))
        im.thumbnail((THUMB_PX, THUMB_PX))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=72)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def clean_desc(text: str | None) -> str:
    d = re.sub(r"^\[[^\]]*\]\s*", "", (text or "").replace("\n", " ")).strip()
    return d[:190] or "(no description)"


def build_html(cards: list[dict], out_path: str) -> None:
    body = []
    for c in cards:
        # Reflect the CURRENT approval state, do not blanket pre-check.
        # 131 assets were un-approved after run 8 leaked another client's
        # Microsoft Project plan into a proposal. A sheet that pre-checks
        # everything would silently restore them on the next export -- the
        # review would undo its own previous outcome.
        checked = "checked" if c["approved"] else ""
        was = "yes" if c["approved"] else "no"
        body.append(f'''<label class="card" data-kind="{c['kind']}" data-was="{was}">
  <input type="checkbox" {checked} value="{c['id']}">
  <img src="data:image/jpeg;base64,{c['thumb']}" loading="lazy"
       onclick="event.preventDefault();zoom(this.src)">
  <div class="meta"><span class="kind {c['kind']}">{c['kind']}</span>
  {c['w']}&times;{c['h']} &middot; {c['kb']} kB</div>
  <div class="desc">{html.escape(c['desc'])}</div>
</label>''')

    page = f"""<!doctype html><meta charset="utf-8">
<title>Shilpi — approve reusable assets</title>
<style>
 body{{font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      margin:0;padding:0 24px 80px;color:#1f0a4a;background:#fafafa}}
 header{{position:sticky;top:0;background:#fff;border-bottom:2px solid #231154;
        padding:16px 0;margin-bottom:18px;z-index:9}}
 h1{{margin:0 0 6px;font-size:20px}}
 .hint{{color:#555;max-width:70ch}}
 .bar{{margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
 button{{font:inherit;padding:8px 14px;border:1px solid #231154;background:#231154;
        color:#fff;border-radius:4px;cursor:pointer}}
 button.ghost{{background:#fff;color:#231154}}
 #count{{font-weight:600}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:16px}}
 .card{{background:#fff;border:2px solid #e3e3e3;border-radius:6px;padding:10px;
       cursor:pointer;display:block}}
 .card:has(input:checked){{border-color:#E85A24;background:#fffdfa}}
 .card img{{width:100%;height:340px;object-fit:contain;background:#f4f4f4;border-radius:3px;
            cursor:zoom-in}}
 .card img:hover{{transform:scale(1.02)}}
 dialog{{max-width:96vw;max-height:96vh;padding:0;border:none;border-radius:6px}}
 dialog img{{max-width:96vw;max-height:90vh;display:block}}
 dialog .close{{position:absolute;top:8px;right:12px}}
 .meta{{margin-top:8px;font-size:12px;color:#666}}
 .kind{{display:inline-block;padding:1px 7px;border-radius:10px;color:#fff;font-size:11px}}
 .kind.corporate{{background:#231154}} .kind.product{{background:#E85A24}}
 .desc{{margin-top:6px;font-size:12px;color:#333}}
 input[type=checkbox]{{float:right;width:18px;height:18px;accent-color:#E85A24}}
 textarea{{width:100%;height:120px;margin-top:10px;font-family:ui-monospace,monospace;
          font-size:12px}}
</style>
<header>
  <h1>Approve reusable assets for Shilpi proposals</h1>
  <div class="hint"><strong>Click any image to view it full size.</strong>
  Everything is pre-selected — uncheck anything that should never appear in a
  client proposal. Look especially for <strong>another client's name, project
  plan, task list, dates or staff names</strong>: a Gantt chart or schedule is a
  client's project plan even when it looks generic, and one slipped through a
  previous review because the thumbnails were too small to read. Architecture
  diagrams are excluded from this list entirely; they depict a single client's
  estate and can never be reused.</div>
  <div class="bar">
    <span id="count"></span>
    <button class="ghost" onclick="setAll(true)">Select all</button>
    <button class="ghost" onclick="setAll(false)">Deselect all</button>
    <button class="ghost" onclick="filter('all')">All</button>
    <button class="ghost" onclick="filter('corporate')">Corporate only</button>
    <button class="ghost" onclick="filter('product')">Product only</button>
    <button class="ghost" onclick="filterRejected()">Previously rejected</button>
    <button onclick="downloadIds()">Download ids.txt</button>
    <button class="ghost" onclick="showIds()">Show ids (copy manually)</button>
  </div>
  <textarea id="out" placeholder="Approved ids appear here — save as ids.txt"></textarea>
</header>
<div class="grid">{''.join(body)}</div>
<dialog id="lb"><button class="close" onclick="lb.close()">Close</button><img id="lbimg"></dialog>
<script>
function zoom(src){{document.getElementById('lbimg').src=src;document.getElementById('lb').showModal();}}
const boxes=()=>[...document.querySelectorAll('input[type=checkbox]')];
function tally(){{
  const on=boxes().filter(b=>b.checked).length;
  const was=[...document.querySelectorAll('.card')].filter(c=>c.dataset.was==='yes').length;
  document.getElementById('count').textContent=
    on+' of '+boxes().length+' approved (was '+was+')';}}
function setAll(v){{boxes().forEach(b=>{{if(b.closest('.card').style.display!=='none')b.checked=v}});tally();}}
function filter(k){{document.querySelectorAll('.card').forEach(c=>{{
  c.style.display=(k==='all'||c.dataset.kind===k)?'block':'none';}});}}
// The 131 assets removed after run 8. Worth a second look: the rules that
// rejected them are keyword rules, and they will have caught things that are
// actually fine as well as the ones that were not.
function filterRejected(){{document.querySelectorAll('.card').forEach(c=>{{
  c.style.display=(c.dataset.was==='no')?'block':'none';}});}}
function approvedIds(){{
  return boxes().filter(b=>b.checked).map(b=>b.value).join('\\n');
}}
// A real file download. document.execCommand('copy') is deprecated and fails
// silently on file:// pages -- it did, and the shell picked up whatever was in
// the clipboard from before, which the approval step then tried to use as a
// UUID. A Blob download cannot fail quietly.
function downloadIds(){{
  const ids=approvedIds();
  const n=ids.split('\\n').filter(Boolean).length;
  if(!n){{alert('Nothing selected.');return;}}
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([ids+'\\n'],{{type:'text/plain'}}));
  a.download='ids.txt'; a.click(); URL.revokeObjectURL(a.href);
  alert(n+' ids downloaded as ids.txt. Move it to ~/iv-sarvam/scripts/');
}}
function showIds(){{
  const t=document.getElementById('out'); t.value=approvedIds(); t.select();
}}
document.addEventListener('change',tally); tally();
</script>"""
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(page)


def apply_approvals(path: str) -> int:
    raw = [line.strip() for line in open(path) if line.strip()]
    # Validate before sending. A stray shell command in the clipboard reached
    # this function once and was posted to PostgREST as a UUID; the database
    # rejected it, but the script should have.
    uuid_re = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                         r"[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
    ids = [x for x in raw if uuid_re.match(x)]
    bad = [x for x in raw if not uuid_re.match(x)]
    if bad:
        print(f"{len(bad)} line(s) are not asset ids and were ignored:", file=sys.stderr)
        for b in bad[:3]:
            print(f"  {b[:70]}", file=sys.stderr)
    if not ids:
        print("\nNo valid asset ids found. The file should contain one UUID per "
              "line -- use the 'Download ids.txt' button in the review page.",
              file=sys.stderr)
        return 1
    print(f"approving {len(ids)}...", file=sys.stderr)
    for i in range(0, len(ids), 100):
        batch = ids[i:i + 100]
        quoted = ",".join(f'"{x}"' for x in batch)
        resp = requests.patch(
            f"{SUPABASE_URL}/rest/v1/visual_assets",
            headers=sb_headers({"Content-Type": "application/json",
                                "Prefer": "return=minimal"}),
            params={"id": f"in.({quoted})"},
            json={"approved": True, "approved_by": os.getenv("USER", "human"),
                  "approved_at": "now()"},
            timeout=60)
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        print(f"  {min(i + 100, len(ids))}/{len(ids)}", file=sys.stderr)
    # Anything NOT in the list stays approved=false, which is the safe default.
    print("done. Assets not in the list remain unapproved.", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="asset_review.html")
    ap.add_argument("--approve", metavar="IDS_FILE",
                    help="Apply approvals from a file of ids, one per line")
    args = ap.parse_args()

    if not (SUPABASE_URL and SUPABASE_KEY):
        print("Missing SUPABASE_URL / SUPABASE_KEY", file=sys.stderr)
        return 1
    if args.approve:
        return apply_approvals(args.approve)

    assets = fetch_assets()
    print(f"{len(assets)} placeable assets", file=sys.stderr)
    cards, missing = [], 0
    for i, a in enumerate(assets, 1):
        blob = download(a["storage_path"])
        thumb = thumbnail(blob) if blob else None
        if not thumb:
            missing += 1
            continue
        cards.append({"id": a["id"], "kind": a["asset_kind"], "thumb": thumb,
                      "approved": bool(a.get("approved")),
                      "w": a.get("width") or "?", "h": a.get("height") or "?",
                      "kb": round((a.get("size_bytes") or 0) / 1024),
                      "desc": clean_desc(a.get("vision_description"))})
        if i % 50 == 0:
            print(f"  {i}/{len(assets)}", file=sys.stderr)

    build_html(cards, args.out)
    size_mb = os.path.getsize(args.out) / 1_048_576
    print(f"\nwrote {args.out} ({len(cards)} images, {size_mb:.1f} MB)"
          f"{f', {missing} unreadable' if missing else ''}", file=sys.stderr)
    print("Open it, uncheck anything unsuitable, click Export, save as ids.txt,\n"
          "then: python3 scripts/review_assets.py --approve ids.txt", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
