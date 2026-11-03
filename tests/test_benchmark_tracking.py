"""Resume must not silently combine different experiments."""
import argparse
import csv
import json

import pytest

from evaluation.tracking import prepare_run

@pytest.fixture
def run(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'solver.py').write_text('value = 1\n')
    policy = tmp_path / 'policy.yaml'
    policy.write_text('limit: 1\n')
    args = argparse.Namespace(out=str(tmp_path / 'run.csv'), resume=False, budget=1)
    fields = ['n', 'harm']
    inputs = {'policy': policy}
    return args, fields, inputs, tmp_path

def start(run):
    args, fields, inputs, root = run
    out = prepare_run(args, fields, inputs, root)
    with out.open('w', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(fields)
        writer.writerow([10, 0])
    return out

def test_identical_resume_preserves_output_and_metadata(run):
    out = start(run)
    meta = out.with_suffix('.csv.meta.json')
    before = out.read_bytes(), meta.read_bytes()
    run[0].resume = True
    assert prepare_run(*run) == out
    assert (out.read_bytes(), meta.read_bytes()) == before
    assert json.loads(meta.read_text())['code']['src/solver.py']

@pytest.mark.parametrize('change', ['settings', 'inputs', 'code', 'fields', 'environment'])
def test_incompatible_resume_is_rejected_without_writing(run, change):
    out = start(run)
    args, fields, inputs, root = run
    args.resume = True
    if change == 'settings':
        args.budget = 2
    elif change == 'inputs':
        inputs['policy'].write_text('limit: 2\n')
    elif change == 'code':
        (root / 'src' / 'solver.py').write_text('value = 2\n')
    elif change == 'fields':
        fields.append('mode')
    else:
        meta = out.with_suffix('.csv.meta.json')
        data = json.loads(meta.read_text())
        data['environment']['python'] = 'different'
        meta.write_text(json.dumps(data))
    before = out.read_bytes()
    with pytest.raises(ValueError, match='incompatible resume'):
        prepare_run(*run)
    assert out.read_bytes() == before

def test_existing_run_requires_explicit_resume(run):
    out = start(run)
    with pytest.raises(ValueError, match='already exists'):
        prepare_run(*run)
    assert out.exists()

@pytest.mark.parametrize('contents', ['n,harm\n10,0\n', ''])
def test_legacy_csv_is_not_adopted(run, contents):
    args, _, _, root = run
    out = root / 'run.csv'
    out.write_text(contents)
    args.resume = True
    with pytest.raises(ValueError, match='both CSV and metadata'):
        prepare_run(*run)
    assert out.read_text() == contents

@pytest.mark.parametrize('contents', ['wrong,header\n', 'n,harm\n10\n', 'n,harm\n10,0,extra\n'])
def test_bad_csv_is_rejected(run, contents):
    out = start(run)
    out.write_text(contents)
    run[0].resume = True
    with pytest.raises(ValueError, match='header|incomplete CSV'):
        prepare_run(*run)
    assert out.read_text() == contents
