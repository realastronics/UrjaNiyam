import json
from dataclasses import replace

from evaluation.export_demo import ROOT, city_record, write_export
from loadshed import Policy
from loadshed.sim import make_grid
from loadshed.sim.grid import feeder_loads


def test_city_metadata_and_loads_belong_to_its_seed():
    policy = Policy.from_yaml(ROOT / 'configs/policy.yaml')
    for seed in (1, 4):
        grid = make_grid(10, seed)
        city = city_record(grid, policy, 68, 16)
        assert city['seed'] == seed
        for district, spec in zip(city['districts'], grid.specs):
            assert district['connections'] == spec.consumers
            assert district['tier'] == spec.tier.value
            assert district['load_kw'] == list(feeder_loads(spec, replace(policy, horizon_slots=32), 68)[:16])


def test_json_and_browser_export_are_identical(tmp_path):
    payload = {'schema': 2, 'notes': 'A district', 'episodes': []}
    output = tmp_path / 'demo.json'
    write_export(payload, output)
    javascript = output.with_name('demo_data.js').read_text()
    assert json.loads(output.read_text()) == json.loads(javascript.removeprefix('window.DEMO=').removesuffix(';\n'))
    assert not list(tmp_path.glob('*.tmp'))
