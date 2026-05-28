#!/usr/bin/env python3
"""
Glasshouse playlist slicer.

Reads glasshouse_master.csv (output of glasshouse_scrape.py) and produces
one CSV per themed playlist, ready for Soundiiz / Tune My Music import.

Logic:
  - Permissive: a track may appear in multiple playlists when its release
    title matches multiple series patterns. Within a single playlist, no
    duplicate (artist, track) pairs.
  - Each playlist is defined by a name + a regex that matches release titles,
    optionally restricted by label, year range, and an exclusion regex.
  - Catch-all playlists collect tracks not matched by ANY specific series.

Note on dedup: glasshouse_scrape.py already dedups globally by (artist, title),
so each track reaches this script exactly once, bound to whichever release was
fetched first. A track therefore lands in multiple playlists only when that one
release title matches multiple patterns -- not because it appeared on multiple
compilations. If you want true multi-series membership, move global dedup out
of the scraper and let this script dedup per playlist instead.

Usage:
    python3 glasshouse_slicer.py

Output:
    ./playlists/<playlist_name>.csv   one file per non-empty playlist
    ./playlists/_summary.txt          per-playlist counts + coverage stats
"""

import csv
import os
import re
import sys

INPUT_CSV = "glasshouse_master.csv"
OUTPUT_DIR = "playlists"

FIELDNAMES = [
    "artist", "track", "release_title", "release_year",
    "label", "release_id", "duration",
]

# ---------------------------------------------------------------------------
# Playlist definitions
# ---------------------------------------------------------------------------
#
# Order does NOT affect results: every specific playlist whose pattern matches
# receives the track (permissive), and catch-alls run in a second pass that
# only sees tracks no specific series claimed. The numeric prefixes are purely
# for readable, sorted output filenames.
#
# P(name, labels, pattern, years=..., exclude=..., accept_unknown_year=...)
#   labels   - set of labels this playlist draws from
#   pattern  - regex matched (case-insensitively) against release_title,
#              or the CATCHALL sentinel
#   years    - specific playlists: None (any year) | (min, max) era window
#              catch-alls:         ANY | NOYEAR | (min, max)
#   exclude  - optional regex; if it matches the title, the track is skipped
#   accept_unknown_year - era-sliced playlist also keeps title matches that
#                         have no parseable year (otherwise they'd be lost)
# ---------------------------------------------------------------------------

HED_KANDI_LABELS = {"Hed Kandi"}
MOS_LABELS = {"Ministry Of Sound", "Ministry Of Sound Recordings"}
KNOWN_LABELS = HED_KANDI_LABELS | MOS_LABELS

CATCHALL = "__CATCHALL__"
ANY = "ANY"        # catch-all: accept any track (with or without a year)
NOYEAR = "NOYEAR"  # catch-all: only tracks with no parseable year

# The Annual matcher is shared by the three era buckets; define it once.
ANNUAL_PATTERN = (
    r"\bthe\s*annual\b|^annual\s|\bannual\s*\d{4}|theannual\d{4}|"
    r"\bannual\s+(I{1,3}|IV|V|VI{0,3}|IX|X{1,3}|XL|XX[VI]{0,3})\b|"
    r"\bthe\s*\d{4}\s*annual\b"
)


def P(name, labels, pattern, years=None, exclude=None, accept_unknown_year=False):
    return {
        "name": name,
        "labels": labels,
        "pattern": pattern,
        "years": years,
        "exclude": exclude,
        "accept_unknown_year": accept_unknown_year,
    }


