from evaluation.run_scale import comparable, summarize

def test_modes_are_not_mixed_and_zero_is_preserved():
    row = dict(best_greedy_harm=100, ls_mode='relaxed_downtime', ls_status='FEASIBLE', ls_harm=0,
               bnb_mode='strict', bnb_status='FEASIBLE', bnb_harm=80, bnb_bound=50,
               cpsat_mode='relaxed_downtime', cpsat_status='OPTIMAL', cpsat_harm=0, cpsat_bound=0)
    assert comparable(row, 'strict') == ({'best_greedy': 100, 'bnb': 80}, 50)
    assert comparable(row, 'relaxed_downtime') == ({'ls': 0, 'cpsat': 0}, None)

def test_unsuccessful_solver_objectives_are_not_compared():
    row = dict(ls_mode='strict', ls_status='INFEASIBLE', ls_harm=2,
               bnb_mode='strict', bnb_status='UNKNOWN', bnb_harm=None, bnb_bound=0)
    assert comparable(row, 'strict') == ({}, 0)

def test_missing_legacy_modes_are_not_guessed():
    assert comparable(dict(ls_harm=1, cpsat_harm=2, cpsat_bound=1), 'strict') == ({}, None)

def test_summary_handles_zero_and_inconsistent_bounds(capsys):
    from evaluation.run_scale import FIELDS
    row = dict.fromkeys(FIELDS)
    row.update(n=3, tier='mild', best_greedy_harm=0, bnb_mode='strict',
               bnb_status='OPTIMAL', bnb_harm=0, bnb_bound=1)
    summarize([row], [3], ['mild'])
    output = capsys.readouterr().out
    assert 'inconsistent bound rows: 1' in output
    row['bnb_bound'] = 0
    summarize([row], [3], ['mild'])
    assert 'inconsistent bound rows: 0' in capsys.readouterr().out


def test_integer_oracle_bound_is_not_a_bound_for_the_original_problem():
    row = dict(cpsat_mode='strict', cpsat_status='OPTIMAL', cpsat_harm=10, cpsat_bound=10)
    assert comparable(row, 'strict') == ({'cpsat': 10}, None)
