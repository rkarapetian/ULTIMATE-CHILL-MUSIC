#!/usr/bin/env python3
"""
Glasshouse playlist slicer.

Reads glasshouse_master.csv (output of glasshouse_scrape.py) and produces
one CSV per themed playlist, ready for Soundiiz / Tune My Music import.

Logic:
  - Permissive dedup: a track may appear in multiple playlists if it was
    released across multiple matching series. Within a single playlist, no
    duplicate (artist, track) pairs.
  - Each playlist is defined by a name + a regex that matches release titles.
  - Catch-all playlists collect anything not matched by a specific series.

Usage:
    python3 glasshouse_slicer.py

Output:
    ./playlists/<playlist_name>.csv   one file per themed playlist
    ./playlists/_summary.txt          counts per playlist + unmatched releases
"""

import csv
import os
import re
import sys
from collections import defaultdict

INPUT_CSV = "glasshouse_master.csv"
OUTPUT_DIR = "playlists"

# ---------------------------------------------------------------------------
# Playlist definitions
# ---------------------------------------------------------------------------
#
# Order matters for the "catch-all" buckets: more specific matchers should
# come first. A track can match multiple playlists (permissive mode), but the
# catch-all playlists only collect tracks not matched by ANY specific series.
#
# Each entry: (playlist_name, label_filter_or_None, regex_pattern, year_range_or_None)
#   - label_filter: restricts to tracks from a specific label, or None for any
#   - regex_pattern: matches against release_title (case-insensitive)
#   - year_range: (min_year, max_year) inclusive, or None for any year
#
# ---------------------------------------------------------------------------

HED_KANDI_LABELS = {"Hed Kandi"}
MOS_LABELS = {"Ministry Of Sound", "Ministry Of Sound Recordings"}

