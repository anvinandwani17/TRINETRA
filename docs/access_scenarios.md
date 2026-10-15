# Town / medical access scenarios

This extension adds conditional road and foot access scenarios alongside the existing hospital-only, observability-aware counts. It does not change the flood model, thresholds, terrain products, or legacy meanings.

## Inputs and bounded context

The API requests historical OSM for the case polygon plus a **2,000 m metric buffer**. It uses the existing date guards, response hashes, 50 MiB per-layer extraction cap, and local cache. This requests OSM data, not new satellite imagery. The source settlement cohort and exposed-feature accounting remain inside/intersecting the original case polygon. Roads for the scenario graph are clipped to the buffered query boundary.

`TRINETRA_ACCESS_BUFFER_M` may be set between 0 and 5,000 m before backend startup. The actual query polygon, buffer, source hashes, requested snapshot and source availability are recorded in provenance. The first run has different OSM cache identities from the previous case-only query and can take longer. Querying a larger area does not create new satellite support outside the case.

## Fixed pre-event destinations

Destinations are mapped medical facilities and settlement nodes tagged `place=town` or `place=city` in the bounded query area. Each source settlement is excluded from selecting its own OSM identity as a destination. Sources and destinations snap to existing graph nodes within a maximum of 500 m.

For each travel mode, select the nearest reachable mapped destination on the pre-event graph using road-network length plus source/destination snap-connector lengths. Keep that destination fixed for both post-event scenarios. A farther alternative is not silently substituted after closure. The policy means nearest **pre-event reachable, mapped destination in the bounded graph**, not globally nearest settlement or every possible destination.

## Two explicit assumptions

1. **Optimistic:** unknown roads, source/destination locations and snap connectors are open.
2. **Pessimistic:** those unknown roads, locations and connectors are closed.

In both scenarios, candidate-intersecting roads, locations and connectors are conditionally closed. Geometry outside the observed AOI is always unknown, including the new buffer. Candidate exposure is not verified road failure. Snap connectors are assumptions and are screened too.

Counts are reported only for settlements connected to their selected destination on the mapped pre-event graph. Pre-event disconnections, missing snap matches and absent other destinations are reported as **not assessed**, with explicit reasons. A missing road, settlement or medical source produces null/unavailable scenarios, not fabricated zero results.

`cut_off_lower` is the number disconnected with unknown terrain open; `cut_off_upper` is the number disconnected with unknown terrain closed. These are conditional scenario bounds within the eligible cohort, not statistical confidence intervals or bounds on actual disaster casualties/isolation. A 0-to-12 range may be the correct result when evidence is insufficient.

## Outputs

- `summary.access_scenarios`: buffer, policy, status, destination counts, and separate car/foot eligibility and scenario counts.
- `provenance.access_scenarios`: the same assumptions and results, alongside historical query records.
- `settlements.geojson`: per-mode fixed destination, baseline distance, source/destination snap distances, endpoint uncertainty, scenario booleans and non-assessment reasons.
- Dashboard: a dedicated town/medical scenario panel with per-settlement details. Original access labels remain separate.
- Detailed report and one-page PDF: scenario range, eligible denominator and non-assessed count.

## Limitations

The graph uses undirected coordinate-node connections. One-way restrictions, grade-separated crossing inference, missing roads, turning restrictions, barriers and true off-road connectors are not resolved. Existing access and motor-vehicle exclusions are retained. No scenario certifies a safe path, functional medical destination, structural damage or actual isolation. The bounded graph can miss distant alternatives. No live traffic or field observations are used.

## Validation and laptop check

Synthetic fixtures verify unknown-road bounds, known-candidate closures, unknown sources/destinations, self-target exclusion, town destinations, fixed-target selection, pre-event disconnection, outside-AOI uncertainty, source outages, empty graphs, snap limits and query-buffer bounds. An orchestration fixture verifies expanded querying with the original source-settlement cohort. Existing report/API and general-case regression checks passed, as did the frontend TypeScript/Vite build.

Actual Trishuli counts require a new integrated job on the user's laptop with historical OSM available. No scenario counts from unit fixtures are claimed as case-study results. Keep that new results ZIP for review before updating the technical report or demo.
