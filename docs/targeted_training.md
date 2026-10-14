# Event-directed candidate training data

The checkpoint uses the author's pinned RandomEvents config in
Orion-AI-Lab/KuroSiwo, commit 4347ed173c4e48f5a9d578bb5fe8453706b08e5e.
Its event assignments differ from the physical train_GRD/test_GRD folders in
the webdataset at e8e61b7b1254b04bfa2b5e26d43db90cb86b5004.
For example, test_GRD/shard-00000.tar starts with event 118, which this
checkpoint assigns to training. Conversely, train_GRD contains event 1111007,
which this checkpoint assigns to test. Folder names alone are not split guards.

The recorded 13-patch test result (IoU 0.1887, precision 0.2162, recall 0.5975)
is a tiny subset of the checkpoint's RandomEvents test partition. It must not
be presented as a full webdataset default-split benchmark result. There are
6 covered events, 4 missing events and only 0.0818% sample coverage. The model
and threshold were unchanged during evaluation.

The training-only fetcher previously omitted test_GRD, making some declared
training/validation events unreachable. Pagination and stale revisions were
ruled out. Repeating those probes cannot resolve that omission.

scripts/fetch_training_targeted.py searches both physical folders while checking
every accepted sample against the unchanged, hash-verified training/validation
indexes. It checks the partition against the frozen run config first, preserves
all held-out manifest rows, and records each sample's physical source revision,
shard and byte offset. No split files or model weights are changed.

Archive boundary metadata and bounded lower-bound searches locate event blocks
instead of blind probes. This requires ordered fixed-layout archives; boundary,
layout, checksum or partition incompatibility stops the operation. MiB and
request budgets are enforced, partial progress/cache is reusable, and known
invalid label records are rejected without weakening validation. An existing
dataset fetch lock is preserved.

Example from TRINETRA-shared, with the backend stopped:

```powershell
$original = "$env:USERPROFILE\OneDrive\Desktop\TRINETRA"
python scripts/fetch_training_targeted.py `
  --root "$original\data\kuro_siwo" `
  --run-config "$original\artifacts\models\siamese_v1\pilot\run_config.json" `
  --train-per-event 4 --val-per-event 2 --max-mib 512 --max-requests 768
```

Existing counts above those small quotas are preserved. These targets are for
a bounded experimental candidate, not adequate representative training or proof
of improved accuracy. Contiguous samples may be spatially correlated. Check
reports/targeted_training_download.json for actual event counts, errors and
rejections. The training guard still requires all declared training/validation
events; never bypass event separation or use test scores to select a checkpoint.

Develop a candidate in a new model-output directory on a GPU. Keep the deployed
pilot intact. Data preparation does not train a model or establish accuracy.
