import math
from collections import Counter
from statistics import mean, stdev

from scipy.stats import beta as _beta_dist
from scipy.stats import binom as _binom_dist
from scipy.stats import chi2 as _chi2_dist
from scipy.stats import nct as _nct_dist
from scipy.stats import norm as _norm_dist


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
    operation = request.get('operation')
    if operation == 'summary':
        return _summary(data, request)
    if operation == 'tolerance':
        return tolerance(data, request)
    raise ValueError('未知声明操作')


def _summary(data, request):
    attribute = request.get('attribute')
    rows = selected_readings(data, attribute)
    exact = [row['value'] for row in rows if row['qualifier'] == 'exact']
    spec = data['specifications'][attribute]
    return {'attribute': attribute, 'exact_count': len(exact), 'bounded_count': len(rows) - len(exact),
            'exact_mean': mean(exact) if exact else None,
            'exact_standard_deviation': stdev(exact) if len(exact) > 1 else None,
            'exact_within_spec': sum(spec['lower'] <= value <= spec['upper'] for value in exact),
            'notice': '只描述精确观测，不构成总体覆盖声明'}


# ---- 总体覆盖容忍声明 ----
#
# 方法要点（详见 docs/analysis-contract.md）：
# 1. 正向声明用分布自由次序统计量容忍界，置信度只依赖秩次：
#    单侧上界 X_(s)：P(覆盖>=p) = P(Bin(n,p) <= j-1)，j 为 X_(s) 在全体真值中的秩；
#    双侧 [X_(r),X_(s)]：P(覆盖>=p) = P(Bin(n,p) <= j2-j1-1)。
# 2. 检测限记录不是真实读数：其真实秩未知，按使结论最保守的秩处理；
#    确定落在界内的删失（如检测上限低于候选上界）才计入界内计数。
# 3. 固定规格用计数置信界（Clopper-Peason 下界），无法判定的删失只做部分识别。
# 4. 逐项、多项同时（Bonferroni）与多维联合（同一样品计数）分别给出，不混用。

SIDES = ('two-sided', 'lower', 'upper')


def _probability(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + '应为 0 到 1 之间的数')
    if not 0 < value < 1:
        raise ValueError(name + '应为 0 到 1 之间的数')
    return float(value)


def _binom_cdf(k, n, p):
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return float(_binom_dist.cdf(k, n, p))


def _coverage_confidence(count, n, p):
    # 界内计数为 count 时，“总体覆盖比例至少为 p”的置信度 P(Bin(n, p) <= count - 1)
    return _binom_cdf(count - 1, n, p)


def _coverage_lower_bound(count, n, confidence):
    if count <= 0:
        return 0.0
    return float(_beta_dist.ppf(1.0 - confidence, count, n - count + 1))


def _coverage_upper_bound(count, n, confidence):
    if count >= n:
        return 1.0
    return float(_beta_dist.ppf(confidence, count + 1, n - count))


def _skewness(values):
    size = len(values)
    if size < 3:
        return None
    center = mean(values)
    m2 = sum((value - center) ** 2 for value in values) / size
    if m2 == 0:
        return None
    m3 = sum((value - center) ** 3 for value in values) / size
    return m3 / m2 ** 1.5


def _classify(row, lower, upper):
    # 相对固定区间 [lower, upper] 分类记录；检测限只按确定信息归类
    if row['qualifier'] == 'exact':
        return 'within' if lower <= row['value'] <= upper else 'outside'
    if row['qualifier'] == 'below':
        # 真值 < 检测上限：上限不超过 lower 时必然在界外，否则无法判定
        return 'outside' if row['value'] <= lower else 'ambiguous'
    # 真值 > 检测下限：下限不低于 upper 时必然在界外，否则无法判定
    return 'outside' if row['value'] >= upper else 'ambiguous'


def _combine_status(first, second):
    if 'outside' in (first, second):
        return 'outside'
    if 'ambiguous' in (first, second) or 'missing' in (first, second):
        return 'ambiguous'
    return 'within'


def _per_batch(rows):
    batches = {}
    for row in rows:
        entry = batches.setdefault(row['batch'], {'n': 0, 'exact': 0, 'below': 0, 'above': 0})
        entry['n'] += 1
        entry[row['qualifier']] += 1
    return [{'batch': batch, **counts} for batch, counts in sorted(batches.items())]


