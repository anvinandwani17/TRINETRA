import { useEffect, useRef, useState } from 'react';
import maplibregl, { GeoJSONSource } from 'maplibre-gl';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';
import './integration.css';

// MapLibre control wrappers enable pointer events on maplibregl-ctrl.
Object.assign(MapboxDraw.constants.classes, {
  CANVAS: 'maplibregl-canvas',
  CONTROL_BASE: 'maplibregl-ctrl',
  CONTROL_PREFIX: 'maplibregl-ctrl-',
  CONTROL_GROUP: 'maplibregl-ctrl-group',
  ATTRIBUTION: 'maplibregl-ctrl-attrib',
});

const API = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
type AOI = { type: 'Polygon'; coordinates: number[][][] };
type Layer = { type: 'FeatureCollection'; features: any[] };
type Job = { id: string; state: string; message: string; summary: Record<string, any>; warnings: string[]; provenance: Record<string, any>; stage_history?: {stage:string; status:string; elapsed_seconds?:number}[]; selected_pair?:Record<string,any>; estimated_download_bytes?:number };
type SavedCase = {case:string; label:string; aoi:AOI; flood_date:string; methods:string[]};
const definitions = [
  { key: 'unobservable', label: 'Unknown / unsupported terrain', color: '#94a3b8', kind: 'fill' },
  { key: 'flood_extent', label: 'Candidate flood evidence', color: '#38bdf8', kind: 'fill' },
  { key: 'affected_buildings', label: 'Potentially affected buildings', color: '#fbbf24', kind: 'fill' },
  { key: 'unknown_buildings', label: 'Buildings in unknown terrain', color: '#d1d5db', kind: 'fill' },
  { key: 'unknown_roads', label: 'Roads with unknown exposure', color: '#cbd5e1', kind: 'line' },
  { key: 'damaged_roads', label: 'Potentially affected roads', color: '#f97316', kind: 'line' },
  { key: 'affected_bridges', label: 'Potentially affected mapped bridges', color: '#e879f9', kind: 'line' },
  { key: 'settlements', label: 'Mapped settlements / conditional access', color: '#facc15', kind: 'circle' },
] as const;

