"""Real Sentinel-1 metadata discovery from Copernicus Data Space."""

from .sentinel1 import CatalogError, NoSuitablePair, S1Pair, select_s1_pair

__all__ = ["CatalogError", "NoSuitablePair", "S1Pair", "select_s1_pair"]
