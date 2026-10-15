"""Synthetic graph/geometry fixtures only, never product results."""
import copy
import io

import geopandas as gpd
import pytest
from pypdf import PdfReader
from shapely.geometry import Point, LineString, box, shape, mapping

from network.access_scenarios import analyze_scenarios, buffered_query_aoi
from network.observed_access import analyze
from reports.one_page import one_page_pdf


AOI=box(85.095,27.945,85.135,27.965)
NODES=[(85.10,27.95),(85.11,27.95),(85.12,27.95),(85.13,27.95)]
STATUS={name:'available' for name in ['roads','settlements','hospitals']}


def frame(rows):
    return gpd.GeoDataFrame(rows or {'geometry':[]},geometry='geometry',crs=4326)


def fixture():
    roads=frame([{'osm_id':'way/1','highway':'residential','geometry':LineString(NODES)}])
    settlements=frame([{'osm_id':'node/1','place':'village','name':'Fixture village','geometry':Point(NODES[0])}])
    hospitals=frame([{'osm_id':'node/2','amenity':'hospital','name':'Fixture hospital','geometry':Point(NODES[-1])}])
    return roads,settlements,hospitals


def run(hazard=None,unknown=None,roads=None,settlements=None,hospitals=None,context=None,status=None,aoi=AOI):
    base_roads,base_settlements,base_hospitals=fixture()
    roads=roads if roads is not None else base_roads
    settlements=settlements if settlements is not None else base_settlements
    hospitals=hospitals if hospitals is not None else base_hospitals
    return analyze_scenarios(roads,settlements,context if context is not None else settlements,hospitals,
        frame([{'geometry':hazard}]) if hazard is not None else frame([]),
        frame([{'geometry':unknown}]) if unknown is not None else frame([]),
        aoi,status or STATUS,2000)


def test_unknown_road_creates_ordered_bounds():
    summary,details=run(unknown=box(85.114,27.949,85.116,27.951))
    for mode in ['car','foot']:
        assert summary[mode]['eligible']==1
        assert (summary[mode]['cut_off_lower'],summary[mode]['cut_off_upper'])==(0,1)
        assert details['node/1'][mode]['optimistic_connected'] is True
        assert details['node/1'][mode]['pessimistic_connected'] is False


def test_candidate_road_closes_in_both_scenarios():
    summary,_=run(hazard=box(85.114,27.949,85.116,27.951))
    assert (summary['car']['cut_off_lower'],summary['car']['cut_off_upper'])==(1,1)


def test_unknown_settlement_remains_explicit_but_scenarios_can_be_evaluated():
    unknown=Point(NODES[0]).buffer(.0001)
    summary,details=run(unknown=unknown)
    assert details['node/1']['car']['source_unknown'] is True
    assert summary['car']['eligible']==1
    assert (summary['car']['cut_off_lower'],summary['car']['cut_off_upper'])==(0,1)
    roads,settlements,hospitals=fixture()
    legacy=analyze(roads,frame([]),settlements,hospitals,frame([]),frame([{'geometry':unknown}]),AOI)
    assert legacy['settlements'][0]['road_status']=='unknown'


def test_unknown_hospital_is_open_only_in_optimistic_scenario():
    summary,details=run(unknown=Point(NODES[-1]).buffer(.0001))
    assert details['node/1']['car']['target_unknown'] is True
    assert (summary['car']['cut_off_lower'],summary['car']['cut_off_upper'])==(0,1)


def test_source_town_cannot_select_itself():
    roads,settlements,_=fixture();settlements['place']='town'
    summary,details=run(roads=roads,settlements=settlements,hospitals=frame([]))
    assert summary['mapped_town_destinations']==1
    assert summary['car']['eligible']==0 and summary['car']['not_assessed']==1
    assert details['node/1']['car']['reason']=='no_other_snapped_destination'


def test_other_town_is_a_real_destination():
    roads,settlements,_=fixture()
    towns=frame([{'osm_id':'node/3','place':'town','geometry':Point(NODES[-1])}])
    summary,details=run(roads=roads,settlements=settlements,hospitals=frame([]),context=towns)
    assert summary['car']['eligible']==1
    assert details['node/1']['car']['target']['kind']=='town'


def test_baseline_disconnection_is_not_new_cut_off():
    roads=frame([{'osm_id':'way/a','highway':'residential','geometry':LineString(NODES[:2])},
                 {'osm_id':'way/b','highway':'residential','geometry':LineString(NODES[2:])}])
    summary,details=run(roads=roads)
    assert summary['car']['baseline_disconnected']==1
    assert summary['car']['eligible']==0
    assert summary['car']['cut_off_lower']==summary['car']['cut_off_upper']==0
    assert details['node/1']['car']['optimistic_connected'] is None


