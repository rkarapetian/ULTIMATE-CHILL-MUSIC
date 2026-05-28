#!/usr/bin/env python3
"""
Hed Kandi + Ministry of Sound full catalog scraper.

Pulls every compilation release from Discogs for the target labels, fetches
each tracklist (including tracks nested inside continuous DJ mixes),
deduplicates tracks across the entire catalog, and writes a single master CSV.

Designed to be resumable: every label page and every release tracklist is
cached to disk, including "deleted/unavailable" tombstones so dead releases
are never re-fetched. Re-running the script picks up exactly where it left
off. If your laptop sleeps, the network drops, or you Ctrl+C, just re-run the
same command and it resumes from the last successful fetch.

Usage:
    python3 glasshouse_scrape.py

Optional:
    export DISCOGS_TOKEN=xxxx   # free token raises the limit 25 -> 60 req/min

Output:
    ./glasshouse_cache/        per-release JSON cache (safe to delete)
    ./glasshouse_master.csv    final deduplicated tracklist

No API key required (a free token just makes it faster).
Full run is roughly 45 to 70 minutes unauthenticated, ~20 with a token.
"""

import csv
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import certifi

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

UA = "GlasshouseScraper/1.1 +personal-use"
CACHE_DIR = "glasshouse_cache"
OUTPUT_CSV = "glasshouse_master.csv"

# Optional free Discogs personal-access token. Raises the rate limit from
# 25 req/min (unauthenticated) to 60 req/min. Read from the environment so
# the token never lives in source.
TOKEN = os.environ.get("DISCOGS_TOKEN", "").strip()

# Labels to scrape, by Discogs label ID.
#   774   = Hed Kandi
#   1169  = Ministry Of Sound
#   66138 = Ministry Of Sound Recordings
# Add or remove IDs here to expand the scrape later (Defected = 9637, etc.)
LABELS = [
    (774, "Hed Kandi"),
    (1169, "Ministry Of Sound"),
    (66138, "Ministry Of Sound Recordings"),
]

# Stay comfortably under the rate ceiling: ~23/min unauthenticated, ~55/min
# with a token. Discogs throttles hard, so we leave headroom.
DELAY = 1.1 if TOKEN else 2.6

# Discogs paginates label releases; 100 per page is the max.
PER_PAGE = 100

# A release summary is kept if its format or title looks like a comp/album.
COMP_FORMAT_HINTS = re.compile(r"comp|album", re.IGNORECASE)
COMP_TITLE_HINTS = re.compile(
    r"(volume|vol\.?|the\s+mix|sessions|annual|disco heaven|the album|"
    r"nu cool|beach house|back to|chillout|club classics|anthems)",
    re.IGNORECASE,
)

# Sentinel written to the cache for releases Discogs no longer serves, so we
# don't re-request them on every resume.
TOMBSTONE = {"_unavailable": True}

# Build an SSL context backed by certifi's CA bundle (macOS Python often
# can't find a system bundle otherwise) and apply it to all urllib calls.
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

