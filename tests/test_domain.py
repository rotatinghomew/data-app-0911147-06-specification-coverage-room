import copy
import json
import math
import unittest
from pathlib import Path

from app import domain
from app.service import load_study

ROOT = Path(__file__).resolve().parents[1]


def binom_cdf(k, n, p):
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def coverage_confidence(count, n, p):
    return binom_cdf(count - 1, n, p)


def cp_lower(count, n, confidence):
    if count == 0:
        return 0.0
    low, high = 0.0, 1.0
    for _ in range(120):
        mid = (low + high) / 2
        if 1 - binom_cdf(count - 1, n, mid) > 1 - confidence:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def load_cases():
    raw = (ROOT / 'data/analysis-cases.json').read_text(encoding='utf-8')
    return {case['name']: case['study'] for case in json.loads(raw)}


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.data = load_study()
        self.request = {'operation': 'summary', 'attribute': 'strength'}

    def test_detection_boundary_not_counted_as_exact(self):
        data = copy.deepcopy(self.data)
        data['measurements'] = [{'attribute': 'strength', 'unit': 'N', 'value': 100,
                                  'qualifier': 'below', 'batch': 'b1'}]
        result = domain.act(data, self.request)
        self.assertEqual(result['exact_count'], 0)
        self.assertIsNone(result['exact_mean'])
        self.assertEqual(result['bounded_count'], 1)

    def test_exact_spec_inclusive(self):
        data = copy.deepcopy(self.data)
        spec = data['specifications']['strength']
        data['measurements'] = [dict(data['measurements'][0], attribute='strength', unit='N',
                                    qualifier='exact', value=value) for value in [spec['lower'], spec['upper']]]
        self.assertEqual(domain.act(data, self.request)['exact_within_spec'], 2)

    def test_unknown_attribute(self):
        with self.assertRaises(ValueError):
            domain.act(self.data, {'operation': 'summary', 'attribute': 'unknown'})


