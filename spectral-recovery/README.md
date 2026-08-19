## Spectral Recovery Algorithm



# TODO: refactor this to the reflect current state

## BAP Input Contract

This module can now ingest BAP exports through `bap_manifest.json`.

Recommended BAP settings:
- `export_profile="spectral_recovery"`
- yearly compositing window via `season_start` and `season_end`

`spectral_rec.py` will:
1. Read `bap_manifest.json` (if present in `bap_composite_dir`)
2. Select entries with `export_profile="spectral_recovery"` and reflectance payload
3. Build a deterministic `{year: filepath}` input mapping for `sr.read_timeseries(...)`

If no manifest is present, it falls back to directory scanning behavior.


