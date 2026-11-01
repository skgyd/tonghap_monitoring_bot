"""Browser regression for the two source panes. Run with BRIDGE_BROWSER_TEST=1."""
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app import Server
from hosted import SourceHandler, CollectorHandler


@unittest.skipUnless(os.environ.get('BRIDGE_BROWSER_TEST') == '1', 'Requires Playwright Chromium')
class SourceBrowserTests(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.source = SimpleNamespace(mock_lock=threading.Lock(), mock={'polestar': [], 'jobpass': []},
                                      config={'mode':'demo'})
        self.public = self.serve(SourceHandler)
        self.collector = self.serve(CollectorHandler)
        playwright = sync_playwright().start()
        self.addCleanup(playwright.stop)
        self.browser = playwright.chromium.launch()
        self.addCleanup(self.browser.close)

    def serve(self, handler):
        server = Server(('127.0.0.1', 0), handler)
        server.application = self.source
        server.api = Mock()
        server.api.call.return_value = ({'id':'judge'}, '')
        server.settings = {'judges':{'monitoring01':'judge'}}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:' + str(server.server_port)

    def context(self, **options):
        context = self.browser.new_context(**options)
        self.addCleanup(context.close)
        context.add_cookies([{'name':'MMAUTHTOKEN','value':'test','url':self.public}])
        return context

    def test_panels_without_javascript(self):
        page = self.context(java_script_enabled=False).new_page()
        page.goto(self.public + '/ib-sources/both')
        for source, marker in [('polestar', '#polestar'), ('jobpass', '#running')]:
            frame = page.frame_locator('iframe[src$="/' + source + '"]')
            frame.locator(marker).wait_for()
            frame.locator('.source-warning').wait_for()
            self.assertEqual(frame.locator('#ready').count(), 0)

    def test_chrome_global_errors_and_live_recovery(self):
        from playwright.sync_api import expect
        context = self.context()
        # Match a browser which reserves the window.chrome name in classic scripts.
        context.add_init_script("""
            if (!Object.hasOwn(window,'chrome') || Object.getOwnPropertyDescriptor(window,'chrome').configurable)
                Object.defineProperty(window,'chrome',{value:window.chrome || {},configurable:false});
        """)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.goto(self.public + '/ib-sources/both')
        alarm = page.frame_locator('iframe[src$="/polestar"]')
        job = page.frame_locator('iframe[src$="/jobpass"]')
        for pane in (alarm, job):
            expect(pane.locator('#ready')).to_be_visible()
            expect(pane.locator('#count')).to_have_text('0')
        expect(job.locator('#running tr')).to_have_count(4)
        expect(job.locator('.top-status')).to_contain_text('실행중(4)')
        for name in ('polestar', 'jobpass'):
            with self.source.mock_lock:
                self.source.mock[name].append({'event_id':name+'-1','message':'DEMO','created_at':'2026-10-01T00:00:00Z'})
        expect(alarm.locator('#rows tr .message')).to_have_text('DEMO')
        expect(alarm.locator('.major-chip')).to_have_text('major')
        with self.source.mock_lock:
            self.source.mock['polestar'].append({'event_id':'polestar-2','severity':'critical',
                'message':'CRITICAL','created_at':'2026-10-01T00:00:01Z'})
        expect(alarm.locator('#critical-count')).to_have_text('1')
        expect(alarm.locator('#count')).to_have_text('1')
        expect(alarm.locator('#range')).to_have_text('(1~2 / 2)')
        critical = alarm.locator('tr[data-event-id="polestar-2"]')
        expect(critical.locator('.critical-chip')).to_have_text('critical')
        expect(critical.locator('td').last).to_have_css('background-color', 'rgb(248, 198, 213)')
        with self.source.mock_lock:
            self.source.mock['polestar'].pop()
        expect(alarm.locator('#critical-count')).to_have_text('0')
        job.locator('#rows tr').dblclick()
        expect(job.locator('#detail-id')).to_have_text('jobpass-1')
        expect(job.locator('#execution-log')).to_be_visible()
        job.locator('#close').click()

        data_url = self.public + '/ib-sources/data/polestar'
        page.route(data_url, lambda route: route.fulfill(status=503, body='{}'))
        expect(alarm.locator('#sync-status')).to_contain_text('자동으로 다시 조회')
        expect(alarm.locator('#ready')).to_have_count(0)
        expect(alarm.locator('#rows tr')).to_have_count(1)
        page.unroute(data_url)
        expect(alarm.locator('#ready')).to_be_visible()
        page.route(data_url, lambda route: route.fulfill(status=401, body='{}'))
        expect(alarm.locator('#sync-status')).to_contain_text('다시 로그인')
        expect(alarm.locator('#critical-count')).to_have_text('…')
        expect(alarm.locator('#rows tr')).to_have_count(0)
        page.unroute(data_url)
        expect(alarm.locator('#rows tr')).to_have_count(1)
        with self.source.mock_lock:
            self.source.mock['polestar'].clear()
        expect(alarm.locator('#count')).to_have_text('0')
        expect(alarm.locator('#rows tr')).to_have_count(0)
        for name in ('polestar','jobpass'):
            page.goto(self.collector + '/mock/' + name)
            expect(page.locator('#ready')).to_be_visible()
        self.assertEqual(errors, [])


    def test_random_rows_detail_and_collector_fields_agree(self):
        from unittest.mock import patch
        from playwright.sync_api import expect
        from demo_events import BUSINESSES, SCENARIOS, make_event
        from bridge.collector import Collector
        from demo_launcher import configuration
        selected = {}
        for source in ('polestar', 'jobpass'):
            selected[source] = []
            for index, business in enumerate(BUSINESSES):
                scenario = SCENARIOS[source][index % len(SCENARIOS[source])]
                with patch('demo_events.random.choice', side_effect=[business, scenario]):
                    selected[source].append(make_event(source, source + str(index), '2026-11-01T00:00:00+00:00'))
        with self.source.mock_lock:
            self.source.mock.update(selected)
        context = self.context()
        page = context.new_page()
        for source in ('polestar', 'jobpass'):
            page.goto(self.public + '/ib-sources/' + source)
            expect(page.locator('#rows > tr')).to_have_count(len(selected[source]))
            collector = Collector(None, '.', source, configuration(False)['collectors'][source], threading.Event())
            for event in selected[source]:
                row = page.locator('[data-event-id="' + event['event_id'] + '"]')
                expect(row.locator('.job')).to_have_text(event['group_path'])
                self.assertEqual(collector.read_row(row), (event['event_id'], event['job_name'], event['message']))
                if source == 'jobpass':
                    row.dblclick()
                    expect(page.locator('#detail-id')).to_have_text(event['event_id'])
                    expect(page.locator('#detail-job')).to_contain_text(event['task_name'])
                    expect(page.locator('#detail-log')).to_contain_text(event['message'])
                    expect(page.locator('#detail-business')).to_have_text(event['business_name'])
                    expect(page.locator('#detail-path')).to_have_text(event['group_path'])
                    page.locator('#close').click()
            page.reload()
            expect(page.locator('#rows > tr')).to_have_count(len(selected[source]))
            self.assertEqual(collector.read_row(page.locator('#rows > tr').first)[2], selected[source][0]['message'])
        # Legacy persisted events keep their original path-derived routing key.
        with self.source.mock_lock:
            self.source.mock['jobpass'] = [{'event_id':'old', 'job_name':'BATCH_DEMO',
                'message':'legacy error', 'created_at':'2026-10-26T00:00:00Z'}]
        page.goto(self.collector + '/mock/jobpass')
        expect(page.locator('[data-event-id="old"]')).to_be_visible()
        legacy = collector.read_row(page.locator('[data-event-id="old"]'))
        self.assertEqual(legacy, ('old', '금융거래', 'legacy error'))
        page.locator('[data-event-id="old"]').dblclick()
        expect(page.locator('#detail-log')).to_contain_text('legacy error')