PLAYLISTS = [
    # ---- Hed Kandi series ----
    P("01_HedKandi_The_Mix", HED_KANDI_LABELS,
      r"\bthe\s+mix\b|^hed\s*kandi\s*the\s*mix|hed\s*kandi:\s*the\s*mix|^hedkandi\s*the\s*mix"),

    P("02_HedKandi_Beach_House", HED_KANDI_LABELS,
      r"beach\s*house|beach\s*life"),

    P("03_HedKandi_Disco_Heaven_and_Kandi", HED_KANDI_LABELS,
      r"disco\s*heaven|disco\s*kandi|nu\s*disco|deep\s*disco|twisted\s*disco|back\s*to\s*disco"),

    P("04_HedKandi_Nu_Cool_and_Chill", HED_KANDI_LABELS,
      r"nu\s*cool|serve\s*chilled|chill(out|ed)?|kandi\s*lounge|sound\s*of\s*winter|sound\s*of\s*sax|winter\s*chill"),

    P("05_HedKandi_World_Series", HED_KANDI_LABELS,
      r"world\s*series|hed\s*kandi[:\s]*(miami|ibiza|tokyo|barcelona|london|san\s*francisco|paris|new\s*york)"),

    P("06_HedKandi_Back_to_Love", HED_KANDI_LABELS,
      r"back\s*to\s*love"),

    P("07_HedKandi_A_Taste_Of_Kandi", HED_KANDI_LABELS,
      r"taste\s*of\s*(kandi|winter)|a\s*taste\s*of"),

    P("08_HedKandi_Tropical_and_Deep", HED_KANDI_LABELS,
      r"tropical|deep\s*house|twisted\s*house"),

    P("09_HedKandi_Classics_and_Anthems", HED_KANDI_LABELS,
      r"classics|anthems|signature|fit\s*&?\s*fabulous|pure\s*kandi|hed\s*kandi\s*\d{4}|hed\s*kandi\s*live"),

    # Catch-all for any Hed Kandi track not matched above (all years)
    P("10_HedKandi_Everything_Else", HED_KANDI_LABELS, CATCHALL, years=ANY),

    # ---- Ministry of Sound: The Annual, sliced by era ----
    # Year-less Annuals default into the earliest bucket so they aren't lost
    # to the generic catch-all.
    P("11_MoS_The_Annual_1999_to_2005", MOS_LABELS, ANNUAL_PATTERN,
      years=(1999, 2005), accept_unknown_year=True),
    P("12_MoS_The_Annual_2006_to_2012", MOS_LABELS, ANNUAL_PATTERN, years=(2006, 2012)),
    P("13_MoS_The_Annual_2013_and_after", MOS_LABELS, ANNUAL_PATTERN, years=(2013, 2099)),

    # ---- Ministry of Sound: Sessions ----
    P("14_MoS_Sessions", MOS_LABELS,
      r"^sessions(\s|$)|\bsessions\s+(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|\d+)|sessions\s+\d{4}|miami\s*sessions|saturday\s*sessions|vegas\s*sessions|late\s*night\s*sessions|summer\s*sessions|bounce\s*sessions|electro\s*house\s*sessions|kid\s*kenobi\s*sessions|chillout\s*sessions"),

    # ---- Ministry of Sound: Clubbers Guide ----
    P("15_MoS_Clubbers_Guide", MOS_LABELS, r"clubber'?s?\s*guide"),

    # ---- Ministry of Sound: Chillout / Chilled ----
    P("16_MoS_Chillout_and_Chilled_House", MOS_LABELS,
      r"chill(ed|out)|chilled\s*house|chillout\s*session|the\s*chillout|laidback|just\s*chillin"),

    # ---- Ministry of Sound: Trance Nation ----
    P("17_MoS_Trance_Nation", MOS_LABELS,
      r"trance\s*nation|trance\s*classics|trance\s*anthems|cream\s*trance"),

    # ---- Ministry of Sound: The Sound Of (genre-specific) ----
    P("18_MoS_The_Sound_Of", MOS_LABELS,
      r"\bthe\s*sound\s*of\b|sound\s*of\s*(dubstep|bassline|deep\s*house|bass|trap|rinse|freedom)"),

    # ---- Ministry of Sound: Bass / Bassline / Addicted To Bass ----
    P("19_MoS_Bass_and_Bassline", MOS_LABELS,
      r"addicted\s*to\s*bass|bassline|maximum\s*bass|back\s*to\s*bass|bass\s*house|bass\s*unleashed"),

    # ---- Ministry of Sound: Mash Up Mix ----
    P("20_MoS_Mash_Up_Mix", MOS_LABELS, r"mash\s*up\s*mix"),

    # ---- Ministry of Sound: Anthems (retrospectives) ----
    # Ibiza-branded anthems belong to the Ibiza playlist; exclude them here.
    P("21_MoS_Anthems", MOS_LABELS,
      r"\banthems\b|winter\s*anthems|summer\s*anthems|club\s*anthems|dance\s*anthems",
      exclude=r"\bibiza\b"),

    # ---- Ministry of Sound: Ibiza / Ayia Napa / Pacha ----
    P("22_MoS_Ibiza_and_Beach_Destinations", MOS_LABELS,
      r"ibiza|ayia\s*napa|pacha|marbella|underground\s*miami|sunshine"),

    # ---- Ministry of Sound: Running Trax (workout) ----
    P("23_MoS_Running_Trax", MOS_LABELS, r"running\s*trax|workout|pump\s*it\s*up"),

    # ---- Ministry of Sound: Back To The Old Skool ----
    P("24_MoS_Old_Skool_and_Classics", MOS_LABELS,
      r"back\s*to\s*the\s*old\s*skool|old\s*skool|classics|garage\s*classics|piano\s*house|funky\s*house|real\s*house"),

    # ---- Ministry of Sound: Mashed series (mashup era) ----
    P("25_MoS_Mashed", MOS_LABELS, r"^mashed\b|\bmashed\s+(two|three|four|five|II|III|IV|V|\d+)"),

    # ---- Ministry of Sound: Masterpiece (curator-led mixes) ----
    P("26_MoS_Masterpiece", MOS_LABELS, r"masterpiece:?\s*created\s*by|^masterpiece\b"),

    # ---- Ministry of Sound: Ritmo De Bacardi (Latin house) ----
    P("27_MoS_Ritmo_De_Bacardi_and_Latin", MOS_LABELS,
      r"ritmo\s*de\s*bacardi|bacardi\s*b-live|fiesta:?\s*latin|latin\s*house|throwback\s*latino"),

    # ---- Ministry of Sound: Hard Dance / Hard House / Hardcore ----
    P("28_MoS_Hard_Dance_and_Hardcore", MOS_LABELS,
      r"hard\s*nrg|hard\s*dance|hard\s*house|hardcore|helter\s*skelter|happy\s*hardcore|gatecrasher|rave\s*nation"),

    # ---- Ministry of Sound: Club Nation + The Underground ----
    P("29_MoS_Club_Nation_and_Underground", MOS_LABELS,
      r"club\s*nation|the\s*underground|underground\s*ibiza|club\s*files|club\s*anthems|club\s*rotation"),

    # ---- Ministry of Sound: Drum & Bass / Dubstep / Garage / Grime ----
    P("30_MoS_DnB_Dubstep_Garage", MOS_LABELS,
      r"drum\s*&?\s*bass|dnb|d&b|dubstep|garage(?!\s*king)|uk\s*garage|grime|jungle|rinse\s*fm"),

    # ---- Ministry of Sound: Uncovered (covers/reinterpretations series) ----
    P("31_MoS_Uncovered", MOS_LABELS, r"^uncovered\b|uncovered:\s*a\s*unique|uncovered\s*vol"),

    # ---- Ministry of Sound: Loveparade (German rave festival comps) ----
    P("32_MoS_Loveparade", MOS_LABELS, r"loveparade|love\s*parade"),

    # ---- Catch-all for MoS tracks not in any specific series, split by era ----
    P("33_MoS_Everything_Else_pre_2005", MOS_LABELS, CATCHALL, years=(1990, 2005)),
    P("34_MoS_Everything_Else_2006_to_2012", MOS_LABELS, CATCHALL, years=(2006, 2012)),
    P("35_MoS_Everything_Else_2013_and_after", MOS_LABELS, CATCHALL, years=(2013, 2099)),
    P("36_MoS_Everything_Else_no_year", MOS_LABELS, CATCHALL, years=NOYEAR),
]


