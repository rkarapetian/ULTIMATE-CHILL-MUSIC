#!/usr/bin/env python3
"""
Hed Kandi + Ministry of Sound full catalog scraper.

Pulls every compilation release from Discogs for the target labels, fetches
each tracklist, deduplicates tracks across the entire catalog, and writes a
single master CSV.

Designed to be resumable: every label page and every release tracklist is
cached to disk. Re-running the script picks up exactly where it left off.
If your laptop sleeps, network drops, or you Ctrl+C, just re-run the same
command and it resumes from the last successful fetch.

Usage:
    python3 glasshouse_scrape.py

Output:
    ./glasshouse_cache/        per-release JSON cache (safe to delete)
    ./glasshouse_master.csv    final deduplicated tracklist

No API key required. Uses Discogs public API at 24 requests/minute.
Full run is roughly 45 to 70 minutes depending on network conditions.
"""

import ssl as _ssl_mod
import certifi as _certifi_mod
import urllib.request as _urlreq
_ctx = _ssl_mod.create_default_context(cafile=_certifi_mod.where())
_https_handler = _urlreq.HTTPSHandler(context=_ctx)
_opener = _urlreq.build_opener(_https_handler)
_urlreq.install_opener(_opener)

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

UA = "GlasshouseScraper/1.0 +personal-use"
CACHE_DIR = "glasshouse_cache"
OUTPUT_CSV = "glasshouse_master.csv"

# Labels to scrape. Discogs label IDs.
#   774   = Hed Kandi
#   1169  = Ministry Of Sound
#   66138 = Ministry Of Sound Recordings
# Add or remove IDs here to expand the scrape later (Defected = 9637, etc.)
LABELS = [
    (774, "Hed Kandi"),
    (1169, "Ministry Of Sound"),
    (66138, "Ministry Of Sound Recordings"),
]

# Discogs allows 25 unauthenticated requests/minute.
# 2.6 seconds between calls keeps us comfortably under the ceiling.
DELAY = 2.6

# Discogs paginates label releases. 100 per page is the max.
PER_PAGE = 100

# Only keep releases whose format/title suggests an album or compilation.
# Filters out singles, promos, remix EPs, etc.
COMP_FORMAT_HINTS = re.compile(r"comp|album", re.IGNORECASE)

os.makedirs(CACHE_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# HTTP helper with retry + rate-limit handling
# ---------------------------------------------------------------------------

def fetch_json(url, retries=5):
    """Fetch a URL and return parsed JSON. Handles 429s and transient errors."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            time.sleep(DELAY)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Rate limited. Honor the Retry-After header if present.
                wait = int(e.headers.get("Retry-After", "60"))
                print(f"  [429] rate limited, sleeping {wait}s", file=sys.stderr)
                time.sleep(wait + 1)
                continue
            if e.code in (500, 502, 503, 504):
                print(f"  [{e.code}] server error, retry {attempt + 1}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
                continue
            if e.code == 404:
                # Release was deleted from Discogs. Skip cleanly.
                return None
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"  [net] {type(e).__name__}, retry {attempt + 1}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    print(f"  FAILED after {retries} retries: {url}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Label release listing (paginated)
# ---------------------------------------------------------------------------

def list_label_releases(label_id, label_name):
    """Yield every release dict for a label, walking pagination."""
    page = 1
    while True:
        cache_path = os.path.join(CACHE_DIR, f"label_{label_id}_page_{page}.json")
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                data = json.load(f)
            print(f"[{label_name}] page {page} (cached)")
        else:
            url = (
                f"https://api.discogs.com/labels/{label_id}/releases"
                f"?per_page={PER_PAGE}&page={page}"
            )
            print(f"[{label_name}] page {page} (fetching)")
            data = fetch_json(url)
            if data is None:
                print(f"  could not fetch page {page}, stopping label", file=sys.stderr)
                return
            with open(cache_path, "w") as f:
                json.dump(data, f)

        for release in data.get("releases", []):
            yield release

        pagination = data.get("pagination", {})
        if page >= pagination.get("pages", 1):
            return
        page += 1


# ---------------------------------------------------------------------------
# Release detail fetch (with disk cache)
# ---------------------------------------------------------------------------

def fetch_release(release_id):
    """Fetch a single release with its tracklist. Cached to disk."""
    cache_path = os.path.join(CACHE_DIR, f"release_{release_id}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    url = f"https://api.discogs.com/releases/{release_id}"
    data = fetch_json(url)
    if data is None:
        return None
    with open(cache_path, "w") as f:
        json.dump(data, f)
    return data


# ---------------------------------------------------------------------------
# Compilation filter
# ---------------------------------------------------------------------------

def is_compilation(release_summary):
    """Filter releases to compilations/albums only."""
    fmt = release_summary.get("format", "")
    title = release_summary.get("title", "")
    if COMP_FORMAT_HINTS.search(fmt):
        return True
    # Hed Kandi/MoS comps follow predictable title patterns
    if re.search(r"(volume|vol\.?|the\s+mix|sessions|annual|disco heaven|"
                 r"the album|nu cool|beach house|back to|chillout|"
                 r"club classics|anthems)", title, re.IGNORECASE):
        return True
    return False


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Glasshouse scrape: Hed Kandi + Ministry of Sound")
    print("=" * 60)

    # Step 1: collect all release summaries from label pages
    all_releases = []
    seen_ids = set()
    for label_id, label_name in LABELS:
        for rel in list_label_releases(label_id, label_name):
            rid = rel.get("id")
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                rel["_source_label"] = label_name
                all_releases.append(rel)

    print(f"\nFound {len(all_releases)} unique releases across all labels.")

    # Step 2: filter to compilations
    comps = [r for r in all_releases if is_compilation(r)]
    print(f"Filtered to {len(comps)} compilation/album releases.\n")

    # Step 3: fetch tracklists
    track_rows = []
    seen_tracks = set()  # (artist_lower, title_lower) for dedup

    for i, rel in enumerate(comps, 1):
        rid = rel["id"]
        print(f"[{i}/{len(comps)}] release {rid}: {rel.get('title', '?')[:60]}")
        detail = fetch_release(rid)
        if detail is None:
            continue

        release_title = detail.get("title", "")
        release_year = detail.get("year", "")
        label_name = rel["_source_label"]

        for track in detail.get("tracklist", []):
            ttype = track.get("type_", "track")
            if ttype != "track":
                continue
            tname = (track.get("title") or "").strip()
            artists = track.get("artists") or detail.get("artists") or []
            artist_name = ", ".join(
                (a.get("name") or "").strip() for a in artists if a.get("name")
            ).strip()
            if not tname or not artist_name:
                continue

            key = (artist_name.lower(), tname.lower())
            if key in seen_tracks:
                continue
            seen_tracks.add(key)

            track_rows.append({
                "artist": artist_name,
                "track": tname,
                "release_title": release_title,
                "release_year": release_year,
                "label": label_name,
                "release_id": rid,
                "duration": track.get("duration", ""),
            })

    # Step 4: write CSV
    print(f"\nWriting {len(track_rows)} unique tracks to {OUTPUT_CSV}")
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "artist", "track", "release_title", "release_year",
            "label", "release_id", "duration",
        ])
        writer.writeheader()
        writer.writerows(track_rows)

    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run the same command to resume.", file=sys.stderr)
        sys.exit(130)
