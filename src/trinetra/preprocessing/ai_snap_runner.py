"""Standalone checked SNAP execution for the experimental AI branch."""
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
from trinetra.core.files import atomic_json, _sha256

class DownloadError(RuntimeError):
    """A SNAP execution or output validation failure."""

def bands_from_dim(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise DownloadError('SNAP did not create its DIMAP metadata')
    root = ET.parse(path).getroot()
    bands = {}
    for element in root.iter():
        if element.tag.split('}')[-1] == 'DATA_FILE_PATH':
            href = element.attrib.get('href')
            if not href:
                continue
            candidate = (path.parent / href).resolve()
            if candidate.suffix.lower() == chr(46)+chr(104)+chr(100)+chr(114):
                candidate = candidate.with_suffix(chr(46)+chr(105)+chr(109)+chr(103))
            if not candidate.is_relative_to(path.parent) or not candidate.is_file():
                raise DownloadError('DIMAP band path is missing or escapes the processing folder')
            bands[candidate.stem] = candidate
    if not bands:
        raise DownloadError('DIMAP has no physical raster bands')
    return bands


def run_graph(gpt, graph, output, signature, *, cache_mb=2048, threads=2):
    output = Path(output)
    marker = output.with_suffix('.verified.json')
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
