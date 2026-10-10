"""ESA SNAP graph construction and checked, cached GPT execution."""
from pathlib import Path
from decimal import Decimal, InvalidOperation
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile

from trinetra.downloads.sentinel1 import atomic_json, _sha256, DownloadError


def gpt_path(value=None):
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path.cwd()/'.env')
    value = value or os.getenv('SNAP_GPT') or shutil.which('gpt')
    if not value or not Path(value).is_file():
        raise DownloadError('Install ESA SNAP with Microwave Toolbox, then set SNAP_GPT to its bin/gpt.exe')
    return str(Path(value).resolve())


def border_noise_policy(source):
    """ESA MPC-0243: IPF >=2.90 already sets no-value GRD border pixels to zero."""
    try:
        with zipfile.ZipFile(source) as archive:
            manifests = [i for i in archive.infolist() if i.filename.endswith('.SAFE/manifest.safe')]
            if len(manifests) != 1 or manifests[0].file_size > 10_000_000:
                raise DownloadError('Missing, ambiguous, or oversized SAFE processing manifest')
            root = ET.fromstring(archive.read(manifests[0]))
    except (OSError,zipfile.BadZipFile,ET.ParseError):
        raise DownloadError('Cannot read Sentinel-1 processing version from SAFE manifest') from None
    versions = []
    for node in root.iter():
        if node.tag.split('}')[-1] == 'software':
            name = node.attrib.get('name','').lower()
            if 'sentinel' in name and 'ipf' in name:
                version = node.attrib.get('version','')
                if not re.fullmatch(r'\d+\.\d{1,2}',version):
                    raise DownloadError('Unrecognized Sentinel-1 IPF version; refusing automatic border-noise skip')
                try:
                    number = Decimal(version)
                except InvalidOperation:
                    raise DownloadError('Invalid Sentinel-1 IPF version') from None
                if number <= 0:
                    raise DownloadError('Invalid Sentinel-1 IPF version')
                versions.append((version,number))
    if not versions or len({n for _,n in versions}) != 1:
        raise DownloadError('Sentinel-1 IPF version is missing or conflicting; cannot choose border handling safely')
    required = versions[0][1] < Decimal('2.90')
    return {'ipf_version':versions[0][0], 'remove_grd_border_noise':required,
            'reason':'legacy IPF requires border denoising' if required else 'IPF >=2.90 supplies zero-valued no-data borders; preserve/mask zeros',
            'reference':'ESA Sentinel-1 MPC-0243 v2.2, section 4'}


def build_graph(source, output, region_wkt, crs, *, dem=None, dem_nodata=-9999, mask=False, border_noise_required=True, prepared_source=False):
    """Each branch retains SAR metadata until physical correction finishes."""
    graph = ET.Element('graph', id='TRINETRA-S1-20m')
    ET.SubElement(graph, 'version').text = '1.0'
    previous = None
    def node(name, operator, params):
        nonlocal previous
        element = ET.SubElement(graph, 'node', id=name)
        ET.SubElement(element, 'operator').text = operator
        sources = ET.SubElement(element, 'sources')
        if previous:
            ET.SubElement(sources, 'sourceProduct', refid=previous)
        parameters = ET.SubElement(element, 'parameters')
        for key, value in params.items():
            ET.SubElement(parameters, key).text = str(value).lower() if isinstance(value, bool) else str(value)
        previous = name
    terrain = {'demName': 'External DEM' if dem else 'Copernicus 30m Global DEM',
               'demResamplingMethod': 'BILINEAR_INTERPOLATION'}
    if dem:
        terrain.update(externalDEMFile=str(Path(dem).resolve()), externalDEMNoDataValue=dem_nodata,
                       externalDEMApplyEGM=False)
    node('read', 'Read', {'file': str(Path(source).resolve())})
    node('orbit', 'Apply-Orbit-File', {'orbitType': 'Sentinel Precise (Auto Download)', 'polyDegree': 3, 'continueOnFail': False})
    if border_noise_required:
        node('border', 'Remove-GRD-Border-Noise', {'selectedPolarisations': 'VV,VH'})
    node('noise', 'ThermalNoiseRemoval', {'selectedPolarisations': 'VV,VH', 'removeThermalNoise': True})
    # Buffered subset retains context for terrain geometry; final output is exact AOI.
    if not prepared_source:
        node('subset', 'Subset', {'geoRegion': region_wkt, 'copyMetadata': True})
    node('calibrate', 'Calibration', {'selectedPolarisations': 'VV,VH', 'outputSigmaBand': False,
                                      'outputBetaBand': True, 'outputGammaBand': False, 'outputImageScaleInDb': False})
    node('multilook', 'Multilook', {'sourceBands': 'Beta0_VV,Beta0_VH', 'nRgLooks': 2, 'nAzLooks': 2,
                                  'outputIntensity': True, 'grSquarePixel': True})
    if mask:
        node('simulation', 'SAR-Simulation', {**terrain, 'sourceBands': 'Beta0_VV', 'saveLayoverShadowMask': True})
        bands = 'layover_shadow_mask'
    else:
        node('flatten', 'Terrain-Flattening', {**terrain, 'sourceBands': 'Beta0_VV,Beta0_VH', 'oversamplingMultiple': 1.0})
        bands = 'Gamma0_VV,Gamma0_VH'
    node('geocode', 'Terrain-Correction', {**terrain, 'sourceBands': bands,
         'imgResamplingMethod': 'NEAREST_NEIGHBOUR' if mask else 'BILINEAR_INTERPOLATION',
         'pixelSpacingInMeter': 20.0, 'mapProjection': crs,
         'saveSelectedSourceBand': True, 'saveDEM': True, 'saveLocalIncidenceAngle': True,
         'nodataValueAtSea': False, 'applyRadiometricNormalization': False})
    node('write', 'Write', {'file': str(Path(output).resolve()), 'formatName': 'BEAM-DIMAP'})
    ET.indent(graph)
    return ET.tostring(graph, encoding='unicode')


