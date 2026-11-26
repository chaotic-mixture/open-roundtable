"""Ordered, caller-defined preference ranges; the first matching priority wins."""


def compile_criteria(spec, ids):
    if not isinstance(spec, list):
        raise ValueError('criteria must be an array')
    rules = []
    for index, clause in enumerate(spec):
        location = f'criteria[{index}]'
        if not isinstance(clause, dict):
            raise ValueError(f'{location} must be an object')
        if clause.get('shift') not in ids:
            raise ValueError(f'{location}.shift must match an existing shift ID')
        field = clause.get('field')
        if field not in ('start','hour','day','delta','end'):
            raise ValueError(f'{location}.field must be start, hour, day, delta or end')
        low,high = clause.get('min'),clause.get('max')
        if type(low)!=int or type(high)!=int or low>high:
            raise ValueError(f'{location} requires integer bounds with min <= max')
        rules.append((ids.index(clause['shift']),field,low,high))
    return rules


def evaluate(rules, rows, starts):
    flags = []
    for idx,field,low,high in rules:
        hour=starts[idx]
        values=dict(start=hour,hour=hour%24,day=hour//24,
                    delta=hour-rows[idx][1],end=hour+rows[idx][2])
        flags.append(low<=values[field]<=high)
    return tuple(flags)
