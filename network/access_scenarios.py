"""Conditional road-access bounds to fixed pre-event town/medical destinations.

Unknown roads/connectors/endpoints open in the optimistic scenario and close in
the pessimistic scenario. Candidate-intersecting roads/connectors/endpoints close
in both. Neither scenario establishes physical closure or safe access.
"""
from collections import Counter
import math

import geopandas as gpd
import networkx as nx
import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, mapping
from shapely.ops import transform

from network.observed_access import build_graph, screen, active


def buffered_query_aoi(aoi, buffer_m=2000):
    from shapely.geometry import shape
    boundary=shape(aoi)
    buffer_m=float(buffer_m)
    if not math.isfinite(buffer_m) or not 0<=buffer_m<=5000:
        raise ValueError('Access-network buffer must be between 0 and 5000 metres')
    if buffer_m==0:return mapping(boundary)
    local=gpd.GeoSeries([boundary],crs=4326).estimate_utm_crs()
    if local is None:raise ValueError('No local metric CRS for access buffer')
    fwd=Transformer.from_crs(4326,local,always_xy=True).transform
    inv=Transformer.from_crs(local,4326,always_xy=True).transform
    return mapping(transform(inv,transform(fwd,boundary).buffer(buffer_m)))


def destination_records(settlements, hospitals):
    records={}
    for target_type,frame in [('town',settlements),('medical',hospitals)]:
        for _,row in frame.iterrows():
            if target_type=='town' and str(row.get('place','')) not in {'town','city'}:continue
            sid=str(row['osm_id'])
            # A dual-tagged medical/town object has one identity, with medical priority.
            records[sid]={'osm_id':sid,'name':row.get('name') if isinstance(row.get('name'),str) else sid,
                          'kind':target_type,'point':row.geometry.centroid}
    return records


def _snap_records(records,graph,project,max_distance=500):
    if not graph:return {}
    nodes=list(graph.nodes)
    tree=cKDTree(np.array([project(*node) for node in nodes]))
    result={}
    for sid,record in records.items():
        point=record['point'];distance,index=tree.query(project(point.x,point.y))
        if distance<=max_distance:
            node=nodes[int(index)]
            # Endpoint snapping is a modelling assumption, not an observed path.
            result[sid]={**record,'node':node,'snap_distance_m':float(distance),
                         'connector':LineString([(point.x,point.y),node]) if (point.x,point.y)!=node else point}
    return result


def _endpoint_state(record,hazard,unknown,aoi):
    geom=record['connector']
    return {'candidate':not hazard.is_empty and geom.intersects(hazard),
            'unknown':not aoi.covers(geom) or (not unknown.is_empty and geom.intersects(unknown))}


