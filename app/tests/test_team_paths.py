import threading
import unittest
from unittest.mock import patch
from app import replica_page
from bridge.collector import Collector
from demo_events import SCENARIOS, make_event
from demo_launcher import configuration
from test_screen_fields import Node


class TeamPathTests(unittest.TestCase):
    teams = {
        'CMS': ('IT운영팀','02. IT운영팀'),
        '자동이체통합관리': ('IT운영팀','02. IT운영팀'),
        '온누리상품권정보교환': ('IT운영팀','02. IT운영팀'),
        '네트워크론': ('결제개발팀','05. 결제개발팀'),
    }

    def test_paths_match_source_rows_running_jobs_and_collector_routing_key(self):
        jobs=replica_page('jobpass').decode()
        for business,(polestar,jobpass) in self.teams.items():
            for source,path in [('polestar',f'/{polestar}/{business}'),
                                ('jobpass',f'/{jobpass}/01. 서비스/{business}')]:
                with self.subTest(source=source,business=business):
                    with patch('demo_events.random.choice',side_effect=[business,SCENARIOS[source][0]]):
                        event=make_event(source,'id','2026-11-01T00:00:00+00:00')
                    self.assertEqual(event['group_path'],path)
                    if source=='jobpass':
                        self.assertIn('업무 경로: '+path,event['execution_log'].splitlines())
                        self.assertIn('<td>'+path+'</td>',jobs)
                    config=configuration(False)['collectors'][source]
                    row=Node({'data-event-id':'id','.job':path,'.message':event['message']})
                    collector=Collector(None,'.',source,config,threading.Event())
                    self.assertEqual(collector.read_row(row),('id',business,event['message']))
