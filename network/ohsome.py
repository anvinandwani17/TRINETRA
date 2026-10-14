"""Retired prototype OSM helper; synthetic infrastructure is never returned."""


def fetch_all_infrastructure(*args, **kwargs):
    raise RuntimeError(
        'network.ohsome is retired: it fabricated missing infrastructure and used '
        'a fixed snapshot. Use network.verified_osm.fetch_infrastructure(aoi, '
        'flood_date), retaining returned availability, provenance and warnings.'
    )


def fetch_osm_snapshot(*args, **kwargs):
    return fetch_all_infrastructure(*args, **kwargs)
