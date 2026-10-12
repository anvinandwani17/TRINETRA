# General pipeline — step 1: identity and metadata discovery

This change adds a separate discovery command for arbitrary supported local
AOIs and historical UTC event days. It does not change the working Trishuli
dashboard, download scenes, run SNAP, or claim general end-to-end completion.

## Commands (from the repository root)

```powershell
python -m pytest tests/test_general_cases.py -q --tb=short
python scripts/discover_case.py --aoi examples/terai_discovery.geojson --date 2026-09-10 --prepare-only
python scripts/discover_case.py --aoi examples/terai_discovery.geojson --date 2026-09-10
```

The Terai example is a catalogue software test; its date does not assert a
verified local flood. No manual product IDs are provided.

## Identity and storage

Normalized GeoJSON geometry + event day form a SHA256 request identity.
The folder uses a 16-character digest; the complete digest and geometry are
checked before reusing it. Changing geometry or date produces a new case.
Different coordinate precision may produce different identities; coordinates
are not rounded. New runs are separate from the existing `trishuli` folder.

- `data/runs/case-<digest>/case_request.json`: validated AOI/date identity.
- `data/runs/case-<digest>/discovery_manifest.json`: stage status.
- `data/runs/case-<digest>/s1_pair.json`: actual selected catalogue pair.
- `data/cache/metadata/`: existing selector's bounded metadata cache.
- `data/processed/case-<digest>/`: reserved future output location, not created.
- `data/raw/sentinel1/`: existing shared product cache, not accessed this step.

The public official Copernicus STAC catalogue needs no credentials for this
metadata query. Authentication remains necessary for later downloads.
The existing selector requires same relative orbit/direction, IW GRDH VV/VH,
99% common AOI coverage, a +/-30-day window, and whole event day exclusion.
A successful pair is hash-verified and pinned on subsequent runs rather than
silently replaced. Failed searches report failure and never generate images.

## Scope and limits

Pilot cap: 300 km2; existing geometry validator also enforces local bounds,
valid Polygon/MultiPolygon, <=5,000 vertices and no dateline crossing.
The 300 km2 cap is a development bound, not a measured processing-time promise.
This step does not choose a processing CRS. Multi-zone terrain processing and
download/preprocess orchestration require later work. Existing API remains
restricted to verified precomputed Trishuli outputs.

Local unit tests use explicit metadata stubs and no network. Actual discovery
must be verified on Anvi's laptop with catalogue access. Successful discovery
is not evidence of a flood or a guarantee that all later stages will succeed.
