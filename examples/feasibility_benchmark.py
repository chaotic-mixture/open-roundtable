"""Measure the bounded hard-rule search on nine seeded weekly rosters."""
import argparse
import json
from time import perf_counter

from examples.nested_benchmark import CASE_SIZES
from examples.overnight_score import expected
from examples.paired_panel_benchmark import seeded_spec
from examples.refine_schedule import feasibility
from examples.repair_benchmark import schedule
from examples.user_consult import validate_roster


def run(first_seed, budget):
    for seed, size in enumerate(CASE_SIZES, first_seed):
        rows, _ = validate_roster(seeded_spec(seed, size))
        start = perf_counter()
        result = feasibility(rows, lambda plan: True, budget)
        elapsed = perf_counter() - start
        plan = result['plan']
        if plan is not None and any(expected(schedule(rows, plan)).values()):
            raise ValueError(f'seed {seed} failed independent hard-rule verification')
        print(json.dumps(dict(seed=seed, shifts=size, budget=budget, status=result['status'],
                              nodes=result['nodes'], seconds=elapsed, plan=plan)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first-seed', type=int, default=501)
    parser.add_argument('--budget', type=int, default=10000, help='maximum search nodes per roster')
    args = parser.parse_args()
    if args.budget < 1:
        parser.error('--budget must be positive')
    run(args.first_seed, args.budget)
