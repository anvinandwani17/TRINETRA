"""Pure provenance/coverage checks for frozen-checkpoint evaluation."""


def evaluation_coverage(training, report, events, samples, split):
    if split not in {'validation', 'test'}:
        raise ValueError('Evaluate only validation or held-out test')
    if report['split_sha256'] != training['split_sha256']:
        raise ValueError('Evaluation partition changed')
    events = set(map(str, events))
    train = set(map(str, training['train_events']))
    selection = set(map(str, training['validation_events']))
    if events & train:
        raise ValueError('Evaluation events overlap training')
    if split == 'test' and events & selection:
        raise ValueError('Test events overlap checkpoint-selection validation')
    official = {str(event) for event, assigned in report['assignments'].items() if assigned == split}
    if not events or not events <= official:
        raise ValueError('Evaluation events do not match the official partition')
    total = report['counts'][split]['samples']
    if not isinstance(total, int) or not 0 < samples <= total:
        raise ValueError('Invalid local/official evaluation sample count')
    return {'official_events': sorted(official), 'missing_events': sorted(official-events),
            'official_samples': total, 'sample_coverage_fraction': samples/total,
            'full_split_evaluated': samples == total and events == official,
            'checkpoint_selection_events': sorted(selection)}
