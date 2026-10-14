"""Retired uncalibrated closure-probability prototype."""


def assign_edge_failure_probabilities(*args, **kwargs):
    raise RuntimeError(
        'Prototype closure probabilities are unavailable: fixed 0.85/0.95 '
        'assumptions were not calibrated. Use network.observed_access conditional '
        'scenarios and retain unknown access; no physical closure is verified.'
    )


def run_monte_carlo_simulation(*args, **kwargs):
    return assign_edge_failure_probabilities(*args, **kwargs)
