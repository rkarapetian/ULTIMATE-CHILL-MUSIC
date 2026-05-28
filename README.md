# Ultimate Chill Mix Compilations Catalog

A community-built catalog of compilation tracklists from selected house and electronic music labels, derived from public Discogs metadata.

This is a fan project. No affiliation with any label or with Discogs.

## What this is

A deduplicated database of every track that appeared on a compilation release from:

- Hed Kandi
- Ministry Of Sound
- Ministry Of Sound Recordings

The current dataset contains roughly 39,000 unique tracks across 2,100+ releases, sorted into 36 themed playlists for easy import into streaming services like Apple Music, Spotify, or Tidal.

## What's in the repository

```
glasshouse_master.csv          full deduplicated catalog (one row per unique track)
playlists/                     36 themed playlist CSVs
  01_HedKandi_The_Mix.csv      ...
  02_HedKandi_Beach_House.csv
  ...
  36_MoS_Everything_Else_no_year.csv

glasshouse_scrape.py           the scraper (Discogs API, resumable)
glasshouse_slicer_v3.py        the slicer (master CSV -> themed playlists)
```

Each CSV has the same column structure:

| Column | Description |
|---|---|
| artist | Track artist |
| track | Track title (including any remix or version info) |
| release_title | The compilation it appeared on |
| release_year | Year of that compilation |
| label | Source label |
| release_id | Discogs release ID for cross-referencing |
| duration | Track length if available |

## How to use the data

### Import into Apple Music, Spotify, or Tidal

Use a CSV-to-playlist converter such as Soundiiz or TuneMyMusic. Both accept the CSV format directly. Map the `artist` column to Artist and the `track` column to Title.

### Open in a spreadsheet

Numbers, Excel, or Google Sheets will read the CSVs directly. Sort and filter as you would any tabular data.

### Query as a database

The master CSV imports cleanly into SQLite, DuckDB, or pandas for more sophisticated analysis.

## How to extend the catalog with new labels

The scripts are designed to be extended. To add another label (e.g., Defected):

1. Find the Discogs label ID at discogs.com/label/...
2. Open `glasshouse_scrape.py`
3. Add a line to the `LABELS` list near the top:
   ```python
   LABELS = [
       (774, "Hed Kandi"),
       (1169, "Ministry Of Sound"),
       (66138, "Ministry Of Sound Recordings"),
       (9637, "Defected Records"),  # new line
   ]
   ```
4. Re-run `python3 glasshouse_scrape.py`. The scraper is resumable, so it will skip everything already cached and only fetch the new label.
5. Re-run `python3 glasshouse_slicer_v3.py` to regenerate the themed playlists.

To add new themed playlists, edit the `PLAYLISTS` list in the slicer script.

## Reproducing the data yourself

Requirements: Python 3.9 or later, internet connection. No API key needed.

```bash
git clone https://github.com/YOUR-USERNAME/THIS-REPO.git
cd THIS-REPO
python3 glasshouse_scrape.py     # 45 to 70 minutes
python3 glasshouse_slicer_v3.py  # under 10 seconds
```

The scraper respects the Discogs public API rate limit of 25 requests per minute and is fully resumable. If the run is interrupted, re-run the same command and it will pick up where it left off.

## Data accuracy and limitations

- Tracklists come directly from Discogs and reflect whatever the Discogs community has entered. Errors, missing tracks, and inconsistent formatting all exist.
- Deduplication is based on case-insensitive `(artist, track title)` pairs. A track titled "Put 'Em High" and "Put 'Em High (Radio Edit)" are treated as different tracks.
- The slicer uses regex patterns to assign tracks to themed playlists. Some misclassification is inevitable. Pull requests welcome.

## Legal and attribution

- Track metadata (artist names, track titles, release years) is factual information and is not copyrightable.
- Source data: Discogs (discogs.com), used under their public API.
- Label names and series names ("Hed Kandi," "Ministry Of Sound," "The Annual," "Beach House," etc.) are trademarks of their respective owners and are used here only for descriptive purposes.
- This project is not affiliated with, endorsed by, or connected to Hed Kandi, Ministry Of Sound, Discogs, or any party mentioned in the data.

## License

The code in this repository (scrape and slicer scripts) is released under the MIT License.

The data files (CSV outputs) are released under Creative Commons Zero (CC0), to the extent that any rights apply to a compilation of factual metadata.

## Contributing

If you find errors, miscategorized tracks, or want to add support for additional labels, open an issue or submit a pull request.
