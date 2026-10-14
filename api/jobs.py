"""Retired prototype runner; never reuse its unverified cache or invented polygons."""
MESSAGE = (
    "api.jobs is retired because its prototype inputs were not verified. "
    "Use api.main:app or scripts/run_integrated_case.py with verified case outputs."
)


def create_job(*args, **kwargs):
    raise RuntimeError(MESSAGE)


def get_job(*args, **kwargs):
    raise RuntimeError(MESSAGE)


async def run_job(*args, **kwargs):
    raise RuntimeError(MESSAGE)


def compute_cache_key(*args, **kwargs):
    raise RuntimeError(MESSAGE)
