"""Retired prototype report; never default missing evidence to zero or fixed dates."""


def generate_situation_report(*args, **kwargs):
    raise RuntimeError(
        'Prototype report generation is retired. Use reports.grounded.report(job) '
        'or reports.html_report.report_html(job) with a verified integrated job. '
        'Missing data must retain unavailable/unknown status.'
    )
