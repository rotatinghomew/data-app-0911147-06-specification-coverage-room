import copy
import unittest
from app import domain
from app.service import load_study


class DomainTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