class ToleranceClaimTests(unittest.TestCase):
    def setUp(self):
        self.data = load_study()
        self.cases = load_cases()

    def claim(self, data, attributes, coverage=0.95, confidence=0.95, side='two-sided'):
        return domain.act(data, {'operation': 'tolerance', 'mode': 'claim', 'attributes': attributes,
                                 'coverage': coverage, 'confidence': confidence, 'side': side})

    def test_order_statistic_cases_match_distribution_free_formula(self):
        for name, size in (('order_statistic_10', 10), ('order_statistic_59', 59)):
            result = self.claim(self.cases[name], ['strength'], side='upper')
            claim = result['items'][0]['claim']
            self.assertAlmostEqual(claim['achieved_confidence'], 1 - 0.95 ** size, places=12)
            self.assertEqual(claim['bound']['upper'], 40.0 + (size - 1) * 0.1)
        small = self.claim(self.cases['order_statistic_10'], ['strength'], side='upper')['items'][0]['claim']
        self.assertFalse(small['supported'])
        self.assertEqual(small['required_n_reference']['n'], 59)
        large = self.claim(self.cases['order_statistic_59'], ['strength'], side='upper')['items'][0]['claim']
        self.assertTrue(large['supported'])

    def test_all_censored_claim_refuses_to_certify(self):
        result = self.claim(self.cases['all_censored'], ['strength'], side='upper')
        claim = result['items'][0]['claim']
        self.assertFalse(claim['supported'])
        self.assertIsNone(claim['bound'])
        self.assertIn('检测限', claim['explanation'])

    def test_all_censored_spec_keeps_partial_identification(self):
        result = domain.act(self.cases['all_censored'],
                            {'operation': 'tolerance', 'mode': 'spec', 'attributes': ['strength'],
                             'coverage': 0.95, 'confidence': 0.95})
        check = result['items'][0]['spec_check']
        self.assertEqual(check['within'], 0)
        self.assertEqual(check['ambiguous'], 99)
        self.assertEqual(check['outside'], 1)
        self.assertEqual(check['coverage_lower_bound'], 0.0)
        self.assertLess(check['coverage_upper_bound'], 1.0)
        self.assertEqual(check['achieved_confidence'], 0.0)
        self.assertFalse(check['supported'])
        self.assertIn('partial_identification', check)

    def test_constant_case_degenerates_cleanly(self):
        result = self.claim(self.cases['constant_exact'], ['strength'], coverage=0.5, confidence=0.9, side='upper')
        item = result['items'][0]
        self.assertIsNone(item['exact_skewness'])
        self.assertFalse(item['normal_reference']['available'])
        claim = item['claim']
        self.assertTrue(claim['supported'])
        self.assertEqual(claim['bound']['upper'], 7.5)
        self.assertAlmostEqual(claim['achieved_confidence'], 1 - 0.5 ** 5, places=12)

    def test_above_censoring_not_counted_as_value(self):
        result = self.claim(self.cases['above_censoring'], ['strength'], coverage=0.9, confidence=0.8,
                            side='upper')
        item = result['items'][0]
        self.assertEqual(item['n_above_limit'], 2)
        claim = item['claim']
        self.assertFalse(claim['supported'])
        self.assertAlmostEqual(claim['achieved_confidence'], coverage_confidence(8, 10, 0.9), places=12)
        spec = domain.act(self.cases['above_censoring'],
                          {'operation': 'tolerance', 'mode': 'spec', 'attributes': ['strength'],
                           'coverage': 0.9, 'confidence': 0.95})['items'][0]['spec_check']
        self.assertEqual((spec['within'], spec['ambiguous'], spec['outside']), (8, 2, 0))

    def test_default_strength_upper_supported_with_censored_rank(self):
        claim = self.claim(self.data, ['strength'], side='upper')['items'][0]['claim']
        self.assertTrue(claim['supported'])
        self.assertEqual(claim['bound'], {'upper': 70.3729, 'upper_sample': 's-69'})
        self.assertAlmostEqual(claim['achieved_confidence'], coverage_confidence(99, 100, 0.95), places=12)

    def test_default_strength_lower_uses_worst_case_rank(self):
        claim = self.claim(self.data, ['strength'], side='lower')['items'][0]['claim']
        self.assertTrue(claim['supported'])
        self.assertEqual(claim['bound'], {'lower': 31.8058, 'lower_sample': 's-6'})
        self.assertAlmostEqual(claim['achieved_confidence'], coverage_confidence(99, 100, 0.95), places=12)

    def test_default_strength_two_sided_infeasible_due_to_censoring(self):
        claim = self.claim(self.data, ['strength'])['items'][0]['claim']
        self.assertFalse(claim['supported'])
        self.assertEqual(claim['bound']['lower'], 31.8058)
        self.assertEqual(claim['bound']['upper'], 73.5655)
        self.assertAlmostEqual(claim['achieved_confidence'], coverage_confidence(98, 100, 0.95), places=12)
        self.assertAlmostEqual(claim['max_coverage_at_confidence'], cp_lower(98, 100, 0.95), places=9)
        self.assertEqual(claim['required_n_reference']['n'], 93)

    def test_default_thickness_two_sided_supported_at_full_range(self):
        claim = self.claim(self.data, ['thickness'])['items'][0]['claim']
        self.assertTrue(claim['supported'])
        self.assertEqual(claim['bound']['lower'], 1.4418)
        self.assertEqual(claim['bound']['upper'], 2.4407)
        self.assertAlmostEqual(claim['achieved_confidence'], coverage_confidence(99, 100, 0.95), places=12)

    def test_above_limit_record_worsens_lower_bound_rank(self):
        data = {'measurements': [
            {'sample': 'a', 'batch': 'b1', 'attribute': 'strength', 'unit': 'N', 'value': 10,
             'qualifier': 'above'},
            {'sample': 'b', 'batch': 'b1', 'attribute': 'strength', 'unit': 'N', 'value': 20,
             'qualifier': 'exact'},
            {'sample': 'c', 'batch': 'b1', 'attribute': 'strength', 'unit': 'N', 'value': 30,
             'qualifier': 'exact'},
            {'sample': 'd', 'batch': 'b1', 'attribute': 'strength', 'unit': 'N', 'value': 40,
             'qualifier': 'exact'}],
            'specifications': {'strength': {'unit': 'N', 'lower': 0, 'upper': 100}},
            'batches': [{'id': 'b1', 'date': '2026-10-01'}]}
        claim = self.claim(data, ['strength'], coverage=0.5, confidence=0.5, side='lower')['items'][0]['claim']
        self.assertEqual(claim['bound']['lower'], 20)
        self.assertAlmostEqual(claim['achieved_confidence'], coverage_confidence(3, 4, 0.5), places=12)

    def test_normal_reference_flagged_and_unavailable_under_censoring(self):
        strength = self.claim(self.data, ['strength'], side='upper')['items'][0]['normal_reference']
        self.assertFalse(strength['available'])
        self.assertIn('检测限', strength['reason'])
        thickness = self.claim(self.data, ['thickness'])['items'][0]['normal_reference']
        self.assertTrue(thickness['available'])
        self.assertIn('正态', thickness['assumption'])
        self.assertGreater(thickness['k_factor'], 0)
        self.assertIn('不作为覆盖证据', thickness['risk'])