os.makedirs(CACHE_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Small cache helpers
# ---------------------------------------------------------------------------

def _read_cache(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_cache(path, data):
    """Write JSON atomically so an interrupted write can't corrupt the cache."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# HTTP helper with retry + rate-limit handling
# ---------------------------------------------------------------------------

def _retry_after_seconds(headers, default=60):
    """Parse a Retry-After header, which may be seconds or an HTTP-date."""
    raw = headers.get("Retry-After")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime
        when = parsedate_to_datetime(raw)
        delta = (when - datetime.now(timezone.utc)).total_seconds()
        return max(int(delta), 1)
    except Exception:
        return default


def fetch_json(url, retries=5):
    """Fetch a URL and return parsed JSON, or None on unrecoverable failure.

    Rate-limit (429) waits do NOT consume the retry budget — only genuine
    failures do — so a long throttled run won't abandon URLs prematurely.
    """
    if TOKEN:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}token={urllib.parse.quote(TOKEN)}"

    attempt = 0
    while attempt < retries:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            time.sleep(DELAY)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = _retry_after_seconds(e.headers)
                print(f"  [429] rate limited, sleeping {wait}s", file=sys.stderr)
                time.sleep(wait + 1)
                continue  # deliberately not counted against `retries`
            if e.code in (500, 502, 503, 504):
                attempt += 1
                print(f"  [{e.code}] server error, retry {attempt}", file=sys.stderr)
                time.sleep(5 * attempt)
                continue
            if e.code in (404, 410):
                return None  # release deleted/blocked — caller writes a tombstone
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            attempt += 1
            print(f"  [net] {type(e).__name__}, retry {attempt}", file=sys.stderr)
            time.sleep(5 * attempt)
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
            data = _read_cache(cache_path)
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
            _write_cache(cache_path, data)

        yield from data.get("releases", [])

        pagination = data.get("pagination", {})
        if page >= pagination.get("pages", 1):
            return
        page += 1


# ---------------------------------------------------------------------------
# Release detail fetch (with disk cache + tombstones)
# ---------------------------------------------------------------------------

def fetch_release(release_id):
    """Fetch a single release with its tracklist. Cached to disk.

    Returns the release dict, or None if Discogs no longer serves it. The
    None case is cached as a tombstone so it's never re-requested.
    """
    cache_path = os.path.join(CACHE_DIR, f"release_{release_id}.json")
    if os.path.exists(cache_path):
        cached = _read_cache(cache_path)
        return None if cached.get("_unavailable") else cached

    url = f"https://api.discogs.com/releases/{release_id}"
    data = fetch_json(url)
    _write_cache(cache_path, data if data is not None else TOMBSTONE)
    return data


# ---------------------------------------------------------------------------
# Compilation filter
# ---------------------------------------------------------------------------

def is_compilation(release_summary):
    """Filter releases to compilations/albums only."""
    fmt = release_summary.get("format", "") or ""
    title = release_summary.get("title", "") or ""
    return bool(COMP_FORMAT_HINTS.search(fmt) or COMP_TITLE_HINTS.search(title))


# ---------------------------------------------------------------------------
# Tracklist flattening
# ---------------------------------------------------------------------------

def iter_playable_tracks(tracklist):
    """Yield real tracks from a Discogs tracklist.

    Continuous DJ mixes (the bulk of Hed Kandi / MoS releases) list their
    tracks inside `sub_tracks` under an index/heading entry, so we recurse.
    Headings and standalone index markers are skipped.
    """
    for entry in tracklist:
        sub = entry.get("sub_tracks")
        if sub:
            yield from iter_playable_tracks(sub)
            continue
        if entry.get("type_", "track") == "track":
            yield entry


def track_artist(track, release_artist):
    """Resolve a track's artist string, falling back to the release artist
    only when it's a real (non-"Various") credit."""
    artists = track.get("artists") or []
    name = ", ".join(
        (a.get("name") or "").strip() for a in artists if a.get("name")
    ).strip()
    if name:
        return name
    if release_artist and not release_artist.lower().startswith("various"):
        return release_artist
    return ""


def release_artist_name(detail):
    artists = detail.get("artists") or []
    return ", ".join(
        (a.get("name") or "").strip() for a in artists if a.get("name")
    ).strip()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Glasshouse scrape: Hed Kandi + Ministry of Sound")
    print(f"Mode: {'authenticated (60/min)' if TOKEN else 'anonymous (25/min)'}")
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
        rel_artist = release_artist_name(detail)
        label_name = rel["_source_label"]

        for track in iter_playable_tracks(detail.get("tracklist", [])):
            tname = (track.get("title") or "").strip()
            artist_name = track_artist(track, rel_artist)
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

    # Step 4: write CSV atomically
    print(f"\nWriting {len(track_rows)} unique tracks to {OUTPUT_CSV}")
    tmp_csv = f"{OUTPUT_CSV}.tmp"
    with open(tmp_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "artist", "track", "release_title", "release_year",
            "label", "release_id", "duration",
        ])
        writer.writeheader()
        writer.writerows(track_rows)
    os.replace(tmp_csv, OUTPUT_CSV)

    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run the same command to resume.", file=sys.stderr)
        sys.exit(130)
