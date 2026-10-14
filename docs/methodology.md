# TRINETRA methodology and evidence status

## Inputs and temporal policy
The request is a bounded polygon (at most 300 km²) and an event UTC day. Sentinel-1 IW GRDH VV/VH products must form a compatible same-relative-orbit/direction pre/post pair with at least 99% common coverage. Observations bracket the event; they are not necessarily on the chosen day. Missing post-event scenes cause failure, including requests too close to the present. Geographic grids derive from supported local-case geometry. The baseline orchestrator handles general cases; the experimental neural pipeline builds separate Sigma0 inputs and runs a frozen checkpoint. Laptop execution and integrated export were verified for one custom case; broader coverage and accuracy remain unvalidated.

## Satellite processing
Real scenes are verified through download manifests and hashes. New physical runs require SNAP/GPT and the existing terrain/radiometry operators. Pre/post imagery shares a checked projected grid. Observability estimates and method-specific exclusions remain separate. Baseline outputs use 0 (no candidate evidence), 1 (candidate evidence), 255 (unknown/outside). A zero does not establish dry terrain. The original baseline retains its conservative slope cap. Optional HAND variants use provisional drainage-height screening and separate outputs, with rejected/unresolved terrain unknown. HAND's approximately 6.5% Trishuli assessability is a severe coverage limitation; reduced candidate counts do not establish accuracy. Debris detection and Sentinel-2 confirmation are not implemented.

## Neural pilot
The supplied run_config.json records 104 training samples from 15 events and 2 validation samples from 2 events; the declared official test split was not used for training/checkpoint selection. A later separate four-patch/two-event test subset scored IoU 0.3194 and precision approximately 0.384. Siamese U-Net uses pre-VV/pre-VH/post-VV/post-VH, fixed linear-to-dB transform, clipping [-35,5], scaling (dB+15)/20, and threshold 0.5. Validation macro-event IoU selected the checkpoint. These tiny subsets cannot establish broad generalization. Sigma0 preprocessing differences, SAR geometry and steep-slope candidates remain concerns. Inference and one custom-case Sigma0 run succeeded on the laptop; neither establishes prediction accuracy.

The evaluation command accepts trained fit/pilot checkpoints, preserves official split/checksum and event-leakage guards, reports the actual local sample/event coverage and never tunes a threshold. A partial test subset must be described as a subset, not the full held-out test. A high-relief evaluation needs independently established terrain labels; this patch does not invent them.

## Infrastructure and conditional access
Historical OSM is extracted for the bounded area and requested pre-event snapshot, with availability and response hashes retained. Features are intersected with candidate and unknown geometry. Candidate-intersecting road edges are removed in a scenario; routes through unknown terrain cannot certify access. Medical facilities inside the AOI are the current destinations. Town destinations, buffered networks and snapping sensitivity are pending. No physical road closure, landing-zone suitability or calibrated failure probability is verified.

## Reference context
The supplied CEMS EMSR927 Bidur package maps mass movement/landslides, not flood water. Its corridor may be compared as explicitly class-mismatched context within analysed coverage; it must not be used as flood IoU or as training labels. Claude's quoted corridor numbers have not been recomputed by this patch. Baseline/AI agreement does not establish either detector's precision or recall.

## Reproduction and exports
api.main:app is the active API. Source identity, sensor dates, output grid, method, checkpoint hash where applicable, historical snapshot, output hashes, exclusions and availability appear in job-specific exports. Current basemap tiles are not the historical OSM analysis data. Exported GeoJSON is EPSG:4326. Unknown-road/building layers are retained. The export's candidate_affected_roads filename replaces a misleading legacy name while API keys stay compatible.

The laptop demonstrated saved baseline/AI integration, custom-case general AI processing/export, and optional Trishuli HAND generation/integration/export. See evaluation_status.md for the verified jobs and measured coverage. Historical test counts are evidence for their specific revisions, not a new full-suite result or clean-install certification.

## Scope and submission gaps
Broader general-case validation, representative independent flood evaluation, HAND accuracy, debris, Sentinel-2, town/buffer routing and multilingual reports remain pending. Unused job/CLI, synthetic OSM, uncalibrated probability, landing-zone and default-filled report prototypes are retired; active verified infrastructure/access/grounded reports remain. Report attribution does not select a project license. Public asset delivery, clean-install startup and a declared code license remain incomplete. See evaluation_status.md and clean_install.md for evidence and acceptance steps.
