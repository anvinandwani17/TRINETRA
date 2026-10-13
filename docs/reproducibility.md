# Reproduce and distribute existing evidence

## Evaluate the frozen pilot

Keep running satellite jobs separate. After the download/SNAP job finishes, use the existing CPU environment and actual cached Kuro Siwo data. Do not retrain or change threshold based on held-out scores.

```powershell
$original = "$env:USERPROFILE\OneDrive\Desktop\TRINETRA"
python scripts/evaluate_siamese.py --root "$original\data\kuro_siwo" --checkpoint "$original\artifacts\models\siamese_v1\pilot\best.pt" --split validation --output artifacts/reports/pilot_validation.json
```

Use `--split test` with a separate output only when genuine official test samples are present. The loader rejects absent samples and changed partition/source hashes. No test imagery is downloaded by this command. The result lists official/local samples, missing events, checkpoint hash, selection events and per-event metrics. Validation reuse is not independent test performance; partial event/sample coverage is not the full benchmark. An empty/failed run is not a zero-accuracy result. This patch does not infer mountain labels or claim a high-relief score.

## Prepare saved-case assets

Run after the active job ends. This reads assets and writes a separate bundle; it does not change processing manifests, weights, thresholds or existing results. Choose a new output directory.

```powershell
$original = "$env:USERPROFILE\OneDrive\Desktop\TRINETRA"
python scripts/release_assets.py package --case-dir "$original\data\processed\trishuli" --model-dir "$original\artifacts\models\siamese_v1\pilot" --output artifacts/release/trishuli_v1
```

The package requires both detectors, observability, comparison/agreement and model config/history. Missing required files stop packaging. Raw scenes, private credentials, and AI preprocessing inputs are not included. Existing portable saved-case API lookup uses recorded filenames. Fresh inference still needs its original hash-matching preprocessing inputs, whose existing manifest may contain absolute paths; this bundle does not make fresh preprocessing portable.

The command produces `TRINETRA_case_assets.zip` and `assets_manifest.json`, containing bundle and per-file hashes, sizes and relative paths. Publish both as release assets after checking original data terms. Publish the trusted bundle hash separately in release notes. This script does not create or publish a GitHub Release, grant public access, select a project license or upload files.

## Fetch a published bundle

Obtain the manifest from your team's trusted release, then use its actual HTTPS bundle URL:

```powershell
python scripts/release_assets.py fetch --manifest assets_manifest.json --bundle-url 'ACTUAL_HTTPS_RELEASE_ASSET_URL' --repo .
```

Validate the release URL and trusted manifest identity independently: hashes verify consistency, not publisher authenticity. The fetcher verifies the bundle and every file before installation, prevents path escape and refuses to overwrite different assets. Same-hash existing files are reused. Test from another machine before calling the submission reproducible.

## Remaining submission tasks

- Select and declare a project code license with the team; attribution is not a code license.
- Publish the assets and verify external access.
- Record fresh uncached SNAP completion and stage timings for supported cases.
- Include real evaluation outputs with their actual coverage, not a generalized pilot score.
- Keep legacy prototype scripts and invalid EMSR927 flood metrics out of the advertised active workflow until separately repaired or retired.