async function request(path: string, options?: RequestInit) {
  const response = await fetch(API + path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
  return data;
}

export default function App() {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const draw = useRef<MapboxDraw | null>(null);
  const poll = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollingVersion = useRef(0);
  const [ready, setReady] = useState(false);
  const [aoi, setAoi] = useState<AOI | null>(null);
  const [date, setDate] = useState('');
  const [inputMode, setInputMode] = useState<'custom' | 'saved'>('custom');
  const [method, setMethod] = useState('baseline');
  const [status, setStatus] = useState('READY');
  const [message, setMessage] = useState('Draw a new area on the map and choose an event date (maximum 300 km²).');
  const [cases, setCases] = useState<SavedCase[]>([]);
  const [selectedCase, setSelectedCase] = useState('');
  const [allowDownloads, setAllowDownloads] = useState(false);
  const [transport, setTransport] = useState('s3');
  const [tracking, setTracking] = useState<Job | null>(null);
  const [previews, setPreviews] = useState<Record<string,string>>({});
  const [job, setJob] = useState<Job | null>(null);
  const [layers, setLayers] = useState<Record<string, Layer>>({});
  const [visible, setVisible] = useState<Record<string, boolean>>(Object.fromEntries(definitions.map(x => [x.key, true])));
  const busy = !['READY', 'ERROR', 'FAILED', 'COMPLETE'].includes(status);


  function chooseInputMode(mode: 'custom' | 'saved') {
    resetDisplay(); setInputMode(mode); setAoi(null); setDate(''); setMethod('baseline'); setAllowDownloads(false);
    draw.current?.deleteAll();
    setMessage(mode === 'custom' ? 'Draw a polygon, choose an event date, then run baseline analysis.' : 'Select a saved case and load its area and date.');
  }

  function startDrawing() {
    if (!draw.current || !ready || busy) return;
    resetDisplay(); setInputMode('custom'); setSelectedCase(''); setMethod('baseline'); setAoi(null);
    draw.current.deleteAll();
    draw.current.changeMode('draw_polygon');
    setMessage('Drawing: click at least three corners, then click the first corner to finish. Press Escape to cancel.');
  }

  function resetDisplay() {
    pollingVersion.current++;
    if (poll.current) clearTimeout(poll.current);
    setJob(null); setTracking(null); setLayers({}); setPreviews({}); setStatus('READY');
  }

  useEffect(() => {
    if (!container.current) return;
    const instance = new maplibregl.Map({ container: container.current,
      style: { version: 8, sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256,
        attribution: '© OpenStreetMap contributors' } }, layers: [{ id: 'osm', type: 'raster', source: 'osm' }] },
      center: [85.15, 27.95], zoom: 11 });
    map.current = instance;
    instance.addControl(new maplibregl.NavigationControl(), 'top-right');
    const control = new MapboxDraw({ displayControlsDefault: false, controls: { polygon: true, trash: true } });
    draw.current = control;
    instance.addControl(control as unknown as maplibregl.IControl, 'top-left');
    const capture = (event: any) => {
      const feature = event.features?.[0];
      if (feature?.geometry.type === 'Polygon') {
        resetDisplay();
        setInputMode('custom'); setSelectedCase(''); setMethod('baseline');
        setAoi(feature.geometry); setMessage('Area selected. Baseline supports local historical cases; missing scenes require the download option.');
      }
    };
    instance.on('draw.create', capture); instance.on('draw.update', capture);
    instance.on('draw.delete', () => { resetDisplay(); setAoi(null); });
    instance.on('load', () => {
      definitions.forEach(definition => {
        instance.addSource(definition.key, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        const paint: any = definition.kind === 'fill' ? { 'fill-color': definition.color, 'fill-opacity': definition.key === 'unobservable' ? 0.28 : 0.65 }
          : definition.kind === 'line' ? { 'line-color': definition.color, 'line-width': 3 }
          : { 'circle-radius': 6, 'circle-stroke-color': '#0f172a', 'circle-stroke-width': 1,
              'circle-color': ['match', ['get', 'road_status'], 'potential_cut_off', '#f97316', 'unknown', '#94a3b8', '#facc15'] };
        instance.addLayer({ id: definition.key + '-layer', type: definition.kind, source: definition.key, paint } as any);
        instance.on('click', definition.key + '-layer', event => {
          const props = event.features?.[0]?.properties || {};
          const content = document.createElement('div');
          content.style.color = '#0f172a'; content.style.maxWidth = '250px';
          content.textContent = [definition.label, props.name || props.osm_id || '',
            props.road_status || props.status || props.classification || '',
            props.foot_status ? 'Foot access: ' + props.foot_status : '',
            'Candidate evidence; field verification required.'].filter(Boolean).join(' · ');
          new maplibregl.Popup().setLngLat(event.lngLat).setDOMContent(content).addTo(instance);
        });
      });
      setReady(true);
    });
    instance.on('error', event => setMessage('Map resource error: ' + event.error.message));
    return () => { pollingVersion.current++; if (poll.current) clearTimeout(poll.current); setReady(false); instance.remove(); map.current = null; draw.current = null; };
  }, []);

  useEffect(() => {
    if (!ready || !map.current) return;
    definitions.forEach(({ key }) => {
      (map.current!.getSource(key) as GeoJSONSource).setData((layers[key] || { type: 'FeatureCollection', features: [] }) as any);
      map.current!.setLayoutProperty(key + '-layer', 'visibility', visible[key] ? 'visible' : 'none');
    });
  }, [layers, visible, ready]);

  useEffect(() => {
    if (!ready || !map.current) return;
    // Keep the selected request polygon fixed while its job is running.
    map.current.getCanvas().style.pointerEvents = busy ? 'none' : '';
    map.current.getContainer().querySelectorAll<HTMLButtonElement>('button.mapbox-gl-draw_ctrl-draw-btn')
      .forEach(button => { button.disabled = busy; });
  }, [busy, ready]);

  useEffect(() => {
    let active = true;
    request('/api/v1/cases').then(data => { if (active) setCases(data.cases); })
      .catch(error => { if (active) setMessage('Saved-case list unavailable: ' + String(error)); });
    return () => { active = false; };
  }, []);

  async function loadCase() {
    try {
      const selected = cases.find(item => item.case === selectedCase);
      if (!selected) throw new Error('Choose an available saved case.');
      resetDisplay();
      setInputMode('saved');
      setAoi(selected.aoi); setDate(selected.flood_date);
      if (!selected.methods.includes(method)) setMethod('baseline');
      setAllowDownloads(false);
      draw.current?.deleteAll();
      draw.current?.add({ type: 'Feature', properties: {}, geometry: selected.aoi });
      const points = selected.aoi.coordinates.flat() as number[][];
      const bounds = new maplibregl.LngLatBounds(); points.forEach(point => bounds.extend(point as [number, number]));
      map.current?.fitBounds(bounds, { padding: 40 });
      setMessage('Saved area loaded. Run analysis to verify its files and query pre-event infrastructure.');
    } catch (error) { setStatus('ERROR'); setMessage(String(error)); }
  }

  async function run() {
    if (!aoi || !date) return;
    const version = ++pollingVersion.current;
    if (poll.current) clearTimeout(poll.current);
    setJob(null); setTracking(null); setPreviews({}); setLayers({}); setStatus('PROCESSING'); setMessage('Validating area, date and satellite inputs...');
    try {
      const created = await request(inputMode === 'saved' && selectedCase === 'trishuli' && method === 'experimental_ai' ? '/api/v1/jobs' : '/api/v1/pipeline-jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aoi, flood_date: date, config: method,
          allow_downloads:allowDownloads, transport, max_product_gib:3.0 }) });
      setTracking(created);
      const started = Date.now();
      const check = async () => {
        if (version !== pollingVersion.current) return;
        try {
          const current: Job = await request('/api/v1/jobs/' + created.id);
          if (version !== pollingVersion.current) return;
          setStatus(current.state); setMessage(current.message);
          setTracking(current);
          if (current.state === 'COMPLETE') {
            const output = await request('/api/v1/jobs/' + current.id + '/layers');
            if (version !== pollingVersion.current) return;
            setJob(current); setLayers(output);
            const imageList = await request('/api/v1/cases/' + current.summary.case + '/previews?method=' + encodeURIComponent(current.summary.method));
            if (version === pollingVersion.current) setPreviews(imageList.previews);
          } else if (current.state !== 'FAILED') {
            if (Date.now() - started > 24 * 60 * 60 * 1000) { setStatus('ERROR'); setMessage('Polling stopped after 24 hours. Check job status; processing may still be running.'); }
            else poll.current = setTimeout(check, 1500);
          }
        } catch (error) { setStatus('ERROR'); setMessage(String(error)); }
      };
      await check();
    } catch (error) { setStatus('ERROR'); setMessage(String(error)); }
  }

  const count = (key: string) => job?.summary[key] ?? 'Unknown / unavailable';
  return <div className="trinetra-app">
    <header><div><div className="eyebrow">Sentinel-1 · terrain · mapped infrastructure</div><h1>TRINETRA</h1><p>Candidate flood evidence and conditional access assessment</p></div><strong>{status}</strong></header>
    <div className="trinetra-body"><aside>
      <div className="input-mode" role="group" aria-label="Analysis input">
        <button type="button" aria-pressed={inputMode === 'custom'} onClick={() => chooseInputMode('custom')} disabled={busy}>New area / date</button>
        <button type="button" aria-pressed={inputMode === 'saved'} onClick={() => chooseInputMode('saved')} disabled={busy}>Saved cases</button>
      </div>
      {inputMode === 'saved' ? <>
        <label>Saved case<select value={selectedCase} onChange={e => { resetDisplay(); setSelectedCase(e.target.value); setAoi(null); setDate(''); setMethod('baseline'); draw.current?.deleteAll(); }} disabled={busy}>
          <option value="">Choose a saved case</option>
          {cases.map(item => <option key={item.case} value={item.case}>{item.label}</option>)}</select></label>
        <button onClick={loadCase} disabled={!ready || busy || !selectedCase}>Load selected saved area</button>
      </> : <p className="notice">Draw your area using the polygon tool at the map's top-left. Choose a historical event date. New analysis needs compatible before/after scenes; it can take several minutes. Saved cases are optional shortcuts.</p>}
      {inputMode === 'custom' && <button type="button" onClick={startDrawing} disabled={!ready || busy}>Draw area on map</button>}
      <label>Flood date (UTC day)<input type="date" value={date} max={new Date().toISOString().slice(0,10)} onChange={e => { resetDisplay(); setDate(e.target.value); if (inputMode === 'saved') { setInputMode('custom'); setSelectedCase(''); setMethod('baseline'); } }} disabled={busy} /></label>
      <label>Detection method<select value={method} onChange={e => { resetDisplay(); setMethod(e.target.value); }} disabled={busy}>
        <option value="baseline">Conservative deterministic baseline</option><option value="experimental_ai">Experimental AI — frozen pilot</option><option value="baseline_hand">Experimental baseline + HAND</option><option value="experimental_ai_hand">Experimental AI + HAND</option></select></label>
      {<><label className="layer-choice"><input type="checkbox" checked={allowDownloads} onChange={e => setAllowDownloads(e.target.checked)} disabled={busy} />Allow missing scene downloads (two scenes, up to 3 GiB each)</label>
        {allowDownloads && <label>Official CDSE transport<select value={transport} onChange={e => setTransport(e.target.value)} disabled={busy}><option value="s3">S3</option><option value="odata">OData</option></select></label>}</>}
      <button onClick={run} disabled={!aoi || !date || busy || !ready}>Run integrated analysis</button>
      <p role="status">{message}</p>
      <p className="notice">{method.endsWith('_hand') ? 'Experimental HAND uses provisional drainage/height thresholds. Rejected terrain is unknown. This can greatly reduce assessable coverage; fewer candidates do not establish better accuracy.' : method === 'experimental_ai' ? 'Experimental AI can process a bounded new area: physical preprocessing → separate linear Sigma0 → frozen CPU inference → infrastructure/report. Extra SNAP time is required. Accuracy and model scores are unverified; this is not the conservative baseline.' : <>Baseline: saved results are verified and reused. New local cases run discovery → download/cache → SNAP → detection → historical OSM → access → report. SNAP and local credentials are needed for uncached scenes. No suitable pair means no result.</>}</p>
      {tracking && <section className="pipeline-progress"><h2>Job stages</h2><a href={API + '/api/v1/jobs/' + tracking.id} target="_blank" rel="noreferrer">Job {tracking.id} status</a>
        <ol>{tracking.stage_history?.map((item,index) => <li key={index}>{item.stage}: {item.status}{item.elapsed_seconds !== undefined ? ' · ' + item.elapsed_seconds.toFixed(1) + ' s' : ''}</li>)}</ol>
        {tracking.estimated_download_bytes != null && <p>Catalogue estimate: {(tracking.estimated_download_bytes / 1024**3).toFixed(3)} GiB; cached products are reused.</p>}
        {tracking.selected_pair && <details><summary>Automatically selected Sentinel-1 pair</summary><p>Before: {tracking.selected_pair.pre_id}</p><p>After: {tracking.selected_pair.post_id}</p><p>Relative orbit: {tracking.selected_pair.relative_orbit} · {tracking.selected_pair.orbit_direction}</p></details>}
      </section>}
      {job && <><h2>Evidence summary</h2><dl>
        <dt>Candidate flood area</dt><dd>{job.summary.candidate_area_km2.toFixed(4)} km²</dd>
        <dt>Assessable by selected method</dt><dd>{(job.summary.assessable_fraction * 100).toFixed(1)}%</dd>
        <dt>Unknown / unsupported area</dt><dd>{job.summary.unknown_area_km2.toFixed(4)} km²</dd>
        <dt>Potentially affected mapped roads</dt><dd>{count('damaged_road_segments')}</dd>
        <dt>Potentially affected mapped buildings</dt><dd>{count('affected_buildings')}</dd>
        <dt>Potentially affected mapped bridges</dt><dd>{count('affected_bridges')}</dd>
        <dt>Potential road-access loss</dt><dd>{count('settlements_cut_off')}</dd>
        <dt>Settlements with unknown access</dt><dd>{count('settlements_access_unknown')}</dd>
        <dt>OSM snapshot</dt><dd>{job.summary.osm_snapshot || 'Unavailable'}</dd></dl>
        <p className="notice">Intersections are candidate impacts. Unknown terrain is not dry. No road, walking route or landing zone is certified safe.</p>
        {job.summary.method === 'experimental_ai' && <p className="notice">Pilot AI results are experimental. Accuracy in the requested area and flood probabilities are unverified.</p>}
        <h2>Map layers</h2>{definitions.map(({ key, label, color }) => <label className="layer-choice" key={key}>
          <input type="checkbox" checked={visible[key]} onChange={e => setVisible({ ...visible, [key]: e.target.checked })} />
          <span style={{ color }}>●</span> {label}</label>)}
        <h2>Data availability</h2>{Object.entries(job.summary.infrastructure_status).map(([name, state]) => <p key={name}>{name}: {String(state)}</p>)}
        <div className="exports"><a href={API + '/api/v1/jobs/' + job.id + '/report'} target="_blank" rel="noreferrer">Situation report / PDF</a>
          <a href={API + '/api/v1/jobs/' + job.id + '/export'}>Download results ZIP</a></div>
        <details><summary>Sources and limitations</summary><p>Sentinel-1 before: {job.provenance.source_pair.pre_datetime}</p><p>After: {job.provenance.source_pair.post_datetime}</p>
          <ul>{job.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details>
        {Object.keys(previews).length > 0 && <><h2>Satellite previews</h2>{Object.entries(previews).map(([name,url]) => <a className="satellite-preview" key={name} href={API + url} target="_blank" rel="noreferrer"><span>{name === 'experimental_ai' ? 'Experimental AI: uncalibrated score and candidate mask' : name === 'baseline' ? 'Deterministic baseline candidate mask' : name === 'change' ? 'Backscatter change — not flood classification' : name}</span><img src={API + url} alt={name + ' satellite preview'} /></a>)}</>}
      </>}
      <footer className="source-credits">Copernicus Sentinel / Copernicus DEM · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors (ODbL)</a> · HeiGIT ohsome. AI training: Kuro Siwo (Bountos et al., 2024). CEMS EMSR927: separate landslide context only. Current basemap differs from the historical analysis snapshot.</footer>
    </aside><main ref={container} aria-label="Interactive flood and infrastructure map" /></div>
  </div>;
}
