"""Conditional connectivity using candidate hazards and explicit unknown edges."""
import networkx as nx
import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely.geometry import LineString, mapping
from shapely.ops import transform

CARS = {'motorway', 'motorway_link', 'trunk', 'trunk_link', 'primary', 'primary_link', 'secondary', 'secondary_link', 'tertiary', 'tertiary_link', 'unclassified', 'residential', 'living_street', 'service', 'road'}


def collection(features):
    return {'type': 'FeatureCollection', 'features': features}


def feature(geometry, properties):
    return {'type': 'Feature', 'geometry': mapping(geometry), 'properties': properties}


def build_graph(roads, walking=False):
    graph = nx.Graph()
    if roads.empty:
        return graph
    project = Transformer.from_crs(4326, roads.estimate_utm_crs(), always_xy=True).transform
    for index, row in roads.iterrows():
        highway = str(row.get('highway', ''))
        foot = str(row.get('foot', ''))
        if (walking and (highway in {'motorway', 'motorway_link'} or foot in {'no', 'private'})) or (not walking and highway not in CARS):
            continue
        if str(row.get('access', '')) in {'no', 'private'} or (not walking and str(row.get('motor_vehicle', '')) in {'no', 'private'}):
            continue
        geometry = row.geometry
        if geometry.geom_type not in {'LineString', 'MultiLineString'}:
            continue
        lines = [geometry] if geometry.geom_type == 'LineString' else geometry.geoms
        for part, line in enumerate(lines):
            coords = list(line.coords)
            for segment, (a, b) in enumerate(zip(coords, coords[1:])):
                u, v = tuple(a[:2]), tuple(b[:2])
                if u == v:
                    continue
                edge = LineString([u, v])
                props = {'edge_id': f'{row.get("osm_id", index)}:{part}:{segment}', 'osm_id': str(row.get('osm_id', index)),
                         'name': None if not isinstance(row.get('name'), str) else row.get('name'),
                         'highway': highway, 'bridge': str(row.get('bridge', 'no')),
                         'geometry': edge, 'length_m': transform(project, edge).length, 'failed': False}
                # Identical endpoint pairs need only one geometric connection;
                # retain the alternate source ID for provenance, not new topology.
                if graph.has_edge(u, v):
                    graph[u][v].setdefault('alternate_osm_ids', []).append(props['osm_id'])
                else:
                    graph.add_edge(u, v, **props)
    return graph


def snap(points, graph, max_distance_m=500):
    if points.empty or not graph:
        return {}
    project = Transformer.from_crs(4326, points.estimate_utm_crs(), always_xy=True).transform
    nodes = list(graph.nodes)
    tree = cKDTree(np.array([project(*node) for node in nodes]))
    result = {}
    for _, row in points.iterrows():
        point = row.geometry.centroid
        distance, idx = tree.query(project(point.x, point.y))
        if distance <= max_distance_m:
            result[str(row['osm_id'])] = nodes[idx]
    return result


def screen(graph, hazard, unknown):
    result = graph.copy()
    for _, _, data in result.edges(data=True):
        data['candidate_exposure'] = not hazard.is_empty and data['geometry'].intersects(hazard)
        data['unknown'] = not unknown.is_empty and data['geometry'].intersects(unknown)
        data['failed'] = data['candidate_exposure']  # conditional closure scenario only
    return result


def active(graph, exclude_unknown=False):
    result = graph.copy()
    result.remove_edges_from([(u, v) for u, v, d in result.edges(data=True) if d.get('failed') or (exclude_unknown and d.get('unknown'))])
    return result


def connectivity(graph, screened, source, targets):
    targets = [node for node in targets if node in graph]
    if source not in graph or not targets:
        return 'unknown', False
    connected = lambda g: any(nx.has_path(g, source, target) for target in targets)
    if not connected(graph):
        return 'baseline_disconnected', False
    if not connected(active(screened)):
        return 'potential_cut_off', True
    if not connected(active(screened, True)):
        return 'unknown', True
    return 'no_candidate_route_impact', True


