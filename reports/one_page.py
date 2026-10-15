"""Generate a compact A4 PDF directly from a completed integrated job.

The full report, source records and all warnings remain available separately.
No model inference, geometry or summary values are changed by this renderer.
"""
from datetime import datetime, timezone
from html import escape
import io
import math


def _text(value):
    return 'Unknown / unavailable' if value is None or value == '' else str(value)


def _number(value, decimals=0):
    if isinstance(value, bool) or value is None:
        return 'Unknown / unavailable'
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 'Unknown / unavailable'
    if not math.isfinite(number) or number < 0:
        return 'Unknown / unavailable'
    return f'{number:,.{decimals}f}'


def _fraction(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 'Unknown / unavailable'
    if isinstance(value, bool) or not math.isfinite(number) or not 0 <= number <= 1:
        return 'Unknown / unavailable'
    return f'{100 * number:.2f}%'


def one_page_pdf(job):
    """Return a genuine one-page PDF, or refuse rather than clip content."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    summary = job.get('summary') or {}
    provenance = job.get('provenance') or {}
    pair = provenance.get('source_pair') or {}
    job_id = _text(job.get('id'))
    generated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    messages = list(dict.fromkeys(str(x) for x in list(job.get('warnings') or []) + list(provenance.get('limitations') or [])))
    navy, teal = colors.HexColor('#102239'), colors.HexColor('#087f8c')
    light, grey = colors.HexColor('#eef4f7'), colors.HexColor('#4b5e73')

    for size in [9.5, 9.0, 8.5, 8.0]:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=38, rightMargin=38,
            topMargin=32, bottomMargin=36, title='TRINETRA one-page situation report',
            author='TRINETRA contributors')
        width = A4[0] - 76
        normal = ParagraphStyle('compact', fontName='Helvetica', fontSize=size,
            leading=size * 1.28, textColor=navy, spaceAfter=4)
        small = ParagraphStyle('small', parent=normal, fontSize=size - 1,
            leading=(size - 1) * 1.28, textColor=grey, spaceAfter=3)
        heading = ParagraphStyle('heading', parent=normal, fontName='Helvetica-Bold',
            fontSize=size + 1, textColor=teal, spaceBefore=6, spaceAfter=5)
        title = ParagraphStyle('title', parent=normal, fontName='Helvetica-Bold',
            fontSize=22, leading=25, spaceAfter=3)
        def para(value, style=normal):
            # Every service-provided value is plain text, never interpreted markup.
            return Paragraph(escape(_text(value)).replace('\n', '<br/>'), style)
        def rowtable(rows, widths):
            table = Table([[para(v) for v in row] for row in rows], colWidths=widths)
            table.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ROWBACKGROUNDS', (0, 0), (-1, -1), [light, colors.white]),
                ('LEFTPADDING', (0, 0), (-1, -1), 7),
                ('RIGHTPADDING', (0, 0), (-1, -1), 7),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            return table
        story = [para('TRINETRA', title), para('ONE-PAGE SITUATION REPORT | EDUCATIONAL PROTOTYPE', heading),
            para(f'Generated {generated} | Job {job_id}', small),
            para(f"Case: {_text(summary.get('case'))} | Event: {_text(job.get('flood_date'))} | Method: {_text(summary.get('method'))}"),
            para('Candidate evidence only. Unknown terrain is not dry land. No damage or safe route is confirmed.'),
            para('Satellite observations', heading),
            para(f"Before: {_text(pair.get('pre_datetime'))} | {_text(pair.get('pre_id'))}", small),
            para(f"After: {_text(pair.get('post_datetime'))} | {_text(pair.get('post_id'))}", small),
            para(f"Orbit: {_text(pair.get('relative_orbit'))} / {_text(pair.get('orbit_direction'))} | Output grid: {_number(summary.get('resolution_m'), 1)} m", small),
            para('Evidence and coverage', heading),
            rowtable([
                ['Candidate area (km2)', _number(summary.get('candidate_area_km2'), 4), 'Candidate pixels', _number(summary.get('candidate_pixels'))],
                ['Assessable by selected method', _fraction(summary.get('assessable_fraction')), 'Unknown / unsupported (km2)', _number(summary.get('unknown_area_km2'), 4)],
            ], [width * .28, width * .18, width * .33, width * .21]),
            para('Mapped infrastructure and conditional access', heading)]
        metrics = [
            ('Candidate-intersecting road features', 'damaged_road_segments'),
            ('Candidate-intersecting mapped bridges', 'affected_bridges'),
            ('Candidate-intersecting mapped buildings', 'affected_buildings'),
            ('Mapped settlements', 'total_settlements'),
            ('Potential road-access loss to mapped medical facilities', 'settlements_cut_off'),
            ('Settlements with unknown road access', 'settlements_access_unknown'),
        ]
        story.append(rowtable([(label, _number(summary.get(key))) for label, key in metrics], [width * .76, width * .24]))
        scenarios = summary.get('access_scenarios')
        if scenarios:
            story.append(para('Town / medical access scenarios', heading))
            if scenarios.get('status') != 'complete':
                story.append(para('Unknown / unavailable: ' + scenarios.get('reason', 'Required source unavailable'), small))
            else:
                rows = []
                for mode, label in [('car', 'Road'), ('foot', 'Foot')]:
                    result = scenarios[mode]
                    rows.append([label + ': conditionally cut off (unknown open to closed)',
                        f"{result['cut_off_lower']} to {result['cut_off_upper']} / {result['eligible']} assessed"])
                story.append(rowtable(rows, [width * .76, width * .24]))
                story.append(para(f"Fixed nearest pre-event reachable town/medical destination. Network buffer {scenarios.get('network_buffer_m')} m. Not assessed: road {scenarios['car']['not_assessed']}, foot {scenarios['foot']['not_assessed']}. Snap assumptions and baseline disconnections are detailed separately.", small))
        availability = summary.get('infrastructure_status') or {}
        story += [para('Data availability and verification priorities', heading),
            para(' | '.join(f'{name}: {_text(availability.get(name))}' for name in ['roads', 'buildings', 'settlements', 'hospitals']), small),
            para(f"OSM snapshot requested: {_text(summary.get('osm_snapshot'))} | Latest available: {_text((provenance.get('osm') or {}).get('latest_available'))}", small),
            para('Inspect candidate-intersecting infrastructure and routes through unknown terrain. Connectivity is conditional on the mapped network and medical destinations; zero detected access loss does not establish safe access.', small),
            para('Satellite revisits and processing latency prevent minute-ahead collapse warnings. Model scores are uncalibrated. Terrain screening can exclude genuine impacts. Debris, landing zones and optical confirmation are not established by this report.', small),
            para(f'{len(messages)} unique job warnings / processing limitations. Read every message in the detailed report and provenance.json before interpreting this summary.', small),
            para('Reproducibility and sources', heading)]
        for label, key in [('Processing', 'processing_manifest_sha256'), ('Detection', 'detection_manifest_sha256'), ('Checkpoint', 'checkpoint_sha256')]:
            story.append(para(f'{label} SHA256: {_text(provenance.get(key))}', small))
        story += [para('Full scene identifiers, output hashes, source records and all warnings: detailed report and results ZIP for this job.', small),
            para('Contains modified Copernicus Sentinel data 2026. Terrain: Copernicus DEM. Infrastructure / basemap: (c) OpenStreetMap contributors (ODbL), historical extracts via HeiGIT ohsome. AI training: Kuro Siwo, Bountos et al., NeurIPS 2024, DOI 10.52202/079017-1204, dataset CC BY 4.0. Original source credits and terms remain in ATTRIBUTION.md and the detailed report.', small)]
        def footer(canvas, unused):
            canvas.saveState();canvas.setStrokeColor(teal);canvas.line(38, 28, A4[0] - 38, 28)
            canvas.setFont('Helvetica', 7);canvas.setFillColor(grey)
            canvas.drawString(38, 17, 'TRINETRA | System-generated from a completed job | Field verification required')
            canvas.drawRightString(A4[0] - 38, 17, str(canvas.getPageNumber()))
            canvas.restoreState()
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        if doc.page == 1:
            return buffer.getvalue()
    raise ValueError('Job metadata is too long for the one-page format; use the detailed report. No content was clipped.')