def _censored_definitely_below(below_rows, value):
    # 真值 < 检测上限 <= value 时，该删失记录确定不大于 value
    return sum(1 for row in below_rows if row['value'] <= value)


def _censored_possibly_below(below_rows, above_rows, value):
    # 低于检测限的真值总可能不大于 value；高于检测限的真值仅当检测下限 < value 时可能
    return len(below_rows) + sum(1 for row in above_rows if row['value'] < value)


def _required_n_one_sided(p, confidence):
    return math.ceil(math.log(1.0 - confidence) / math.log(p))


def _required_n_two_sided(p, confidence):
    n = 2
    while n <= 1000000:
        if _coverage_confidence(n - 1, n, p) >= confidence:
            return n
        n += 1
    return None


def _claim_bound(exact, below_rows, above_rows, n, p, confidence, side):
    size = len(exact)
    result = {'side': side, 'requested': {'coverage': p, 'confidence': confidence}}
    if size == 0:
        result.update({'supported': False, 'bound': None, 'achieved_confidence': None,
                       'max_coverage_at_confidence': None, 'required_n_reference': None,
                       'explanation': '没有精确观测：检测限是检出边界而非真实读数，无法构造分布自由容忍界'})
        return result
    if side == 'upper':
        chosen = None
        for rank in range(1, size + 1):
            j2 = rank + _censored_definitely_below(below_rows, exact[rank - 1][0])
            if _coverage_confidence(j2, n, p) >= confidence:
                chosen = (rank, j2)
                break
        supported = chosen is not None
        rank, j2 = chosen if supported else (size, size + _censored_definitely_below(below_rows, exact[size - 1][0]))
        achieved = _coverage_confidence(j2, n, p)
        value, sample = exact[rank - 1]
        bound = {'upper': value, 'upper_sample': sample}
        max_coverage = _coverage_lower_bound(size + _censored_definitely_below(below_rows, exact[size - 1][0]),
                                             n, confidence)
        required = _required_n_one_sided(p, confidence)
    elif side == 'lower':
        chosen = None
        for rank in range(size, 0, -1):
            value = exact[rank - 1][0]
            worst_rank = rank + _censored_possibly_below(below_rows, above_rows, value)
            if _coverage_confidence(n - worst_rank + 1, n, p) >= confidence:
                chosen = (rank, worst_rank)
                break
        supported = chosen is not None
        if supported:
            rank, worst_rank = chosen
        else:
            rank = 1
            worst_rank = 1 + _censored_possibly_below(below_rows, above_rows, exact[0][0])
        achieved = _coverage_confidence(n - worst_rank + 1, n, p)
        value, sample = exact[rank - 1]
        bound = {'lower': value, 'lower_sample': sample}
        worst_at_min = 1 + _censored_possibly_below(below_rows, above_rows, exact[0][0])
        max_coverage = _coverage_lower_bound(n - worst_at_min + 1, n, confidence)
        required = _required_n_one_sided(p, confidence)
    else:
        if size < 2:
            result.update({'supported': False, 'bound': None, 'achieved_confidence': 0.0,
                           'max_coverage_at_confidence': None, 'required_n_reference': None,
                           'explanation': '精确观测不足两条，无法构造双侧分布自由容忍区间'})
            return result
        span = None
        for candidate in range(1, size):
            if _coverage_confidence(candidate, n, p) >= confidence:
                span = candidate
                break
        supported = span is not None
        if not supported:
            span = size - 1
        best_start, best_width = 0, None
        for start in range(0, size - span):
            width = exact[start + span][0] - exact[start][0]
            if best_width is None or width < best_width:
                best_start, best_width = start, width
        lower_value, lower_sample = exact[best_start]
        upper_value, upper_sample = exact[best_start + span]
        achieved = _coverage_confidence(span, n, p)
        bound = {'lower': lower_value, 'lower_sample': lower_sample,
                 'upper': upper_value, 'upper_sample': upper_sample}
        max_coverage = _coverage_lower_bound(size - 1, n, confidence)
        required = _required_n_two_sided(p, confidence)
    result.update({'supported': supported, 'bound': bound, 'achieved_confidence': achieved,
                   'max_coverage_at_confidence': max_coverage,
                   'required_n_reference': {'n': required,
                                            'basis': '全部观测精确且取极端次序统计量的最优情形'}})
    if supported:
        result['explanation'] = (
            '分布自由方法支持该声明：总体中至少 {:.4f} 的比例落在所示界限内，'
            '达成置信度 {:.4f}，不低于要求的 {:.4f}'.format(p, achieved, confidence))
    else:
        result['explanation'] = (
            '现有资料无法以置信度 {:.4f} 支持覆盖比例 {:.4f}：最宽可取界限的达成置信度仅 {:.4f}；'
            '可把覆盖比例降至约 {:.4f}，或增加独立样本（最优情形参考 n>={}）'.format(
                confidence, p, achieved, max_coverage, required))
    return result


