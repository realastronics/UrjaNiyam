from loadshed import Policy
from loadshed.sim import build_problem, make_grid, make_scenario, slot_of_day
from loadshed.whatif import explain_cuts, reasons

POL = Policy.from_yaml("configs/policy.yaml")
p = build_problem(make_grid(10, 1), POL, make_scenario("moderate", 0.0684), slot_of_day(17), episode_offset=8)
plan, price = explain_cuts(p)
r = reasons(p, plan)
cut = [f for f in plan.schedule.cut if plan.schedule.cut[f][0]]
for f in cut:
    print(f"{f} is cut now. " + (f"Keeping it on instead would add {price[f]:,.0f} harm." if price[f] is not None else "Keeping it on leaves no executable plan."))
for f, why in r.items():
    if f not in cut and why != "chosen by the optimizer":
        print(f"{f} stays on: {why}.")
print(f"The other {sum(1 for f, w in r.items() if f not in cut and w == 'chosen by the optimizer')} feeders stay on because the shortfall is already covered.")