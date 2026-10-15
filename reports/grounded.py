"""Deterministic situation report derived only from the integrated job."""
from datetime import datetime, timezone
from reports.credits import ATTRIBUTIONS


def report(job):
    summary, provenance = job['summary'], job['provenance']
    pair = provenance.get('source_pair', {})
    lines = ['# TRINETRA situation report', '', f"Generated: {datetime.now(timezone.utc).isoformat()}",
             f"Job: {job.get('id', 'Unavailable')}", f"Case: {summary.get('case', 'Unavailable')}",
             f"Event date: {job['flood_date']}", f"Method: {summary['method']}", '',
             '## Satellite observations', '',
             f"Before: {pair.get('pre_datetime')} — {pair.get('pre_id')}",
             f"After: {pair.get('post_datetime')} — {pair.get('post_id')}",
             f"Relative orbit: {pair.get('relative_orbit')}; direction: {pair.get('orbit_direction')}",
             f"Output grid: {summary['resolution_m']:g} m", '',
             f"Candidate inundation evidence: {summary['candidate_area_km2']:.4f} km² ({summary['candidate_pixels']} pixels).",
             f"Assessable by the selected method: {summary['assessable_fraction']:.1%} of AOI pixels.",
             f"Unknown or unsupported: {summary['unknown_area_km2']:.4f} km². These areas are not classified as dry.", '',
             'These are candidate flood detections. They do not establish confirmed flood extent, physical road damage or safe passage.', '',
             '## Infrastructure and conditional access', '',
             f"OSM snapshot requested: {summary.get('osm_snapshot') or 'Unavailable'}", '',
             '| Measure | Result |', '|---|---|']
    metrics = [('Mapped settlements', 'total_settlements'), ('Potential road-access loss to mapped medical facilities', 'settlements_cut_off'),
               ('Settlements with unknown road access', 'settlements_access_unknown'), ('Candidate-intersecting mapped road features', 'damaged_road_segments'),
               ('Candidate-intersecting mapped bridges', 'affected_bridges'), ('Candidate-intersecting mapped buildings', 'affected_buildings')]
    for label, key in metrics:
        value = summary.get(key)
        lines.append(f"| {label} | {value if value is not None else 'Unknown / unavailable'} |")
    lines += ['', 'Route results model the removal of candidate-intersecting road edges. No route is certified safe. Medical facility access is a bounded proxy, not proof of settlement isolation from all destinations.', '',
              ]
    scenarios = summary.get('access_scenarios')
    if scenarios:
        lines += ['', '## Town / medical access scenarios', '',
                  'Counts are conditional bounds for settlements connected on the mapped pre-event graph. Each uses a fixed nearest reachable town/city or medical destination; the source town cannot select itself.',
                  'Candidate-intersecting roads, endpoints and snap connectors are closed in both scenarios. Unknown roads, endpoints and connectors are open in the optimistic scenario and closed in the pessimistic scenario.', '',
                  f"Network context buffer: {scenarios.get('network_buffer_m')} m; endpoint snap limit: {scenarios.get('snap_limit_m')} m.", '']
        if scenarios.get('status') != 'complete':
            lines.append('Unknown / unavailable: ' + scenarios.get('reason', 'Required historical infrastructure is unavailable'))
        else:
            lines += ['| Mode | Baseline-connected assessed | Disconnected: unknown open | Disconnected: unknown closed | Not assessed | Already baseline-disconnected |',
                      '|---|---|---|---|---|---|']
            for mode in ['car', 'foot']:
                value = scenarios[mode]
                lines.append(f"| {mode} | {value['eligible']} | {value['optimistic_disconnected']} | {value['pessimistic_disconnected']} | {value['not_assessed']} | {value['baseline_disconnected']} |")
            lines += ['', 'Per-settlement fixed destinations and scenario booleans are in settlements.geojson. Counts do not establish physical isolation or safe routes. Snap connectors are modelling assumptions.']
        lines += ['', scenarios.get('graph_model', ''), '']
    lines += ['', '## Data availability', '']
    for name, state in summary['infrastructure_status'].items():
        lines.append(f'- {name}: {state}')
    lines += ['', '## Limitations and verification priorities', '']
    for warning in dict.fromkeys(job.get('warnings', []) + provenance.get('limitations', [])):
        lines.append('- ' + warning)
    lines += ['', 'Prioritize field checks of candidate intersections and routes passing through unknown terrain. Landing zones and closure probabilities are not provided.', '',
              '## Reproducibility', '', f"Processing manifest SHA256: {provenance.get('processing_manifest_sha256')}",
              f"Detection manifest SHA256: {provenance.get('detection_manifest_sha256')}"]
    for role, sha in provenance.get('output_sha256', {}).items():
        lines.append(f'{role} SHA256: {sha}')
    if provenance.get('checkpoint_sha256'):
        lines.append('Checkpoint SHA256: ' + provenance['checkpoint_sha256'])
    lines += ['', ATTRIBUTIONS.strip()]
    return '\n'.join(lines) + '\n'