def analyze(roads, buildings, settlements, hospitals, hazard_frame, unknown_frame, aoi):
    hazard = hazard_frame.geometry.union_all() if not hazard_frame.empty else LineString()
    unsupported = unknown_frame.geometry.union_all() if not unknown_frame.empty else LineString()
    # OSM roads can extend beyond the query boundary. Such portions have no
    # satellite observation and cannot certify routes outside the case AOI.
    road_union = roads.geometry.union_all() if not roads.empty else LineString()
    unknown = unsupported.union(road_union.difference(aoi))
    car, walk = build_graph(roads), build_graph(roads, True)
    car_post, walk_post = screen(car, hazard, unknown), screen(walk, hazard, unknown)
    car_sources, walk_sources = snap(settlements, car), snap(settlements, walk)
    # A facility inside unsupported terrain is not a known usable destination.
    eligible_hospitals = hospitals[~hospitals.geometry.intersects(unsupported)] if not hospitals.empty and not unsupported.is_empty else hospitals
    eligible_hospitals = eligible_hospitals[eligible_hospitals.geometry.apply(aoi.covers)] if not eligible_hospitals.empty else eligible_hospitals
    car_targets, walk_targets = snap(eligible_hospitals, car), snap(eligible_hospitals, walk)
    items, points = [], []
    for _, row in settlements.iterrows():
        sid = str(row['osm_id'])
        road_status, road_base = connectivity(car, car_post, car_sources.get(sid), car_targets.values())
        foot_status, foot_base = connectivity(walk, walk_post, walk_sources.get(sid), walk_targets.values())
        if not unsupported.is_empty and row.geometry.intersects(unsupported):
            road_status = foot_status = 'unknown'
        item = {'osm_id': sid, 'id': sid, 'name': row.get('name') if isinstance(row.get('name'), str) else sid,
                'road_status': road_status, 'status': road_status, 'foot_status': foot_status,
                'baseline_connected': road_base or foot_base,
                'interpretation': 'Conditional access to mapped medical facilities within this AOI; no physical road closure is verified'}
        items.append(item)
        points.append(feature(row.geometry, item))
    exposed, unknown_roads, bridges = [], [], []
    # Show walking paths too, even where they are excluded from the car graph.
    for _, row in roads.iterrows():
        geom = row.geometry
        props = {'osm_id': str(row['osm_id']), 'name': row.get('name') if isinstance(row.get('name'), str) else None,
                 'bridge': str(row.get('bridge', 'no')), 'status': 'potentially_affected'}
        if not hazard.is_empty and geom.intersects(hazard):
            exposed.append(feature(geom.intersection(aoi), props))
            if props['bridge'] not in {'no', '0', 'None', 'nan', ''}:
                bridges.append(feature(geom.intersection(aoi), props))
        if not unknown.is_empty and geom.intersects(unknown):
            unknown_roads.append(feature(geom.intersection(aoi), {**props, 'status': 'unknown'}))
    affected_buildings, unknown_buildings = [], []
    for _, row in buildings.iterrows():
        props = {'osm_id': str(row['osm_id']), 'status': 'potentially_affected'}
        if not hazard.is_empty and row.geometry.intersects(hazard):
            affected_buildings.append(feature(row.geometry.intersection(aoi), props))
        if not unsupported.is_empty and row.geometry.intersects(unsupported):
            unknown_buildings.append(feature(row.geometry.intersection(aoi), {**props, 'status': 'unknown'}))
    return {'layers': {'damaged_roads': collection(exposed), 'unknown_roads': collection(unknown_roads),
                       'affected_bridges': collection(bridges), 'affected_buildings': collection(affected_buildings),
                       'unknown_buildings': collection(unknown_buildings), 'settlements': collection(points),
                       'landing_zones': collection([])},
            'summary': {'total_settlements': len(items), 'settlements_cut_off': sum(x['road_status'] == 'potential_cut_off' for x in items),
                        'settlements_access_unknown': sum(x['road_status'] == 'unknown' for x in items),
                        'foot_cut_off': sum(x['foot_status'] == 'potential_cut_off' for x in items),
                        'damaged_road_segments': len(exposed), 'affected_bridges': len(bridges),
                        'affected_buildings': len(affected_buildings), 'total_buildings': len(buildings),
                        'hospitals_monitored': len(hospitals), 'unknown_roads': len(unknown_roads),
                        'viable_landing_zones': None, 'pedestrian_accessible_only': None},
            'handles': {'baseline_road_graph': car, 'hazard_road_graph': car_post,
                        'settlement_road_nodes': car_sources, 'hospital_road_nodes': car_targets}, 'settlements': items}
