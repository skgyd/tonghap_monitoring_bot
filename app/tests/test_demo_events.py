import unittest
from unittest.mock import patch
from demo_events import BUSINESSES, SCENARIOS, make_event


class DemoEventTests(unittest.TestCase):
    def test_every_business_and_scenario_preserves_selected_details(self):
        expected = {'CMS', '자동이체통합관리', '온누리상품권정보교환', '네트워크론'}
        self.assertEqual(set(BUSINESSES), expected)
        for source, scenarios in SCENARIOS.items():
            for business in BUSINESSES:
                messages = set()
                for scenario in scenarios:
                    with self.subTest(source=source, business=business, code=scenario[0]):
                        with patch('demo_events.random.choice', side_effect=[business, scenario]) as choose:
                            event = make_event(source, 'stable-id', '2026-11-01T00:05:00+00:00')
                        self.assertEqual(choose.call_args_list[0].args[0], BUSINESSES)
                        self.assertEqual(choose.call_args_list[1].args[0], SCENARIOS[source])
                        self.assertEqual(event['event_id'], 'stable-id')
                        self.assertEqual(event['job_name'], business)
                        self.assertIn(business, event['task_name'])
                        self.assertIn(business, event['message'])
                        messages.add(event['message'])
                        if source == 'jobpass':
                            self.assertIn(event['message'], event['execution_log'])
                            self.assertIn('2026/11/01 09:05:00', event['execution_log'])
                            self.assertGreater(len(event['execution_log'].splitlines()), 4)
                self.assertEqual(len(messages), len(scenarios))