PLAYLISTS = [
    # ---- Hed Kandi series ----
    ("01_HedKandi_The_Mix",
     HED_KANDI_LABELS,
     r"\bthe\s+mix\b|^hed\s*kandi\s*the\s*mix|hed\s*kandi:\s*the\s*mix|^hedkandi\s*the\s*mix",
     None),

    ("02_HedKandi_Beach_House",
     HED_KANDI_LABELS,
     r"beach\s*house|beach\s*life",
     None),

    ("03_HedKandi_Disco_Heaven_and_Kandi",
     HED_KANDI_LABELS,
     r"disco\s*heaven|disco\s*kandi|nu\s*disco|deep\s*disco|twisted\s*disco|back\s*to\s*disco",
     None),

    ("04_HedKandi_Nu_Cool_and_Chill",
     HED_KANDI_LABELS,
     r"nu\s*cool|serve\s*chilled|chill(out|ed)?|kandi\s*lounge|sound\s*of\s*winter|sound\s*of\s*sax|winter\s*chill",
     None),

    ("05_HedKandi_World_Series",
     HED_KANDI_LABELS,
     r"world\s*series|hed\s*kandi[:\s]*(miami|ibiza|tokyo|barcelona|london|san\s*francisco|paris|new\s*york)",
     None),

    ("06_HedKandi_Back_to_Love",
     HED_KANDI_LABELS,
     r"back\s*to\s*love",
     None),

    ("07_HedKandi_A_Taste_Of_Kandi",
     HED_KANDI_LABELS,
     r"taste\s*of\s*(kandi|winter)|a\s*taste\s*of",
     None),

    ("08_HedKandi_Tropical_and_Deep",
     HED_KANDI_LABELS,
     r"tropical|deep\s*house|twisted\s*house",
     None),

    ("09_HedKandi_Classics_and_Anthems",
     HED_KANDI_LABELS,
     r"classics|anthems|signature|fit\s*&?\s*fabulous|pure\s*kandi|hed\s*kandi\s*\d{4}|hed\s*kandi\s*live",
     None),

    # Catch-all for any Hed Kandi track not matched above (all years)
    ("10_HedKandi_Everything_Else",
     HED_KANDI_LABELS,
     "__CATCHALL__",
     "ALL_YEARS"),

    # ---- Ministry of Sound: The Annual, sliced by era ----
    ("11_MoS_The_Annual_1999_to_2005",
     MOS_LABELS,
     r"\bthe\s*annual\b|^annual\s|\bannual\s*\d{4}|theannual\d{4}|\bannual\s+(I{1,3}|IV|V|VI{0,3}|IX|X{1,3}|XL|XX[VI]{0,3})\b|\bthe\s*\d{4}\s*annual\b",
     (1999, 2005)),

    ("12_MoS_The_Annual_2006_to_2012",
     MOS_LABELS,
     r"\bthe\s*annual\b|^annual\s|\bannual\s*\d{4}|theannual\d{4}|\bannual\s+(I{1,3}|IV|V|VI{0,3}|IX|X{1,3}|XL|XX[VI]{0,3})\b|\bthe\s*\d{4}\s*annual\b",
     (2006, 2012)),

    ("13_MoS_The_Annual_2013_and_after",
     MOS_LABELS,
     r"\bthe\s*annual\b|^annual\s|\bannual\s*\d{4}|theannual\d{4}|\bannual\s+(I{1,3}|IV|V|VI{0,3}|IX|X{1,3}|XL|XX[VI]{0,3})\b|\bthe\s*\d{4}\s*annual\b",
     (2013, 2099)),

    # ---- Ministry of Sound: Sessions ----
    ("14_MoS_Sessions",
     MOS_LABELS,
     r"^sessions(\s|$)|\bsessions\s+(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|\d+)|sessions\s+\d{4}|miami\s*sessions|saturday\s*sessions|vegas\s*sessions|late\s*night\s*sessions|summer\s*sessions|bounce\s*sessions|electro\s*house\s*sessions|kid\s*kenobi\s*sessions|chillout\s*sessions",
     None),

    # ---- Ministry of Sound: Clubbers Guide ----
    ("15_MoS_Clubbers_Guide",
     MOS_LABELS,
     r"clubber'?s?\s*guide",
     None),

    # ---- Ministry of Sound: Chillout / Chilled ----
    ("16_MoS_Chillout_and_Chilled_House",
     MOS_LABELS,
     r"chill(ed|out)|chilled\s*house|chillout\s*session|the\s*chillout|laidback|just\s*chillin",
     None),

    # ---- Ministry of Sound: Trance Nation ----
    ("17_MoS_Trance_Nation",
     MOS_LABELS,
     r"trance\s*nation|trance\s*classics|trance\s*anthems|cream\s*trance",
     None),

    # ---- Ministry of Sound: The Sound Of (genre-specific) ----
    ("18_MoS_The_Sound_Of",
     MOS_LABELS,
     r"\bthe\s*sound\s*of\b|sound\s*of\s*(dubstep|bassline|deep\s*house|bass|trap|rinse|freedom)",
     None),

    # ---- Ministry of Sound: Bass / Bassline / Addicted To Bass ----
    ("19_MoS_Bass_and_Bassline",
     MOS_LABELS,
     r"addicted\s*to\s*bass|bassline|maximum\s*bass|back\s*to\s*bass|bass\s*house|bass\s*unleashed",
     None),

    # ---- Ministry of Sound: Mash Up Mix ----
    ("20_MoS_Mash_Up_Mix",
     MOS_LABELS,
     r"mash\s*up\s*mix",
     None),

    # ---- Ministry of Sound: Anthems (retrospectives) ----
    ("21_MoS_Anthems",
     MOS_LABELS,
     r"^anthems\b|\banthems\b(?!.*ibiza)|winter\s*anthems|summer\s*anthems|club\s*anthems|dance\s*anthems",
     None),

    # ---- Ministry of Sound: Ibiza / Ayia Napa / Pacha ----
    ("22_MoS_Ibiza_and_Beach_Destinations",
     MOS_LABELS,
     r"ibiza|ayia\s*napa|pacha|marbella|underground\s*miami|sunshine",
     None),

    # ---- Ministry of Sound: Running Trax (workout) ----
    ("23_MoS_Running_Trax",
     MOS_LABELS,
     r"running\s*trax|workout|pump\s*it\s*up",
     None),

    # ---- Ministry of Sound: Back To The Old Skool ----
    ("24_MoS_Old_Skool_and_Classics",
     MOS_LABELS,
     r"back\s*to\s*the\s*old\s*skool|old\s*skool|classics|garage\s*classics|piano\s*house|funky\s*house|real\s*house",
     None),

    # ---- Ministry of Sound: Mashed series (mashup era) ----
    ("25_MoS_Mashed",
     MOS_LABELS,
     r"^mashed\b|\bmashed\s+(two|three|four|five|II|III|IV|V|\d+)",
     None),

    # ---- Ministry of Sound: Masterpiece (curator-led mixes) ----
    ("26_MoS_Masterpiece",
     MOS_LABELS,
     r"masterpiece:?\s*created\s*by|^masterpiece\b",
     None),

    # ---- Ministry of Sound: Ritmo De Bacardi (Latin house) ----
    ("27_MoS_Ritmo_De_Bacardi_and_Latin",
     MOS_LABELS,
     r"ritmo\s*de\s*bacardi|bacardi\s*b-live|fiesta:?\s*latin|latin\s*house|throwback\s*latino",
     None),

    # ---- Ministry of Sound: Hard Dance / Hard House / Hardcore ----
    ("28_MoS_Hard_Dance_and_Hardcore",
     MOS_LABELS,
     r"hard\s*nrg|hard\s*dance|hard\s*house|hardcore|helter\s*skelter|happy\s*hardcore|gatecrasher|rave\s*nation",
     None),

    # ---- Ministry of Sound: Club Nation + The Underground ----
    ("29_MoS_Club_Nation_and_Underground",
     MOS_LABELS,
     r"club\s*nation|the\s*underground|underground\s*ibiza|club\s*files|club\s*anthems|club\s*rotation",
     None),

    # ---- Ministry of Sound: Drum & Bass / Dubstep / Garage / Grime ----
    ("30_MoS_DnB_Dubstep_Garage",
     MOS_LABELS,
     r"drum\s*&?\s*bass|dnb|d&b|dubstep|garage(?!\s*king)|uk\s*garage|grime|jungle|rinse\s*fm",
     None),

    # ---- Ministry of Sound: Uncovered (covers/reinterpretations series) ----
    ("31_MoS_Uncovered",
     MOS_LABELS,
     r"^uncovered\b|uncovered:\s*a\s*unique|uncovered\s*vol",
     None),

    # ---- Ministry of Sound: Loveparade (German rave festival comps) ----
    ("32_MoS_Loveparade",
     MOS_LABELS,
     r"loveparade|love\s*parade",
     None),

    # ---- Catch-all for MoS tracks not in any specific series, split by era ----
    ("33_MoS_Everything_Else_pre_2005",
     MOS_LABELS,
     "__CATCHALL__",
     (1990, 2005)),

    ("34_MoS_Everything_Else_2006_to_2012",
     MOS_LABELS,
     "__CATCHALL__",
     (2006, 2012)),

    ("35_MoS_Everything_Else_2013_and_after",
     MOS_LABELS,
     "__CATCHALL__",
     (2013, 2099)),

    ("36_MoS_Everything_Else_no_year",
     MOS_LABELS,
     "__CATCHALL__",
     None),
]

