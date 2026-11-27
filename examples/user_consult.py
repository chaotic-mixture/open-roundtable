"""Consult on a caller-supplied weekly roster with free-text preferences."""
import json
from contextlib import closing
from examples.auto_consult import api, assert_post, encoded
from examples.overnight_score import expected
from examples.repair_benchmark import candidates, schedule
from examples.select_repair import choice
from examples.roster_criteria import compile_criteria, evaluate
from examples.refine_schedule import exceeds_weekly_capacity, refine, feasibility
from open_roundtable.core import Kernel
from open_roundtable.run_journal import initialize, dispatch


class NoFeasibleCandidate(ValueError):
    def __init__(self, attempt):
        self.status = attempt['status']
        self.nodes = attempt['nodes']
        super().__init__(f'hard-constraint feasibility {self.status} after {self.nodes} nodes')


def validate_roster(spec, *, for_repair=True):
    if not isinstance(spec,dict) or any(not isinstance(spec.get(k),str) or not spec[k].strip()
                                       for k in ('title','preference')):
        raise ValueError('provide title, preference and shifts')
    shifts=spec.get('shifts')
    if not isinstance(shifts,list) or not shifts:
        raise ValueError('provide a nonempty shifts array')
    rows=[]
    seen=set()
    labels={field:{} for field in ('id','worker','room')}
    for item in shifts:
        if not isinstance(item,dict):
            raise ValueError('each shift must be an object')
        for field in ('id','worker','room'):
            value=item.get(field)
            if not isinstance(value,str) or not value.strip():
                raise ValueError(f'{field} must be nonempty text')
        start,end=item.get('start'),item.get('end')
        if item['id'] in seen:
            raise ValueError(f"duplicate shift ID: {item['id']}")
        if type(start)!=int or type(end)!=int or not 0<=start<end<=168:
            raise ValueError(f"{item['id']}: start and end must be integer hours with 0 <= start < end <= 168")
        if for_repair and end-start>8:
            raise ValueError(f"{item['id']} cannot satisfy the 8-hour limit with its fixed duration")
        seen.add(item['id'])
        # The existing interval expert accepts three single-character identifiers.
        # Map external labels locally; publish only the caller's original labels.
        key=''
        for field, mapping in labels.items():
            key+=mapping.setdefault(item[field],chr(0x10000+len(mapping)))
        rows.append((key,start,end-start))
    if type(spec.get('search_nodes', 10000)) != int or spec.get('search_nodes', 10000) < 1:
        raise ValueError('search_nodes must be a positive integer')
    rules=compile_criteria(spec.get('criteria',[]),[s['id'] for s in shifts])
    return rows, rules


def selection_messages(spec, menu):
    return [dict(role='user',content='Select the best feasible weekly schedule for this request. '
                 'Candidate times are absolute hours. Return only {"plan": '
                 'zero-based integer}. Request and candidates: '+encoded([spec,menu]))]


def candidate_rank(rules, rows, plan):
    return evaluate(rules, rows, plan), -sum(h != row[1] for h, row in zip(plan, rows))


def prepare_candidates(spec, rows, rules):
    if exceeds_weekly_capacity(rows):
        raise NoFeasibleCandidate(dict(status='infeasible', nodes=0))
    shifts = spec['shifts']
    plans=candidates(rows)
    if not plans:
        attempt=feasibility(rows,lambda p:True,spec.get('search_nodes',10000))
        if attempt['plan'] is None:
            raise NoFeasibleCandidate(attempt)
        plans=[attempt['plan']]
    proof=None
    if rules:
        def rank(plan):
            return candidate_rank(rules, rows, plan)
        plans=refine(rows,plans,rank,dict.fromkeys(r[0] for r in rules))
        plans.sort(key=rank,reverse=True)
        if evaluate(rules[:1],rows,plans[0])[0]:
            proof=dict(status='feasible',nodes=0,plan=plans[0])
        else:
            proof=feasibility(rows,lambda p:evaluate(rules[:1],rows,p)[0],spec.get('search_nodes',10000))
            if proof['plan'] is not None:
                plans.append(proof['plan'])
                plans.sort(key=rank,reverse=True)
    if any(any(expected(schedule(rows,p)).values()) for p in plans):
        raise ValueError('expert candidate failed independent verification')
    menu=[[dict(item,start=h,end=h+row[2]) for item,h,row in zip(shifts,p,rows)] for p in plans]
    return plans, menu, proof


def repair_preview(spec):
    rows, rules = validate_roster(spec)
    starts = [row[1] for row in rows]
    met = evaluate(rules, rows, starts)
    if all(met) and not any(expected(schedule(rows, starts)).values()):
        proof = dict(status='feasible', nodes=0, plan=starts) if rules else None
        return dict(schedule=[dict(shift) for shift in spec['shifts']], criteria_met=met,
                    changed_shifts=0, changes=[], top_priority_search=proof)
    plans, menu, proof = prepare_candidates(spec, rows, rules)
    changes = [dict(id=old['id'], old_start=old['start'], old_end=old['end'],
                    new_start=new['start'], new_end=new['end'])
               for old, new in zip(spec['shifts'], menu[0], strict=True)
               if old['start'] != new['start']]
    return dict(schedule=menu[0], criteria_met=evaluate(rules, rows, plans[0]),
                changed_shifts=len(changes), changes=changes, top_priority_search=proof)


def consult(source, database, model, *, nested=False):
    spec=json.loads(source.read_text(encoding='utf-8-sig'))
    rows, rules = validate_roster(spec)
    plans, menu, proof = prepare_candidates(spec, rows, rules)
    database.touch(exist_ok=False)
    with closing(Kernel(database)) as k:
        initialize(k.db)
        parent = k.create(spec['title'],credits=2)
        topic = parent
        if nested:
            topic = k.delegate(parent, 'schedule panel', spec['title'], 2)
            k.start(parent)
        k.join(topic,'expert',kind='expert')
        k.join(topic,'model',kind='model',source=model)
        k.start(topic)
        assert_post(k,topic,'expert',encoded(dict(input=spec,candidates=menu)))
        request=dict(model=model,stream=False,think=False,format='json',options={'temperature':0},
                     messages=selection_messages(spec,menu))
        response,error,_=dispatch(k,topic,request,api)
        selected=0
        if response is not None:
            assert_post(k,topic,'model',encoded(response))
            try:
                selected=choice(response,len(menu))
            except (ValueError,TypeError,KeyError) as exc:
                error=repr(exc)
        body=encoded(dict(selected=selected,schedule=menu[selected],fallback=error,
                          criteria_met=evaluate(rules,rows,plans[selected]),top_priority_search=proof))
        version=k.propose(topic,body,previous_version=0)
        expert_agrees=(not rules or candidate_rank(rules, rows, plans[selected]) == candidate_rank(rules, rows, plans[0]))
        k.vote(topic,'expert',expert_agrees,version=version)
        if not error:
            k.vote(topic,'model',True,version=version)
        k.feedback(topic,'expert',encoded(dict(hard_pass=True)),source='independent hourly verification')
        result = dict(topic=topic,proposal=version,state=k.get(topic)['state'],**json.loads(body))
        if nested:
            spent = None
            if result['state'] == 'RESOLVED':
                spent = k.settle(topic)
                parent_version = k.propose(parent, body, previous_version=0)
                k.vote(parent, 'schedule panel', True, version=parent_version)
            result.update(parent=parent, parent_state=k.get(parent)['state'], settled_credits=spent)
        return result