def _normal_reference(exact, below_rows, above_rows, p, confidence, side):
    if below_rows or above_rows:
        return {'available': False,
                'reason': '存在检测限记录；不以检测限代替真实值，正态参考区间不计算'}
    size = len(exact)
    if size < 3:
        return {'available': False, 'reason': '精确观测不足 3 条，参数参考区间不稳定'}
    values = [value for value, _ in exact]
    spread = stdev(values)
    if spread == 0:
        return {'available': False, 'reason': '精确观测为常数，标准差为 0，正态参考区间无定义'}
    center = mean(values)
    if side == 'two-sided':
        nu = size - 1
        k_factor = math.sqrt(nu * (1 + 1 / size) * _norm_dist.ppf((1 + p) / 2) ** 2
                             / float(_chi2_dist.ppf(1 - confidence, nu)))
        bound = {'lower': center - k_factor * spread, 'upper': center + k_factor * spread}
        method = 'Howe 近似双侧正态容忍区间'
    else:
        k_factor = float(_nct_dist.ppf(confidence, size - 1, math.sqrt(size) * _norm_dist.ppf(p))) / math.sqrt(size)
        bound = {'upper': center + k_factor * spread} if side == 'upper' else {'lower': center - k_factor * spread}
        method = '非中心 t 单侧正态容忍界'
    skew = _skewness(values)
    risk = '假设敏感：偏斜或截断时实际覆盖可能明显偏离标称，不作为覆盖证据'
    if skew is not None and abs(skew) > 1:
        risk += '；精确观测偏度 {:.2f} 偏大，正态假设尤其可疑'.format(skew)
    return {'available': True, 'method': method, 'k_factor': k_factor, 'bound': bound,
            'assumption': '正态总体（未验证）', 'risk': risk,
            'note': '仅作对照，是否支持以分布自由结果为准'}


def _spec_check(rows, lower, upper, p, confidence, source):
    counts = Counter(_classify(row, lower, upper) for row in rows)
    within, outside, ambiguous = counts['within'], counts['outside'], counts['ambiguous']
    n = len(rows)
    lower_bound = _coverage_lower_bound(within, n, confidence)
    upper_bound = _coverage_upper_bound(within + ambiguous, n, confidence)
    result = {'spec': {'lower': lower, 'upper': upper, 'source': source},
              'within': within, 'outside': outside, 'ambiguous': ambiguous,
              'coverage_lower_bound': lower_bound, 'coverage_upper_bound': upper_bound,
              'confidence': confidence}
    if ambiguous:
        result['partial_identification'] = ('存在无法判定的检测限记录，覆盖比例只能部分识别：'
                                            '真值介于下界（只计确定在内）与上界（无法判定全算在内）之间')
    if p is not None:
        achieved = _coverage_confidence(within, n, p)
        result['requested_coverage'] = p
        result['achieved_confidence'] = achieved
        result['supported'] = achieved >= confidence
        if result['supported']:
            result['explanation'] = ('以置信度 {:.4f} 可断言规格覆盖比例至少为 {:.4f}，'
                                     '达到目标 {:.4f}'.format(confidence, lower_bound, p))
        else:
            result['explanation'] = ('规格覆盖比例达到 {:.4f} 的置信度仅 {:.4f}，低于要求的 {:.4f}；'
                                     '当前可支持的覆盖下界为 {:.4f}'.format(p, achieved, confidence, lower_bound))
    else:
        result['supported'] = None
        result['explanation'] = '以置信度 {:.4f}，规格覆盖比例至少 {:.4f}、至多 {:.4f}'.format(
            confidence, lower_bound, upper_bound)
    return result