class ToleranceSpecTests(unittest.TestCase):
    def setUp(self):
        self.data = load_study()

    def spec(self, attributes, coverage=0.95, confidence=0.95, specs=None):
        request = {'operation': 'tolerance', 'mode': 'spec', 'attributes': attributes,
                   'coverage': coverage, 'confidence': confidence}
        if specs:
            request['specs'] = specs
        return domain.act(self.data, request)

    def test_strength_spec_supported(self):
        check = self.spec(['strength'])['items'][0]['spec_check']
        self.assertEqual((check['within'], check['outside'], check['ambiguous']), (99, 1, 0))
        self.assertTrue(check['supported'])
        self.assertAlmostEqual(check['achieved_confidence'], coverage_confidence(99, 100, 0.95), places=12)
        self.assertAlmostEqual(check['coverage_lower_bound'], cp_lower(99, 100, 0.95), places=9)

    def test_thickness_spec_not_supported(self):
        check = self.spec(['thickness'])['items'][0]['spec_check']
        self.assertEqual((check['within'], check['outside'], check['ambiguous']), (95, 5, 0))
        self.assertFalse(check['supported'])
        self.assertAlmostEqual(check['achieved_confidence'], coverage_confidence(95, 100, 0.95), places=12)
        self.assertAlmostEqual(check['coverage_lower_bound'], cp_lower(95, 100, 0.95), places=9)

    def test_spec_override_changes_classification(self):
        check = self.spec(['strength'], specs={'strength': {'lower': 40, 'upper': 60}})['items'][0]['spec_check']
        self.assertEqual(check['spec']['source'], 'override')
        exact = [row['value'] for row in self.data['measurements']
                 if row['attribute'] == 'strength' and row['qualifier'] == 'exact']
        self.assertEqual(check['within'], sum(40 <= value <= 60 for value in exact))
        self.assertEqual(check['outside'], sum(not 40 <= value <= 60 for value in exact) + 1)

    def test_joint_counts_same_sample_not_product(self):
        result = self.spec(['strength', 'thickness'])
        joint = result['joint']
        self.assertEqual(joint['definitely_within_all'], 94)
        self.assertEqual(joint['definitely_outside_any'], 6)
        self.assertAlmostEqual(joint['achieved_confidence'], coverage_confidence(94, 100, 0.95), places=12)
        self.assertAlmostEqual(joint['joint_coverage_lower_bound'], cp_lower(94, 100, 0.95), places=9)
        self.assertAlmostEqual(joint['product_of_marginal_within_shares'], 0.99 * 0.95, places=12)
        self.assertFalse(joint['supported'])
        self.assertTrue(any('独立' in warning for warning in joint['warnings']))

    def test_simultaneous_requires_bonferroni_level(self):
        result = self.spec(['strength', 'thickness'])
        simultaneous = result['simultaneous']
        self.assertAlmostEqual(simultaneous['required_confidence_per_item'], 0.975, places=12)
        expected = max(0.0, 1 - (1 - coverage_confidence(99, 100, 0.95))
                       - (1 - coverage_confidence(95, 100, 0.95)))
        self.assertAlmostEqual(simultaneous['simultaneous_confidence_lower_bound'], expected, places=12)
        self.assertFalse(all(entry['meets_required'] for entry in simultaneous['items']))

    def test_spec_without_coverage_reports_bounds_only(self):
        check = self.spec(['strength'], coverage=None)['items'][0]['spec_check']
        self.assertIsNone(check['supported'])
        self.assertNotIn('achieved_confidence', check)
        self.assertAlmostEqual(check['coverage_lower_bound'], cp_lower(99, 100, 0.95), places=9)


class ToleranceValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = load_study()

    def assert_invalid(self, request):
        with self.assertRaises(ValueError):
            domain.act(self.data, request)

    def base(self):
        return {'operation': 'tolerance', 'mode': 'claim', 'attributes': ['strength'],
                'coverage': 0.95, 'confidence': 0.95}

    def test_rejects_bad_inputs(self):
        self.assert_invalid(dict(self.base(), mode='unknown'))
        self.assert_invalid(dict(self.base(), attributes=[]))
        self.assert_invalid(dict(self.base(), attributes=['strength', 'strength']))
        self.assert_invalid(dict(self.base(), attributes=['unknown']))
        self.assert_invalid(dict(self.base(), coverage=1.5))
        self.assert_invalid(dict(self.base(), coverage=0))
        self.assert_invalid(dict(self.base(), coverage='0.95'))
        self.assert_invalid(dict(self.base(), confidence=True))
        self.assert_invalid(dict(self.base(), side='middle'))
        claim = self.base()
        del claim['coverage']
        self.assert_invalid(claim)

    def test_rejects_bad_spec_overrides(self):
        request = dict(self.base(), mode='spec')
        self.assert_invalid(dict(request, specs={'unknown': {'lower': 1, 'upper': 2}}))
        self.assert_invalid(dict(request, specs={'strength': {'lower': 60, 'upper': 40}}))
        self.assert_invalid(dict(request, specs={'strength': {'lower': 'x'}}))

    def test_response_carries_method_assumptions_and_caveats(self):
        result = domain.act(self.data, self.base())
        self.assertEqual(result['operation'], 'tolerance')
        self.assertIn('批次混合总体', result['target_population'])
        item = result['items'][0]
        self.assertIn('分布自由', item['method'])
        self.assertTrue(item['assumptions'])
        self.assertTrue(result['caveats'])
        self.assertEqual(len(item['per_batch']), 4)


if __name__ == '__main__':
    unittest.main()
