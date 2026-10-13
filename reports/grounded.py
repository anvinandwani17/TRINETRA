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
              '## Data availability', '']
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