# ---------------------------------------------------------------------------
# Slicer logic
# ---------------------------------------------------------------------------

def compile_playlists(playlists):
    compiled = []
    for name, labels, pattern, year_range in playlists:
        if pattern == "__CATCHALL__":
            # year_range can be: "ALL_YEARS" (no filter), None (only no-year tracks),
            # or a (min, max) tuple
            compiled.append((name, labels, None, year_range, True))
        else:
            compiled.append((name, labels, re.compile(pattern, re.IGNORECASE), year_range, False))
    return compiled


def safe_year(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def main():
    if not os.path.exists(INPUT_CSV):
        print(f"ERROR: {INPUT_CSV} not found. Run glasshouse_scrape.py first.")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    compiled = compile_playlists(PLAYLISTS)

    # Each playlist holds a dict keyed by (artist_lower, track_lower) for dedup within playlist
    playlist_tracks = {name: {} for name, _, _, _, _ in compiled}
    matched_release_ids = set()  # for catch-all logic
    all_release_ids = set()
    rows_read = 0

    with open(INPUT_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows_read += 1
            release_title = row.get("release_title", "")
            label = row.get("label", "")
            year = safe_year(row.get("release_year", ""))
            release_id = row.get("release_id", "")
            artist = (row.get("artist") or "").strip()
            track = (row.get("track") or "").strip()
            if not artist or not track:
                continue

            all_release_ids.add(release_id)
            key = (artist.lower(), track.lower())

            # First pass: match specific (non-catchall) playlists
            specific_matched = False
            for name, labels, pattern, year_range, is_catchall in compiled:
                if is_catchall:
                    continue
                if labels and label not in labels:
                    continue
                if year_range and year is not None:
                    if not (year_range[0] <= year <= year_range[1]):
                        continue
                if year_range and year is None:
                    continue  # year required for era-sliced playlists
                if pattern.search(release_title):
                    if key not in playlist_tracks[name]:
                        playlist_tracks[name][key] = row
                    specific_matched = True
                    matched_release_ids.add(release_id)

            # Second pass: catch-alls only collect tracks NOT matched by any specific
            if not specific_matched:
                for name, labels, pattern, year_range, is_catchall in compiled:
                    if not is_catchall:
                        continue
                    if labels and label not in labels:
                        continue
                    # Catch-all year-range logic:
                    #   "ALL_YEARS" -> accept any track (year or no year)
                    #   None        -> only tracks with NO valid year
                    #   (min, max)  -> only tracks within year range
                    if year_range == "ALL_YEARS":
                        pass  # accept anything
                    elif year_range is None:
                        if year is not None:
                            continue
                    else:
                        if year is None:
                            continue
                        if not (year_range[0] <= year <= year_range[1]):
                            continue
                    if key not in playlist_tracks[name]:
                        playlist_tracks[name][key] = row

    # Write each playlist CSV
    summary_lines = []
    total_tracks_written = 0
    for name, _, _, _, _ in compiled:
        tracks = playlist_tracks[name]
        out_path = os.path.join(OUTPUT_DIR, f"{name}.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "artist", "track", "release_title", "release_year",
                "label", "release_id", "duration",
            ])
            writer.writeheader()
            for row in tracks.values():
                writer.writerow(row)
        line = f"  {len(tracks):>6}  {name}"
        summary_lines.append(line)
        total_tracks_written += len(tracks)
        print(line)

    # Write summary
    unmatched_count = len(all_release_ids - matched_release_ids)
    summary_path = os.path.join(OUTPUT_DIR, "_summary.txt")
    with open(summary_path, "w") as f:
        f.write("Glasshouse playlist slicer summary\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"Source CSV rows read: {rows_read}\n")
        f.write(f"Unique releases seen: {len(all_release_ids)}\n")
        f.write(f"Releases matched to a specific series: {len(matched_release_ids)}\n")
        f.write(f"Releases captured only by catch-all: {unmatched_count}\n")
        f.write(f"Total track slots filled across playlists: {total_tracks_written}\n")
        f.write("  (a track can appear in multiple playlists in permissive mode)\n\n")
        f.write("Per-playlist counts:\n")
        for line in summary_lines:
            f.write(line + "\n")

    print()
    print(f"Wrote {len(compiled)} playlist files to {OUTPUT_DIR}/")
    print(f"Summary at {summary_path}")


if __name__ == "__main__":
    main()
