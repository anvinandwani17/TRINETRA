# General pipeline — step 3: physical SNAP preprocessing

Requires completed general-case discovery and verified downloads (steps 1/2).
The general runner checks the pinned pair and completed download identities.
Raw hashes are rechecked before processing. Output is confined to that case's
own `data/processed/case-<digest>/` folder, preserving the Trishuli outputs.

## First prepare the graphs, without running SNAP

```powershell
python -m pytest tests/test_general_preprocessing.py -q --tb=short
python scripts/preprocess_general_case.py --aoi examples/terai_discovery.geojson --date 2026-09-10 --prepare-only
```

The status must be `graphs_prepared_only`. Four graph XML files are written;
no imagery or previews are claimed. This validates raw hashes and reads the
actual archived processing version. It does not need to execute GPT.

## Then execute the physical workflow on the Windows laptop

```powershell
python scripts/preprocess_general_case.py --aoi examples/terai_discovery.geojson --date 2026-09-10 --gpt "C:\Program Files\esa-snap\bin\gpt.exe"
```

Requires the existing SNAP/Microwave Toolbox installation. Default grid is
20 m. Thermal-noise handling, original-IPF border policy, Beta0 calibration,
terrain-flattened Gamma0, range-Doppler geocoding and SAR-simulated shadow/
layover remain unchanged. Pre/post VV and VH share the same snapped grid.
The AOI centroid chooses the UTM zone and northern/southern hemisphere.
Small AOIs crossing a zone boundary use one centroid UTM CRS throughout.
This is a regional projected grid, not a claim of exactly 20 m ground
resolution everywhere or empirically verified coregistration accuracy.

The local AOI cap is 300 km2, with existing vertex/extent/grid limits. Terrain
context remains a 20 km buffer. Dateline-crossing terrain context and context
outside supported UTM latitudes fail rather than triggering global crops.
The built-in Copernicus 30 m DEM may download uncached local DEM tiles and
orbit support files. No additional Sentinel-1 scenes are selected/downloaded.

Outputs after successful real SNAP execution:

- `processing_manifest.json`: identity, source/raw/output hashes, grid, checks.
- `before_gamma0_db.tif`, `after_gamma0_db.tif`: VV/VH two-band rasters.
- `change_db.tif`: post-minus-pre VV/VH change.
- `observability.tif`: 1=observable, 0=unknown, 255=outside AOI.
- `quality_reasons.tif`, pre/post layover-shadow GeoTIFFs.
- `previews/`: before, after, change and observability PNGs.

Shadow/layover or missing radiometry remain unknown, never dry. The existing
per-graph/source caches and integrity checks are retained. The CLI uses a
case-specific lock. A completed general processing report cannot be replaced
by a preparation-only report. Existing differing case identities are refused.

Changes modify only grid construction and case identity/output handling in
two existing preprocessing files. The installer backs them up first, accepts
harmless line-ending/final-blank-line differences, and refuses other source
changes. The legacy Trishuli date, script and default folder remain supported.

This step does not classify floods, run neural inference or enable arbitrary
cases in the dashboard. Real new-case SNAP results must be verified locally;
unit tests contain explicitly miniature fixtures and never certify imagery.
