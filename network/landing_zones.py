"""Retired landing-zone prototype; OSM tags cannot establish landing suitability."""


def extract_viable_landing_zones(*args, **kwargs):
    raise RuntimeError(
        'Landing-zone assessment is unavailable. Building footprints and absence '
        'of flood candidates cannot establish clear, dry or suitable landing '
        'terrain. The integrated API reports this capability as unassessed.'
    )


def match_settlements_to_landing_zones(*args, **kwargs):
    return extract_viable_landing_zones(*args, **kwargs)