def mode_scenarios(roads,sources,destinations,hazard,unknown,aoi,walking=False):
    graph=build_graph(roads,walking)
    if roads.empty:
        project=lambda x,y:(x,y) # No records can snap to an empty graph.
    else:
        project=Transformer.from_crs(4326,roads.estimate_utm_crs(),always_xy=True).transform
    snapped_sources=_snap_records(sources,graph,project)
    snapped_destinations=_snap_records(destinations,graph,project)
    # Road geometry outside the observed AOI is always unknown.
    union=roads.geometry.union_all() if not roads.empty else LineString()
    uncertainty=unknown.union(union.difference(aoi))
    screened=screen(graph,hazard,uncertainty)
    optimistic_graph=active(screened)
    pessimistic_graph=active(screened,True)
    details={};counts=Counter()
    for sid,source in sources.items():
        base={'eligible':False,'optimistic_connected':None,'pessimistic_connected':None,'target':None}
        if sid not in snapped_sources:
            details[sid]={**base,'reason':'source_not_snapped_within_500m'};counts['not_assessed']+=1;continue
        source=snapped_sources[sid]
        # A town/settlement must not select itself as its own destination.
        targets={key:value for key,value in snapped_destinations.items() if key!=sid}
        if not targets:
            details[sid]={**base,'reason':'no_other_snapped_destination'};counts['not_assessed']+=1;continue
        distances=nx.single_source_dijkstra_path_length(graph,source['node'],weight='length_m')
        reachable=[target for target in targets.values() if target['node'] in distances]
        if not reachable:
            details[sid]={**base,'reason':'baseline_disconnected'}
            counts['baseline_disconnected']+=1;counts['not_assessed']+=1;continue
        target=min(reachable,key=lambda t:(distances[t['node']]+t['snap_distance_m'],t['osm_id']))
        source_state=_endpoint_state(source,hazard,unknown,aoi)
        target_state=_endpoint_state(target,hazard,unknown,aoi)
        candidate_endpoint=source_state['candidate'] or target_state['candidate']
        unknown_endpoint=source_state['unknown'] or target_state['unknown']
        optimistic=(not candidate_endpoint and nx.has_path(optimistic_graph,source['node'],target['node']))
        pessimistic=(not candidate_endpoint and not unknown_endpoint and nx.has_path(pessimistic_graph,source['node'],target['node']))
        if pessimistic and not optimistic:raise ValueError('Scenario monotonicity failed')
        counts['eligible']+=1
        counts['optimistic_connected' if optimistic else 'optimistic_disconnected']+=1
        counts['pessimistic_connected' if pessimistic else 'pessimistic_disconnected']+=1
        details[sid]={'eligible':True,'reason':'fixed_pre_event_destination',
            'target':{k:target[k] for k in ['osm_id','name','kind','snap_distance_m']},
            'baseline_network_distance_m':float(distances[target['node']]),
            'baseline_distance_with_snap_connectors_m':float(distances[target['node']]+source['snap_distance_m']+target['snap_distance_m']),
            'source_snap_distance_m':source['snap_distance_m'],
            'source_unknown':bool(source_state['unknown']),'target_unknown':bool(target_state['unknown']),
            'optimistic_connected':bool(optimistic),'pessimistic_connected':bool(pessimistic)}
    summary={key:int(counts[key]) for key in ['eligible','not_assessed','baseline_disconnected','optimistic_connected','optimistic_disconnected','pessimistic_connected','pessimistic_disconnected']}
    summary['cut_off_lower']=summary['optimistic_disconnected']
    summary['cut_off_upper']=summary['pessimistic_disconnected']
    assert summary['cut_off_lower']<=summary['cut_off_upper']<=summary['eligible']
    assert summary['eligible']+summary['not_assessed']==len(sources)
    return summary,details


def analyze_scenarios(roads,settlements,context_settlements,hospitals,hazard_frame,unknown_frame,aoi,
                      source_status,buffer_m=2000):
    meta={'version':1,'network_buffer_m':float(buffer_m),'source_status':dict(source_status),
          'destination_policy':'Nearest pre-event reachable town/city or mapped medical facility by network distance plus snap connectors; fixed per mode; own identity excluded',
          'unknown_policy':'Open in optimistic scenario; closed in pessimistic scenario, including endpoints and snap connectors',
          'candidate_policy':'Candidate-intersecting edges, endpoints and connectors conditionally closed in both scenarios',
          'snap_limit_m':500,'graph_model':'Undirected OSM coordinate-node graph; one-way restrictions and grade-separated crossing inference are not modelled',
          'interpretation':'Conditional scenario bounds among baseline-connected mapped settlements; not confirmed isolation, damage or safe routes'}
    needed=['roads','settlements','hospitals']
    if any(source_status.get(k) not in {'available','empty'} for k in needed):
        return {**meta,'status':'unavailable','car':None,'foot':None,'reason':'Historical road, settlement or medical destination source unavailable'},{}
    hazard=hazard_frame.geometry.union_all() if not hazard_frame.empty else LineString()
    unknown=unknown_frame.geometry.union_all() if not unknown_frame.empty else LineString()
    sources={str(row['osm_id']):{'osm_id':str(row['osm_id']),'point':row.geometry.centroid} for _,row in settlements.iterrows()}
    destinations=destination_records(context_settlements,hospitals)
    output={**meta,'status':'complete','source_settlements':len(sources),
            'mapped_town_destinations':sum(x['kind']=='town' for x in destinations.values()),
            'mapped_medical_destinations':sum(x['kind']=='medical' for x in destinations.values())}
    details={sid:{} for sid in sources}
    for mode,walking in [('car',False),('foot',True)]:
        output[mode],mode_details=mode_scenarios(roads,sources,destinations,hazard,unknown,aoi,walking)
        for sid,value in mode_details.items():details[sid][mode]=value
    return output,details
