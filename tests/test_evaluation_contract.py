"""Partial pilot coverage and event contamination must not become test claims."""
import copy
import pytest
from trinetra.training.evaluation_contract import evaluation_coverage


def fixtures():
    hashes = {'train': 'train', 'validation': 'val', 'test': 'test'}
    training = {'train_events': ['1'], 'validation_events': ['2'], 'split_sha256': hashes}
    report = {'split_sha256': hashes, 'assignments': {'1': 'train', '2': 'validation', '3': 'test', '4': 'test'},
              'counts': {'validation': {'samples': 20}, 'test': {'samples': 100}}}
    return training, report


def test_partial_test_subset_is_explicit():
    training, report = fixtures()
    result = evaluation_coverage(training, report, ['3'], 2, 'test')
    assert result['missing_events'] == ['4']
    assert result['sample_coverage_fraction'] == .02
    assert result['full_split_evaluated'] is False
    assert result['checkpoint_selection_events'] == ['2']


def test_full_split_requires_all_samples_and_events():
    training, report = fixtures()
    assert evaluation_coverage(training, report, ['3', '4'], 100, 'test')['full_split_evaluated']
    assert not evaluation_coverage(training, report, ['3'], 100, 'test')['full_split_evaluated']


@pytest.mark.parametrize('events,match', [(['1'], 'overlap training'), (['2'], 'checkpoint-selection'), (['99'], 'official partition')])
def test_contaminated_or_unknown_test_events_rejected(events, match):
    training, report = fixtures()
    with pytest.raises(ValueError, match=match): evaluation_coverage(training, report, events, 2, 'test')


def test_changed_split_and_empty_samples_rejected():
    training, report = fixtures()
    changed = copy.deepcopy(report); changed['split_sha256']['test'] = 'changed'
    with pytest.raises(ValueError, match='partition changed'): evaluation_coverage(training, changed, ['3'], 2, 'test')
    with pytest.raises(ValueError, match='sample count'): evaluation_coverage(training, report, ['3'], 0, 'test')