def run_graph(gpt, graph, output, signature, *, cache_mb=2048, threads=2):
    from .io import bands_from_dim
    output = Path(output)
    marker = output.with_suffix('.verified.json')
    # Apply the quality gate to cached runs too; older markers must not bypass it.
    if output.with_suffix('.log').exists():
        check_calibration_log(output)
    if marker.exists():
        import json
        record = json.loads(marker.read_text())
        if record.get('signature') == signature and all(Path(p).is_file() and _sha256(p) == digest for p, digest in record.get('files', {}).items()) and record.get('files'):
            print(f'SNAP cache reused: {output.name}', flush=True)
            return {name:Path(path) for name,path in record['bands'].items()} if record.get('bands') else bands_from_dim(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    graph_path = output.with_suffix('.graph.xml')
    graph_path.write_text(graph, encoding='utf-8')
    log = output.with_suffix('.log')
    print(f'SNAP processing: {output.name}; progress log: {log}', flush=True)
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run([gpt, str(graph_path), '-c', f'{cache_mb}M', '-q', str(threads)],
                                stdout=stream, stderr=subprocess.STDOUT, shell=False)
    if result.returncode:
        raise DownloadError(f'SNAP failed ({result.returncode}); inspect {log}. No successful processing claimed.')
    bands = bands_from_dim(output)
    check_calibration_log(output)
    original_bands = list(bands.values())
    import rasterio
    for name,path in list(bands.items()):
        with rasterio.open(path) as src:
            needs_export = src.crs is None
        if needs_export:
            # Some DIMAP ENVI headers omit map metadata. SNAP reads geocoding
            # from .dim; ask it to export the band rather than inventing a CRS.
            exported = output.parent/(output.stem+'_geotiff')/(name+'.tif')
            exported.parent.mkdir(exist_ok=True)
            export_graph = single_band_export_graph(output,exported,name)
            export_xml = exported.with_suffix('.graph.xml')
            export_xml.write_text(export_graph,encoding='utf-8')
            with log.open('a',encoding='utf-8') as stream:
                result = subprocess.run([gpt,str(export_xml),'-c',f'{cache_mb}M','-q',str(threads)],
                                        stdout=stream,stderr=subprocess.STDOUT,shell=False)
            if result.returncode:
                raise DownloadError(f'SNAP georeferenced band export failed; inspect {log}')
            bands[name] = exported
            path = exported
        with rasterio.open(path) as src:
            if not src.crs or src.width <= 0 or src.height <= 0 or src.count != 1:
                raise DownloadError('SNAP output lacks a geographic grid')
            src.read(1, window=rasterio.windows.Window(0, 0, min(16, src.width), min(16, src.height)))
    files = [output, *original_bands, *bands.values()]
    files.extend(p.with_suffix('.hdr') for p in original_bands if p.with_suffix('.hdr').exists())
    atomic_json(marker, {'signature': signature, 'files': {str(p): _sha256(p) for p in files},
                         'bands':{name:str(path) for name,path in bands.items()}})
    return bands


def snap_processing_metadata(output):
    """Small diagnostic: processing identifiers only, never scene pixels or secrets."""
    root = ET.parse(output).getroot()
    result = []
    for node in root.iter():
        name = node.attrib.get('name', '')
        if name.lower() in {'processing_system_identifier', 'processingsystemidentifier',
                            'processing_system', 'software', 'version'}:
            value = ''.join(node.itertext()).strip()
            if value and len(value) <= 500:
                result.append({'name': name, 'value': value})
    return result


def check_calibration_log(output):
    """Never certify radiometry when SNAP explicitly questions its calibration LUT."""
    output = Path(output)
    log = output.with_suffix('.log')
    if not log.exists():
        return
    content = log.read_text(encoding='utf-8', errors='replace').lower()
    if 'calibration lut for this product could be incorrect' in content:
        diagnostic = output.with_suffix('.calibration_diagnostic.json')
        identifiers = snap_processing_metadata(output) if output.exists() else []
        atomic_json(diagnostic, {'status': 'calibration_unverified',
                                'snap_processing_identifiers': identifiers,
                                'log': str(log),
                                'reason': 'SNAP reported a potentially unreliable calibration LUT'})
        raise DownloadError(f'SNAP calibration needs investigation; no validated radiometry claimed. '
                            f'Processing-version diagnostic: {diagnostic}')


def single_band_export_graph(source, target, band):
    """Export physical terrain-corrected geocoding through SNAP, not an assumed affine."""
    graph = ET.Element('graph',id='TRINETRA-Georeferenced-Export')
    ET.SubElement(graph,'version').text='1.0'
    previous = None
    for name,operator,params in [
        ('read','Read',{'file':str(Path(source).resolve())}),
        ('select','Subset',{'sourceBands':band,'copyMetadata':True}),
        ('write','Write',{'file':str(Path(target).resolve()),'formatName':'GeoTIFF'})]:
        node = ET.SubElement(graph,'node',id=name)
        ET.SubElement(node,'operator').text=operator
        sources=ET.SubElement(node,'sources')
        if previous:
            ET.SubElement(sources,'sourceProduct',refid=previous)
        parameters=ET.SubElement(node,'parameters')
        for key,value in params.items():
            ET.SubElement(parameters,key).text=str(value).lower() if isinstance(value,bool) else str(value)
        previous=name
    ET.indent(graph)
    return ET.tostring(graph,encoding='unicode')
