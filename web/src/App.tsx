import { useEffect, useRef, useState } from 'react';
import maplibregl, { GeoJSONSource } from 'maplibre-gl';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';
import './integration.css';

const API = 'http://127.0.0.1:8000';
type AOI = { type: 'Polygon'; coordinates: number[][][] };
type Layer = { type: 'FeatureCollection'; features: any[] };
type Job = { id: string; state: string; message: string; summary: Record<string, any>; warnings: string[]; provenance: Record<string, any> };
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
  const [date, setDate] = useState('2026-08-26');
  const [method, setMethod] = useState('baseline');
  const [status, setStatus] = useState('READY');
  const [message, setMessage] = useState('Load the processed Trishuli area to begin.');
  const [job, setJob] = useState<Job | null>(null);
  const [layers, setLayers] = useState<Record<string, Layer>>({});
  const [visible, setVisible] = useState<Record<string, boolean>>(Object.fromEntries(definitions.map(x => [x.key, true])));

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
        setAoi(feature.geometry); setMessage('Area selected. Only polygons inside the processed Trishuli area are supported.');
      }
    };
    instance.on('draw.create', capture); instance.on('draw.update', capture);
    instance.on('draw.delete', () => setAoi(null));
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

  async function loadCase() {
    try {
      const selected = await request('/api/v1/cases/trishuli');
      setAoi(selected.aoi); setDate(selected.flood_date);
      draw.current?.deleteAll();
      draw.current?.add({ type: 'Feature', properties: {}, geometry: selected.aoi });
      const points = selected.aoi.coordinates.flat() as number[][];
      const bounds = new maplibregl.LngLatBounds(); points.forEach(point => bounds.extend(point as [number, number]));
      map.current?.fitBounds(bounds, { padding: 40 });
      setMessage('Processed Trishuli area loaded. Choose a method and run analysis.');
    } catch (error) { setStatus('ERROR'); setMessage(String(error)); }
  }

  async function run() {
    if (!aoi) return;
    const version = ++pollingVersion.current;
    if (poll.current) clearTimeout(poll.current);
    setJob(null); setLayers({}); setStatus('PROCESSING'); setMessage('Validating satellite inputs...');
    try {
      const created = await request('/api/v1/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aoi, flood_date: date, config: method }) });
      const started = Date.now();
      const check = async () => {
        if (version !== pollingVersion.current) return;
        try {
          const current: Job = await request('/api/v1/jobs/' + created.id);
          if (version !== pollingVersion.current) return;
          setStatus(current.state); setMessage(current.message);
          if (current.state === 'COMPLETE') {
            const output = await request('/api/v1/jobs/' + current.id + '/layers');
            if (version !== pollingVersion.current) return;
            setJob(current); setLayers(output);
          } else if (current.state !== 'FAILED') {
            if (Date.now() - started > 6 * 60 * 1000) { setStatus('ERROR'); setMessage('Polling timed out. Check the backend terminal; the job may still be running.'); }
            else poll.current = setTimeout(check, 1500);
          }
        } catch (error) { setStatus('ERROR'); setMessage(String(error)); }
      };
      await check();
    } catch (error) { setStatus('ERROR'); setMessage(String(error)); }
  }

  const busy = !['READY', 'ERROR', 'FAILED', 'COMPLETE'].includes(status);
  const count = (key: string) => job?.summary[key] ?? 'Unknown / unavailable';
  return <div className="trinetra-app">
    <header><div><div className="eyebrow">Sentinel-1 · terrain · mapped infrastructure</div><h1>TRINETRA</h1><p>Candidate flood evidence and conditional access assessment</p></div><strong>{status}</strong></header>
    <div className="trinetra-body"><aside>
      <button onClick={loadCase} disabled={!ready || busy}>Load Trishuli test area</button>
      <label>Flood date<input type="date" value={date} onChange={e => setDate(e.target.value)} disabled={busy} /></label>
      <label>Detection method<select value={method} onChange={e => setMethod(e.target.value)} disabled={busy}>
        <option value="baseline">Conservative deterministic baseline</option><option value="experimental_ai">Experimental Siamese U-Net pilot</option></select></label>
      <button onClick={run} disabled={!aoi || busy || !ready}>Run integrated analysis</button>
      <p role="status">{message}</p>
      <p className="notice">This integration uses the existing processed Trishuli case. Other dates or areas need satellite processing first.</p>
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
        {job.summary.method === 'experimental_ai' && <p className="notice">Pilot AI results are experimental. Trishuli accuracy and flood probabilities are unverified.</p>}
        <h2>Map layers</h2>{definitions.map(({ key, label, color }) => <label className="layer-choice" key={key}>
          <input type="checkbox" checked={visible[key]} onChange={e => setVisible({ ...visible, [key]: e.target.checked })} />
          <span style={{ color }}>●</span> {label}</label>)}
        <h2>Data availability</h2>{Object.entries(job.summary.infrastructure_status).map(([name, state]) => <p key={name}>{name}: {String(state)}</p>)}
        <div className="exports"><a href={API + '/api/v1/jobs/' + job.id + '/report'} target="_blank" rel="noreferrer">Situation report / PDF</a>
          <a href={API + '/api/v1/jobs/' + job.id + '/export'}>Download results ZIP</a></div>
        <details><summary>Sources and limitations</summary><p>Sentinel-1 before: {job.provenance.source_pair.pre_datetime}</p><p>After: {job.provenance.source_pair.post_datetime}</p>
          <ul>{job.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details></>}
    </aside><main ref={container} aria-label="Interactive flood and infrastructure map" /></div>
  </div>;
}
