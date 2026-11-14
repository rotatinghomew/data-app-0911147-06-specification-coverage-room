from collections import Counter
from statistics import mean, stdev


def selected_readings(data, attribute):
    if attribute not in data['specifications']:
        raise ValueError('未知规格属性')
    spec = data['specifications'][attribute]
    rows = []
    for row in data['measurements']:
        if row['attribute'] != attribute:
            continue
        if row['unit'] != spec['unit']:
            raise ValueError('测量单位与规格不一致')
        if row['qualifier'] not in ('exact', 'below', 'above'):
            raise ValueError('未知检测限定')
        rows.append(row)
    return rows


def baseline(data):
    return {'attributes': list(data['specifications']),
            'batches': len(data['batches']),
            'qualifiers': dict(Counter(row['qualifier'] for row in data['measurements'])),
            'notice': '检测限不作为实际读数'}


def act(data, request):
    if request.get('operation') != 'summary':
        raise ValueError('未知声明操作')
    attribute = request.get('attribute')
    rows = selected_readings(data, attribute)
    exact = [row['value'] for row in rows if row['qualifier'] == 'exact']
    spec = data['specifications'][attribute]
    return {'attribute': attribute, 'exact_count': len(exact), 'bounded_count': len(rows) - len(exact),
            'exact_mean': mean(exact) if exact else None,
            'exact_standard_deviation': stdev(exact) if len(exact) > 1 else None,
            'exact_within_spec': sum(spec['lower'] <= value <= spec['upper'] for value in exact),
            'notice': '只描述精确观测，不构成总体覆盖声明'}
