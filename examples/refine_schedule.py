"""Bounded preference-guided search from an already feasible weekly schedule."""
from examples.repair_benchmark import schedule
from open_roundtable.schedule_expert import check


def refine(rows, plans, rank, indices):
    """Keep the best feasible single-shift move for each seed and requested shift."""
    result = list(plans)
    for base in plans:
        current = base
        for index in indices:
            best = current
            best_score = rank(current)
            for hour in range(169-rows[index][2]):
                trial = current.copy()
                trial[index] = hour
                score = rank(trial)
                if score > best_score and not any(check(schedule(rows,trial)).values()):
                    best, best_score = trial, score
            current = best
            if current not in result:
                result.append(current)
    # Moving a single shift cannot free an occupied preferred slot. Fix each
    # targeted shift first, then let the other shifts move around that anchor.
    best = max(result,key=rank)
    for index in indices:
        original = [row[1] for row in rows]
        def priority(hour):
            trial = original.copy()
            trial[index] = hour
            return rank(trial), -abs(hour-original[index])
        hours = sorted(range(169-rows[index][2]),key=priority,reverse=True)
        for anchor in hours[:3]:
            starts = original.copy()
            starts[index] = anchor
            placed = schedule([rows[index]],[anchor])
            for j,row in enumerate(rows):
                if j == index:
                    continue
                for hour in sorted(range(169-row[2]),key=lambda h:abs(h-row[1])):
                    token = schedule([row],[hour])[0]
                    if not any(check(placed+[token]).values()):
                        starts[j] = hour
                        placed.append(token)
                        break
                else:
                    break
            else:
                if rank(starts)>rank(best):
                    best=starts
                    result.append(best)
    return result


def exceeds_weekly_capacity(rows):
    # A room or worker can occupy at most 168 distinct hours in one week.
    for field in (1, 2):
        hours = {}
        long_shifts = {}
        for key, _, duration in rows:
            resource = key[field]
            hours[resource] = hours.get(resource, 0) + duration
            work = hours[resource]
            # Each worker needs eight hours off between blocks of at most eight hours.
            # Touching shifts can share a block, so count blocks from total hours.
            rest = 0
            if field == 1:
                # Two shifts longer than four hours cannot share an eight-hour block.
                long_shifts[resource] = long_shifts.get(resource, 0) + (duration > 4)
                blocks = max((work + 7) // 8, long_shifts[resource])
                rest = 8 * (blocks - 1)
            if work + rest > 168:
                return True
    return False


def feasibility(rows, accepts, budget):
    """Exact finite-domain search; a node limit yields unknown, never infeasible."""
    if type(budget)!=int or budget<1:
        raise ValueError('search_nodes must be a positive integer')
    if exceeds_weekly_capacity(rows):
        return dict(status='infeasible', nodes=0, plan=None)
    original=[r[1] for r in rows]
    domains=[sorted(range(169-r[2]),key=lambda h:abs(h-r[1])) for r in rows]
    starts=[]
    visits=0

    def search():
        nonlocal visits
        if len(starts)==len(rows):
            if accepts(starts) and not any(check(schedule(rows,starts)).values()):
                return starts.copy()
            return None
        i=len(starts)
        key,_,duration=rows[i]
        for h in domains[i]:
            if visits>=budget:
                raise StopIteration
            visits+=1
            # Overlap cannot be repaired by inserting more shifts. A short rest
            # gap might be filled by another shift, so check rest only at leaves.
            if any((key[1]==rows[j][0][1] or key[2]==rows[j][0][2])
                   and h<s+rows[j][2] and s<h+duration for j,s in enumerate(starts)):
                continue
            starts.append(h)
            result=search()
            starts.pop()
            if result is not None:
                return result
        return None

    try:
        plan=search()
        return dict(status='feasible' if plan is not None else 'infeasible',nodes=visits,plan=plan)
    except StopIteration:
        return dict(status='unknown',nodes=visits,plan=None)