def test_nearest_destination_is_fixed_before_candidate_closures():
    roads=frame([{'osm_id':'way/a','highway':'residential','geometry':LineString(NODES[:2])},
                 {'osm_id':'way/b','highway':'residential','geometry':LineString([NODES[0],(85.10,27.962)])}])
    hospitals=frame([{'osm_id':'node/near','geometry':Point(NODES[1])},
                     {'osm_id':'node/far','geometry':Point(85.10,27.962)}])
    summary,details=run(roads=roads,hospitals=hospitals,hazard=box(85.104,27.949,85.106,27.951))
    assert details['node/1']['car']['target']['osm_id']=='node/near'
    assert summary['car']['optimistic_disconnected']==1


def test_roads_and_targets_outside_observed_aoi_are_unknown():
    aoi=box(85.095,27.945,85.115,27.955)
    summary,details=run(aoi=aoi)
    assert summary['car']['eligible']==1
    assert (summary['car']['cut_off_lower'],summary['car']['cut_off_upper'])==(0,1)
    assert details['node/1']['car']['target_unknown'] is True


def test_missing_destination_data_yields_null_scenarios():
    summary,details=run(status={**STATUS,'hospitals':'unavailable'})
    assert summary['status']=='unavailable'
    assert summary['car'] is None and details=={}


def test_empty_roads_do_not_invent_connections():
    summary,_=run(roads=frame([]))
    assert summary['car']['eligible']==0
    assert summary['car']['not_assessed']==1


def test_snapping_has_a_bounded_limit():
    roads,settlements,_=fixture()
    settlements['geometry']=[Point(85.10,27.96)]
    summary,details=run(roads=roads,settlements=settlements)
    assert summary['car']['eligible']==0
    assert details['node/1']['car']['reason']=='source_not_snapped_within_500m'


def test_buffer_is_metric_bounded_and_contains_case():
    expanded=shape(buffered_query_aoi(mapping(AOI),2000))
    assert expanded.covers(AOI)
    assert expanded.area>AOI.area
    assert shape(buffered_query_aoi(mapping(AOI),0)).equals(AOI)
    for value in [-1,5001,float('nan')]:
        with pytest.raises(ValueError):buffered_query_aoi(mapping(AOI),value)


def test_pdf_keeps_one_page_and_marks_scenario_denominator():
    scenarios,_=run(unknown=box(85.114,27.949,85.116,27.951))
    job={'id':'scenario-test','flood_date':'2026-08-26',
         'summary':{'case':'fixture only','method':'baseline','candidate_area_km2':0,'candidate_pixels':0,
                    'assessable_fraction':.5,'unknown_area_km2':1,'infrastructure_status':{},'access_scenarios':scenarios},
         'provenance':{},'warnings':[]}
    reader=PdfReader(io.BytesIO(one_page_pdf(job)))
    assert len(reader.pages)==1
    text=reader.pages[0].extract_text()
    assert 'Town / medical access scenarios' in text
    assert '0 to 1 / 1 assessed' in text


def test_orchestrator_queries_buffer_but_keeps_case_settlement_scope(monkeypatch,tmp_path):
    import asyncio
    from api import integrated_jobs as jobs
    from network.verified_osm import FILTERS
    roads,settlements,hospitals=fixture()
    case=box(85.095,27.945,85.115,27.955)
    query_seen=[]
    def fetch(query,event):
        query_seen.append(shape(query))
        return {'roads':roads,'settlements':settlements,'hospitals':hospitals,'buildings':frame([])}, {
            'snapshot_requested':'2026-08-25T00:00:00Z',
            'layers':{name:{'status':'available' if name!='buildings' else 'empty'} for name in FILTERS}}, []
    satellite={'hazard':frame([]),'unknown':frame([]),'aoi':case,
        'summary':{'case':'explicit-unit-fixture','method':'baseline','resolution_m':20,
                   'candidate_area_km2':0,'candidate_pixels':0,'assessable_fraction':1,'unknown_area_km2':0},
        'provenance':{'source_pair':{},'limitations':[]}}
    monkeypatch.setenv('TRINETRA_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setenv('TRINETRA_ACCESS_BUFFER_M','2000')
    monkeypatch.setattr(jobs,'load_satellite',lambda *args:satellite)
    monkeypatch.setattr(jobs,'fetch_infrastructure',fetch)
    job=jobs.create_job('2026-08-26',mapping(case),'baseline')
    asyncio.run(jobs.run_job(job['id']))
    assert job['state']=='COMPLETE',job['message']
    assert query_seen[0].covers(case) and query_seen[0].area>case.area
    scenarios=job['summary']['access_scenarios']
    assert scenarios['source_settlements']==1
    assert scenarios['mapped_medical_destinations']==1
    assert (scenarios['car']['cut_off_lower'],scenarios['car']['cut_off_upper'])==(0,1)
    assert job['summary']['infrastructure_status']['hospitals']=='empty'
    assert job['layers']['settlements']['features'][0]['properties']['access_scenarios']['car']['target']['osm_id']=='node/2'
    assert job['provenance']['osm']['network_buffer_m']==2000
