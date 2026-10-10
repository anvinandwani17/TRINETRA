"""Restore the real IPF identifier in a bounded SNAP intermediate, never raw SAFE."""
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET

from trinetra.downloads.sentinel1 import DownloadError, atomic_json, _sha256
from .io import bands_from_dim
from .snap import border_noise_policy


def processing_field(tree):
    roots = [n for n in tree.iter() if n.attrib.get('name') == 'Abstracted_Metadata']
    if len(roots) != 1:
        raise DownloadError('SNAP intermediate has missing or ambiguous Abstracted_Metadata')
    fields = [n for n in roots[0].iter() if n.attrib.get('name') == 'Processing_system_identifier']
    if len(fields) != 1:
        raise DownloadError('SNAP intermediate has missing or ambiguous processing identifier')
    return fields[0]


def restore_ipf_identifier(dim, raw):
    dim, raw = Path(dim).resolve(), Path(raw).resolve()
    if dim == raw or dim.suffix.lower() != '.dim':
        raise DownloadError('IPF correction is restricted to a local SNAP DIMAP intermediate')
    policy = border_noise_policy(raw)
    version = policy['ipf_version']
    tree = ET.parse(dim)
    field = processing_field(tree)
    old = (field.text or '').strip()
    expected = f'Sentinel-1 IPF {version}'
    # Work around only the observed COGifier field, or validate a real IPF field.
    if 'cogifier' not in old.lower():
        match = re.fullmatch(r'.*sentinel.*ipf\s+(\d+\.\d{1,2})', old, re.IGNORECASE)
        if not match or Decimal(match[1]) != Decimal(version):
            raise DownloadError(f'Unexpected SNAP processing identifier: {old}; refusing guessed correction')
    original_hash = _sha256(dim)
    # Preserve the original metadata byte-for-byte for audit. No raster or LUT edits.
    backup = dim.with_suffix('.reader_original.xml')
    backup.write_bytes(dim.read_bytes())
    field.text = expected
    temporary = dim.with_suffix('.dim.tmp')
    tree.write(temporary, encoding='utf-8', xml_declaration=True)
    temporary.replace(dim)
    if processing_field(ET.parse(dim)).text != expected:
        raise DownloadError('SNAP intermediate processing identifier correction failed')
    report = {'original_identifier': old, 'restored_identifier': expected,
              'ipf_version': version, 'source': str(raw), 'raw_sha256': _sha256(raw),
              'original_dim_sha256': original_hash, 'corrected_dim_sha256': _sha256(dim),
              'scope': 'Only abstracted processing identifier; raster samples, calibration/noise LUTs and original metadata preserved'}
    atomic_json(dim.with_suffix('.processing_fix.json'), report)
    return report


def assert_ipf_identifier(dim, version):
    value = (processing_field(ET.parse(dim)).text or '').strip()
    if value != f'Sentinel-1 IPF {version}':
        raise DownloadError(f'SNAP did not retain the restored IPF identifier: {value}')


def preparation_graph(raw, target, region):
    graph = ET.Element('graph', id='TRINETRA-Reader-Compatibility')
    ET.SubElement(graph, 'version').text = '1.0'
    previous = None
    for name, operator, params in [
        ('read', 'Read', {'file': str(Path(raw).resolve())}),
        ('subset', 'Subset', {'geoRegion': region, 'copyMetadata': True}),
        ('write', 'Write', {'file': str(Path(target).resolve()), 'formatName': 'BEAM-DIMAP'})]:
        node = ET.SubElement(graph, 'node', id=name)
        ET.SubElement(node, 'operator').text = operator
        sources = ET.SubElement(node, 'sources')
        if previous:
            ET.SubElement(sources, 'sourceProduct', refid=previous)
        parameters = ET.SubElement(node, 'parameters')
        for key, value in params.items():
            ET.SubElement(parameters, key).text = str(value).lower() if isinstance(value, bool) else str(value)
        previous = name
    ET.indent(graph)
    return ET.tostring(graph, encoding='unicode')


def prepare_source(gpt, raw, target, region, raw_hash, *, cache_mb, threads):
    target = Path(target)
    graph = preparation_graph(raw, target, region)
    version = border_noise_policy(raw)['ipf_version']
    signature = hashlib.sha256((graph + raw_hash + version + 'ipf-fix-v1').encode()).hexdigest()
    marker = target.with_suffix('.prepared.json')
    if marker.exists():
        record = json.loads(marker.read_text())
        if record.get('signature') == signature and record.get('files') and all(
                Path(p).is_file() and _sha256(p) == digest for p, digest in record['files'].items()):
            assert_ipf_identifier(target, version)
            print(f'SNAP reader-compatible subset cache reused: {target}', flush=True)
            return record['correction']
    target.parent.mkdir(parents=True, exist_ok=True)
    xml = target.with_suffix('.graph.xml')
    xml.write_text(graph, encoding='utf-8')
    log = target.with_suffix('.log')
    print(f'SNAP creating bounded raw subset before calibration; log: {log}', flush=True)
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run([gpt, str(xml), '-c', f'{cache_mb}M', '-q', str(threads)],
                                stdout=stream, stderr=subprocess.STDOUT, shell=False)
    if result.returncode:
        raise DownloadError(f'SNAP raw subset failed; inspect {log}')
    bands = bands_from_dim(target)
    if not any(n.endswith('_VV') for n in bands) or not any(n.endswith('_VH') for n in bands):
        raise DownloadError('Reader-compatible subset must retain both VV and VH')
    correction = restore_ipf_identifier(target, raw)
    files = [target, target.with_suffix('.processing_fix.json'), target.with_suffix('.reader_original.xml'), *bands.values()]
    files.extend(p.with_suffix('.hdr') for p in bands.values() if p.with_suffix('.hdr').exists())
    atomic_json(marker, {'signature': signature, 'correction': correction,
                         'files': {str(p): _sha256(p) for p in files}})
    print(f"SNAP identifier restored: {correction['original_identifier']} -> {correction['restored_identifier']}", flush=True)
    return correction