def _tolerance_item(data, attribute, mode, coverage, confidence, side, overrides):
    spec = data['specifications'][attribute]
    rows = selected_readings(data, attribute)
    exact = sorted((row['value'], row['sample']) for row in rows if row['qualifier'] == 'exact')
    below_rows = [row for row in rows if row['qualifier'] == 'below']
    above_rows = [row for row in rows if row['qualifier'] == 'above']
    item = {'attribute': attribute, 'unit': spec['unit'],
            'n_total': len(rows), 'n_exact': len(exact),
            'n_below_limit': len(below_rows), 'n_above_limit': len(above_rows),
            'per_batch': _per_batch(rows),
            'exact_skewness': _skewness([value for value, _ in exact])}
    if below_rows or above_rows:
        item['censoring_note'] = '检测限记录不作为实际读数；秩次与计数按最不利情形保守处理'
    if mode == 'claim':
        item['claim'] = _claim_bound(exact, below_rows, above_rows, len(rows), coverage, confidence, side)
        item['normal_reference'] = _normal_reference(exact, below_rows, above_rows, coverage, confidence, side)
        item['method'] = '分布自由次序统计量容忍界；检测限记录按最不利秩保守处理'
    else:
        override = overrides.get(attribute, {})
        lower = override.get('lower', spec['lower'])
        upper = override.get('upper', spec['upper'])
        item['spec_check'] = _spec_check(rows, lower, upper, coverage, confidence,
                                         'override' if attribute in overrides else 'study')
        item['method'] = '固定规格计数置信界（Clopper-Pearson）；检测限记录只按确定信息计数'
    item['assumptions'] = ['样品相互独立且来自同一批次混合总体', '不假设分布形状']
    return item


def _joint_spec(data, attributes, bounds_by_attribute, coverage, confidence):
    samples = {}
    for attribute in attributes:
        lower, upper = bounds_by_attribute[attribute]
        for row in selected_readings(data, attribute):
            entry = samples.setdefault(row['sample'], {})
            status = _classify(row, lower, upper)
            entry[attribute] = _combine_status(entry[attribute], status) if attribute in entry else status
    counts = Counter()
    missing = 0
    for entry in samples.values():
        statuses = [entry.get(attribute, 'missing') for attribute in attributes]
        if 'missing' in statuses:
            missing += 1
        if 'outside' in statuses:
            counts['outside'] += 1
        elif all(status == 'within' for status in statuses):
            counts['within'] += 1
        else:
            counts['ambiguous'] += 1
    n = len(samples)
    within, ambiguous = counts['within'], counts['ambiguous']
    lower_bound = _coverage_lower_bound(within, n, confidence)
    upper_bound = _coverage_upper_bound(within + ambiguous, n, confidence)
    shares = []
    for attribute in attributes:
        lower, upper = bounds_by_attribute[attribute]
        rows = selected_readings(data, attribute)
        share = sum(1 for row in rows if _classify(row, lower, upper) == 'within') / len(rows)
        shares.append(share)
    product = math.prod(shares)
    result = {'type': '多维联合覆盖：同一样品同时满足全部规格',
              'attributes': attributes,
              'n_samples': n, 'definitely_within_all': within,
              'definitely_outside_any': counts['outside'], 'ambiguous': ambiguous,
              'samples_with_missing_attributes': missing,
              'joint_coverage_lower_bound': lower_bound,
              'joint_coverage_upper_bound': upper_bound,
              'confidence': confidence,
              'product_of_marginal_within_shares': product,
              'method': '同一样品计数，不假设维度独立',
              'warnings': ['边际覆盖份额相乘仅在维度独立时成立；相关维度下该乘积不是联合覆盖的证据，此处仅作对照',
                           '联合覆盖下界按同一样品同时满足全部规格计数，不假设维度独立']}
    if coverage is not None:
        achieved = _coverage_confidence(within, n, coverage)
        result['requested_coverage'] = coverage
        result['achieved_confidence'] = achieved
        result['supported'] = achieved >= confidence
    return result


def _simultaneous(items, mode, coverage, confidence):
    required = 1 - (1 - confidence) / len(items)
    entries = []
    for item in items:
        block = item['claim'] if mode == 'claim' else item['spec_check']
        achieved = block.get('achieved_confidence')
        entries.append({'attribute': item['attribute'], 'achieved_confidence': achieved,
                        'meets_required': None if achieved is None else achieved >= required})
    known = [entry['achieved_confidence'] for entry in entries if entry['achieved_confidence'] is not None]
    lower_bound = None
    if len(known) == len(entries):
        lower_bound = max(0.0, 1 - sum(1 - value for value in known))
    return {'type': '多项同时成立（Bonferroni）',
            'required_confidence_per_item': required,
            'items': entries,
            'simultaneous_confidence_lower_bound': lower_bound,
            'note': ('逐项成立不蕴含同时成立：要求每项声明置信度至少 1-(1-置信度)/项数，'
                     '或改用联合计数；相关维度不影响该下界，但会影响边际覆盖相乘的合法性')}


