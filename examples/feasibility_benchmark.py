"""Measure the bounded hard-rule search on nine seeded weekly rosters."""
import argparse
import json
from time import perf_counter

from examples.nested_benchmark import CASE_SIZES
from examples.overnight_score import expected
from examples.paired_panel_benchmark import seeded_spec
from examples.refine_schedule import feasibility
from examples.repair_benchmark import schedule
from examples.roster_criteria import evaluate
from examples.user_consult import preference_feasibility, validate_roster


def run(first_seed, budget, require_preferences=False):
    for seed, size in enumerate(CASE_SIZES, first_seed):
        rows, criteria = validate_roster(seeded_spec(seed, size))
        start = perf_counter()
        if require_preferences:
            result = preference_feasibility(rows, criteria, budget)
        else:
            result = feasibility(rows, lambda plan: True, budget)
        elapsed = perf_counter() - start
        plan = result['plan']
        criteria_met = changed_shifts = None
        if plan is not None:
            if any(expected(schedule(rows, plan)).values()):
                raise ValueError(f'seed {seed} failed independent hard-rule verification')
            criteria_met = evaluate(criteria, rows, plan)
            if require_preferences and not all(criteria_met):
                raise ValueError(f'seed {seed} failed requested preference verification')
            changed_shifts = sum(hour != row[1] for hour, row in zip(plan, rows, strict=True))
        print(json.dumps(dict(seed=seed, shifts=size, budget=budget, status=result['status'],
                              nodes=result['nodes'], seconds=elapsed, plan=plan,
                              require_preferences=require_preferences, criteria_met=criteria_met, changed_shifts=changed_shifts)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first-seed', type=int, default=501)
    parser.add_argument('--require-preferences', action='store_true', help='search only start hours satisfying all encoded criteria')
    parser.add_argument('--budget', type=int, default=10000, help='maximum search nodes per roster')
    args = parser.parse_args()
    if args.budget < 1:
        parser.error('--budget must be positive')
    run(args.first_seed, args.budget, args.require_preferences)
