from loadshed import Policy
from loadshed.sim import build_problem, make_grid, make_scenario, slot_of_day
from loadshed.whatif import explain_cuts, reasons

POL = Policy.from_yaml("configs/policy.yaml")
p = build_problem(make_grid(10, 1), POL, make_scenario("moderate", 0.0684), slot_of_day(17), episode_offset=8)
plan, price = explain_cuts(p)
for f, why in reasons(p, plan).items():
    cost = price.get(f)
    print(f, "CUT" if plan.schedule.cut[f][0] else "on ", "|", why, "" if cost is None else f"| keeping it on costs +{cost:,.0f}")