# ---------------------------------------------------------------------------
# Slicer logic
# ---------------------------------------------------------------------------

def compile_playlists(playlists):
    """Precompile regexes in place. Returns the same list of dicts."""
    for pl in playlists:
        pl["is_catchall"] = pl["pattern"] == CATCHALL
        pl["pattern_re"] = None if pl["is_catchall"] else re.compile(pl["pattern"], re.IGNORECASE)
        pl["exclude_re"] = re.compile(pl["exclude"], re.IGNORECASE) if pl["exclude"] else None
    return playlists


def safe_year(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def year_in_window(year, window):
    return year is not None and window[0] <= year <= window[1]


def matches_specific(pl, label, title, year):
    if label not in pl["labels"]:
        return False
    if not pl["pattern_re"].search(title):
        return False
    if pl["exclude_re"] and pl["exclude_re"].search(title):
        return False
    window = pl["years"]
    if window is not None:  # era-sliced
        if year is None:
            return pl["accept_unknown_year"]
        return year_in_window(year, window)
    return True


def matches_catchall(pl, label, year):
    if label not in pl["labels"]:
        return False
    mode = pl["years"]
    if mode == ANY:
        return True
    if mode == NOYEAR:
        return year is None
    return year_in_window(year, mode)


def main():
    if not os.path.exists(INPUT_CSV):
        print(f"ERROR: {INPUT_CSV} not found. Run glasshouse_scrape.py first.")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    compiled = compile_playlists(PLAYLISTS)

    # Each playlist: dict keyed by (artist_lower, track_lower) -> row, for
    # per-playlist dedup.
    playlist_tracks = {pl["name"]: {} for pl in compiled}
    specific_keys = set()    # tracks claimed by >=1 specific series
    catchall_keys = set()    # tracks that only a catch-all claimed
    seen_keys = set()
    rows_read = 0
    unknown_label_rows = 0

    with open(INPUT_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows_read += 1
            title = row.get("release_title", "") or ""
            label = row.get("label", "") or ""
            year = safe_year(row.get("release_year", ""))
            artist = (row.get("artist") or "").strip()
            track = (row.get("track") or "").strip()
            if not artist or not track:
                continue
            if label not in KNOWN_LABELS:
                unknown_label_rows += 1
                continue

            key = (artist.lower(), track.lower())
            seen_keys.add(key)

            # Pass 1: every matching specific series claims the track.
            specific_matched = False
            for pl in compiled:
                if pl["is_catchall"]:
                    continue
                if matches_specific(pl, label, title, year):
                    playlist_tracks[pl["name"]].setdefault(key, row)
                    specific_matched = True

            if specific_matched:
                specific_keys.add(key)
                continue

            # Pass 2: catch-alls only see tracks no specific series claimed.
            for pl in compiled:
                if not pl["is_catchall"]:
                    continue
                if matches_catchall(pl, label, year):
                    playlist_tracks[pl["name"]].setdefault(key, row)
                    catchall_keys.add(key)

    # Write one CSV per non-empty playlist.
    summary_lines = []
    empty_playlists = []
    files_written = 0
    for pl in compiled:
        tracks = playlist_tracks[pl["name"]]
        if not tracks:
            empty_playlists.append(pl["name"])
            summary_lines.append(f"  {0:>6}  {pl['name']}  (empty, not written)")
            continue
        out_path = os.path.join(OUTPUT_DIR, f"{pl['name']}.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(tracks.values())
        files_written += 1
        summary_lines.append(f"  {len(tracks):>6}  {pl['name']}")

    # Write summary (track-level coverage).
    summary_path = os.path.join(OUTPUT_DIR, "_summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("Glasshouse playlist slicer summary\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"Source CSV rows read:                 {rows_read}\n")
        f.write(f"Rows skipped (unknown label):         {unknown_label_rows}\n")
        f.write(f"Unique tracks placed:                 {len(seen_keys)}\n")
        f.write(f"  matched to a specific series:       {len(specific_keys)}\n")
        f.write(f"  placed only by a catch-all:         {len(catchall_keys)}\n")
        f.write(f"Playlist files written (non-empty):   {files_written}\n")
        f.write(f"Empty playlists (skipped):            {len(empty_playlists)}\n")
        f.write("  (a track can appear in multiple playlists when its release\n")
        f.write("   title matches multiple series patterns)\n\n")
        f.write("Per-playlist counts:\n")
        for line in summary_lines:
            f.write(line + "\n")

    for line in summary_lines:
        print(line)
    print()
    if unknown_label_rows:
        print(f"WARNING: skipped {unknown_label_rows} rows with a label outside "
              f"{sorted(KNOWN_LABELS)} -- add it to a label set if expected.")
    print(f"Wrote {files_written} playlist files to {OUTPUT_DIR}/ "
          f"({len(empty_playlists)} empty playlists skipped)")
    print(f"Summary at {summary_path}")


if __name__ == "__main__":
    main()
