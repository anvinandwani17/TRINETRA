# TRINETRA methodology and evidence status

## Inputs and temporal policy
The request is a bounded polygon (at most 300 km²) and an event UTC day. Sentinel-1 IW GRDH VV/VH products must form a compatible same-relative-orbit/direction pre/post pair with at least 99% common coverage. Observations bracket the event; they are not necessarily on the chosen day. Missing post-event scenes cause failure, including requests too close to the present. Geographic grids derive from supported local-case geometry. The baseline orchestrator handles general cases; the experimental neural pipeline can build separate Sigma0 inputs and run a frozen checkpoint for bounded general cases. Its live laptop execution is not yet verified.

## Satellite processing
Real scenes are verified through download manifests and hashes. New physical runs require SNAP/GPT and the existing terrain/radiometry operators. Pre/post imagery shares a checked projected grid. Observability estimates and method-specific exclusions remain separate. Baseline outputs use 0 (no candidate evidence), 1 (candidate evidence), 255 (unknown/outside). A zero does not establish dry terrain. The conservative slope policy is unchanged by this patch; HAND, debris detection and Sentinel-2 confirmation are not implemented.

## Neural pilot
The supplied run_config.json records 104 training samples from 15 events and 2 validation samples from 2 events; the declared official test split was not used. Siamese U-Net uses pre-VV/pre-VH/post-VV/post-VH, fixed linear-to-dB transform, clipping [-35,5], scaling (dB+15)/20, and threshold 0.5. Validation macro-event IoU selected the checkpoint. These pilot metrics cannot establish generalization. Sigma0 preprocessing differences, SAR geometry and steep-slope candidates require investigation. Fresh CPU inference succeeded on the laptop from existing processed Trishuli inputs; this is distinct from fresh SNAP execution.

The evaluation command accepts trained fit/pilot checkpoints, preserves official split/checksum and event-leakage guards, reports the actual local sample/event coverage and never tunes a threshold. A partial test subset must be described as a subset, not the full held-out test. A high-relief evaluation needs independently established terrain labels; this patch does not invent them.

## Infrastructure and conditional access
Historical OSM is extracted for the bounded area and requested pre-event snapshot, with availability and response hashes retained. Features are intersected with candidate and unknown geometry. Candidate-intersecting road edges are removed in a scenario; routes through unknown terrain cannot certify access. Medical facilities inside the AOI are the current destinations. Town destinations, buffered networks and snapping sensitivity are pending. No physical road closure, landing-zone suitability or calibrated failure probability is verified.

## Reference context
The supplied CEMS EMSR927 Bidur package maps mass movement/landslides, not flood water. Its corridor may be compared as explicitly class-mismatched context within analysed coverage; it must not be used as flood IoU or as training labels. Claude's quoted corridor numbers have not been recomputed by this patch. Baseline/AI agreement does not establish either detector's precision or recall.

## Reproduction and exports
api.main:app is the active API. Source identity, sensor dates, output grid, method, checkpoint hash where applicable, historical snapshot, output hashes, exclusions and availability appear in job-specific exports. Current basemap tiles are not the historical OSM analysis data. Exported GeoJSON is EPSG:4326. Unknown-road/building layers are retained. The export's candidate_affected_roads filename replaces a misleading legacy name while API keys stay compatible.

The laptop demonstrated saved Terai baseline integration with a successful live OSM fetch and saved Trishuli AI integration. Fresh CPU neural inference completed separately. A fresh uncached baseline run was downloading when this patch was prepared; completion and timings remain pending. Earlier 21 API tests and frontend build results are historical; see this patch's validation.json for its own checks.

## Scope and submission gaps
General-case AI laptop validation, real flood ground-truth evaluation, HAND, debris, Sentinel-2, town/buffer routing and multilingual reports remain pending. Legacy demo/evaluation modules remain in the repository and are not used by the active API. Report attribution does not select a project license. A published asset release, external access test and declared code license require a separate release step.
