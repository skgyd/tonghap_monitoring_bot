"""Collector and channel settings for the server-side contest environment."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
URL="http://mattermost:8065"
def configuration(real,channels=None,owner=None,admin=None):
 c=json.loads((ROOT/'config.example.json').read_text());c.update(mode='live' if real else 'demo',local_lab=True,trusted_lan_http=True,callback_port=8767,poll_seconds=2)
 m=c['mattermost'];m.update(url=URL,callback_base='http://bridge:8767',monitor_channel=channels['관제팀']['id'] if real else 'demo-monitor',allowed_user_ids=[owner['id'],admin['id']] if real else [],admin_user_ids=[admin['id']] if real else [],callback_allowed_ips=['127.0.0.0/8','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'])
 for name,s in c['collectors'].items():
  s.update(enabled=True,url='http://127.0.0.1:8765/mock/'+name,headless=True,browser_channel='',ready_selector='#ready',row_selector='#rows > tr',id_attribute='data-event-id',job_selector='.job',message_selector='.message',business_from_path=True,business_delimiter='/',business_segment=-1,business_strip_numeric_prefix=False,single_page_complete=True,allow_absence_detection=True,section_selector='')
  if name=='jobpass':s.update(detail_selector='#popup',detail_ready_selector='#detail-id',detail_identity_selector='#detail-id',detail_click='dblclick',close_selector='#close',detail_required_selectors=['#execution-log'],detail_nonempty_selectors=['#detail-id','#detail-log'],detail_extra_capture_selectors=['#execution-log'])
 return c
