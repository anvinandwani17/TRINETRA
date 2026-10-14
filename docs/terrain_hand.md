# Experimental HAND review

Existing `baseline` and `experimental_ai` remain unchanged. Optional API/UI methods `baseline_hand` and `experimental_ai_hand` add a provisional drainage-height screen. New outputs are stored in `terrain_hand`, `baseline_hand` and `ai_flood_hand`. Rejected/unresolved terrain is 255 unknown; it is not dry or safe routing terrain.

The bounded positive-elevation DEM pair is checksum verified, zero/missing cells excluded, bilinearly transferred onto a 60 m metric routing grid, and required to agree within 20 m. Priority-flood conditioning supplies acyclic flat routing; D8 chooses steepest descending neighbors. Contributing area initiates channels at 1 km2. HAND uses original DEM elevation minus the elevation of the first channel reached along the downstream flowpath. Negative values inside filled depressions, unresolved outlets, and unknown cells are unsupported. Boundary channels are excluded; upstream context is bounded and accumulation is a local lower bound. This is a terrain prior, not a hydraulic flood simulation.

The chosen 10 m ceiling and 1 km2 drainage area are provisional. The CLI also reports sensitivity at drainage thresholds 0.5/1/2 km2 and HAND ceilings 5/10/15 m. No labels or test metrics select thresholds. HAND does not establish prediction accuracy or debris/pluvial flood coverage. It sharply reduces assessable coverage and must not replace the default merely because hillside candidates disappear.

On the uploaded Trishuli DEM and recovered frozen predictions: baseline 151 -> 154 candidates; AI 27,868 -> 6,719 candidates. AI candidates on slopes >=30 degrees 16,765 -> 125. Assessable AOI fractions become approximately 6.54% baseline and 6.47% AI. These are whole saved-raster comparisons, not the smaller API AOI-clipped counts. This does NOT meet the desired increase in assessable baseline area. The existing held-out test result is unchanged.

Baseline HAND reuses hash-checked cached 3x3 filtered VV/VH before/after arrays, keeps radiometric thresholds and minimum component area, removes the 10-degree cap, and requires a valid slope neighborhood plus eligible HAND. Its score uses radiometric margins without the old slope weight. AI HAND preserves original frozen model scores only on eligible pixels; unsupported scores become NaN. Neither score is a calibrated probability.

From TRINETRA-shared, with the original cached Trishuli terrain still present:

```powershell
$original = "$env:USERPROFILE\OneDrive\Desktop\TRINETRA"
python scripts/run_hand_review.py --case-dir data/processed/trishuli --source-case-dir "$original\data\processed\trishuli"
```

Before starting the backend for saved Trishuli HAND:

```powershell
$env:TRINETRA_HAND_SOURCE_CASE_DIR = "$original\data\processed\trishuli"
```

New physically processed general cases use their own verified intermediate cache. Saved-output bundles lack the full DEM/radiometry context and cannot reconstruct HAND by themselves. A missing, changed or incompatible cache stops the HAND method explicitly; the existing methods remain available. Interrupted HAND folders/locks must be inspected and preserved before retrying. No automatic overwrite or lock deletion is performed.

References: Nobre et al. (2011), Height Above the Nearest Drainage, Journal of Hydrology, DOI 10.1016/j.jhydrol.2011.03.051, https://www.sciencedirect.com/science/article/pii/S0022169411002599 . HAND follows downstream drainage topology rather than the nearest channel by Euclidean distance. The implementation uses only existing NumPy/SciPy/Rasterio dependencies and is not copied from pysheds.