def tolerance(data, request):
    mode = request.get('mode')
    if mode not in ('claim', 'spec'):
        raise ValueError('未知容忍分析模式')
    attributes = request.get('attributes')
    if not isinstance(attributes, list) or not attributes or len(set(attributes)) != len(attributes):
        raise ValueError('属性列表应为一至多个不重复属性')
    for attribute in attributes:
        if attribute not in data['specifications']:
            raise ValueError('未知规格属性')
    confidence = _probability(request.get('confidence'), '置信程度')
    coverage = request.get('coverage')
    if coverage is not None:
        coverage = _probability(coverage, '覆盖比例')
    if mode == 'claim' and coverage is None:
        raise ValueError('正向声明模式需要覆盖比例')
    side = request.get('side', 'two-sided')
    if side not in SIDES:
        raise ValueError('未知区间侧别')
    overrides = {}
    raw_overrides = request.get('specs') or {}
    if not isinstance(raw_overrides, dict):
        raise ValueError('规格覆盖应为对象')
    for attribute, bounds in raw_overrides.items():
        if attribute not in attributes:
            raise ValueError('规格覆盖的属性未在分析列表中')
        if not isinstance(bounds, dict):
            raise ValueError('规格覆盖应为对象')
        spec = data['specifications'][attribute]
        lower = bounds.get('lower', spec['lower'])
        upper = bounds.get('upper', spec['upper'])
        for value in (lower, upper):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('规格界限应为有限数值')
        if not lower < upper:
            raise ValueError('规格下限应小于上限')
        overrides[attribute] = {'lower': float(lower), 'upper': float(upper)}

    items = [_tolerance_item(data, attribute, mode, coverage, confidence, side, overrides)
             for attribute in attributes]
    batch_ids = '、'.join(batch['id'] for batch in data['batches'])
    result = {'operation': 'tolerance', 'mode': mode,
              'target_population': '批次混合总体（{}合并）；批次为生产批次而非随机样本，结论不外推至未观测批次'.format(batch_ids),
              'requested': {'attributes': attributes, 'coverage': coverage, 'confidence': confidence,
                            'side': side if mode == 'claim' else None},
              'items': items,
              'notice': '容忍声明面向总体覆盖比例，与均值置信区间、个体预测范围是不同承诺；逐项、同时与联合不可混用'}
    if len(attributes) > 1:
        result['simultaneous'] = _simultaneous(items, mode, coverage, confidence)
        if mode == 'spec':
            bounds = {}
            for attribute in attributes:
                spec = overrides.get(attribute, data['specifications'][attribute])
                bounds[attribute] = (spec['lower'], spec['upper'])
            result['joint'] = _joint_spec(data, attributes, bounds, coverage, confidence)
    caveats = ['声明目标是批次混合总体；批次为生产批次而非随机样本，结论不外推至未观测批次',
               '检测限记录（below/above）是检出边界而非实际读数，按最不利情形保守计数',
               '容忍声明针对总体覆盖比例，与均值置信区间、个体预测范围是不同承诺，不可互换']
    skewed = [item['attribute'] for item in items
              if item['exact_skewness'] is not None and abs(item['exact_skewness']) > 1]
    if skewed:
        caveats.append('属性 {} 的精确观测偏度较大：偏斜分布下正态类强分布假设的承诺尤其不可靠，'
                       '分布自由结果不受影响'.format('、'.join(skewed)))
    if mode == 'claim' and any(not item['claim']['supported'] for item in items):
        caveats.append('存在当前样本量无法支持的声明：小样本下分布自由置信度受 1-p**n 量级限制，'
                       '需降低覆盖比例或增加样本，不应强行缩窄区间')
    if len(attributes) > 1:
        caveats.append('逐项成立不蕴含同时成立；相关维度下边际覆盖相乘不是联合覆盖证据，'
                       '联合承诺以同一样品计数为准')
    result['caveats'] = caveats
    return result
