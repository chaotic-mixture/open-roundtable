"""Ordered, caller-defined preference ranges; the first matching priority wins."""


def compile_criteria(spec, ids):
    rules = []
    for clause in spec:
        if not isinstance(clause,dict) or clause.get('shift') not in ids:
            raise ValueError('unknown criterion shift')
        field = clause.get('field')
        if field not in ('start','hour','day','delta','end'):
            raise ValueError('criterion field must be start, hour, day, delta or end')
        low,high = clause.get('min'),clause.get('max')
        if type(low)!=int or type(high)!=int or low>high:
            raise ValueError('criterion needs inclusive integer min and max')
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
