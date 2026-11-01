"""Configurable DOM capture adapter. Real selectors must be validated on-site."""
import threading,time,hashlib,shutil,json,re
from pathlib import Path
from .store import uid

class Collector(threading.Thread):
 def __init__(self,engine,root,source,config,stop):
  super().__init__(daemon=True,name=source);self.e=engine;self.root=Path(root);self.source=source;self.c=config;self.stop=stop
 def scope(self,page):return page.frame_locator(self.c['iframe']) if self.c.get('iframe') else page
 def list_scope(self,page):
  s=self.scope(page)
  return s.locator(self.c['section_selector']) if self.c.get('section_selector') else s
 def read_row(self,r):
  c=self.c
  def text(sel):return r.locator(sel).inner_text().strip() if sel else ''
  fields={key:text(sel) for key,sel in c.get('field_selectors',{}).items() if sel}
  eid=r.get_attribute(c['id_attribute']) if c.get('id_attribute') else text(c.get('id_selector'))
  if not eid and c.get('event_key_fields'):
   keys=c['event_key_fields']
   if any(not fields.get(key) for key in keys):raise ValueError('Identity fields missing; do not use row numbers or elapsed time')
   eid=hashlib.sha256(json.dumps([fields[key] for key in keys],ensure_ascii=False).encode()).hexdigest()
  job=text(c.get('job_selector')) or fields.get('path') or fields.get('system') or fields.get('job_name','')
  if c.get('business_from_path'):
   parts=[part.strip() for part in job.split(c.get('business_delimiter','/')) if part.strip()]
   segment=c.get('business_segment',-1)
   if not parts or not -len(parts)<=segment<len(parts):raise ValueError('Business path segment missing')
   job=parts[segment]
   if c.get('business_strip_numeric_prefix'):job=re.sub(r'^[A-Za-z]?\d+[.\s_-]+','',job).strip()
   if not job:raise ValueError('Business name is empty')
  msg=text(c.get('message_selector')) or fields.get('message') or r.inner_text().strip()
  extra=' | '.join(f'{key}: {value}' for key,value in fields.items() if key!='message')
  if extra:msg+='\n'+extra
  return eid,job,msg
 def open_detail(self,r):
  target=r.locator(self.c['detail_link']) if self.c.get('detail_link') else r
  if self.c.get('detail_click','click')=='dblclick':target.dblclick()
  else:target.click()
 def wait_detail(self,scope):
  for selector in self.c.get('detail_required_selectors',[]):
   scope.locator(selector).wait_for(state='visible')
  for selector in self.c.get('detail_nonempty_selectors',[]):
   locator=scope.locator(selector)
   locator.wait_for(state='visible')
   deadline=time.monotonic()+self.c.get('detail_wait_seconds',20)
   while not locator.inner_text().strip():
    if self.stop.wait(0.2):raise RuntimeError('Stopping')
    if time.monotonic()>deadline:raise TimeoutError('Detail text not loaded')
 def run(self):
  try:
   from playwright.sync_api import sync_playwright
   with sync_playwright() as p:
    args={'headless':self.c.get('headless',False),'no_viewport':True,'args':['--window-size=850,900','--window-position='+('0,0' if self.source=='polestar' else '850,0')],'accept_downloads':False}
    if self.c.get('browser_channel'):args['channel']=self.c['browser_channel']
    context=p.chromium.launch_persistent_context(str(self.root/'profiles'/self.source),**args)
    page=context.pages[0] if context.pages else context.new_page();page.set_default_timeout(10000);page.goto(self.c['url'])
    while not self.stop.is_set():
     try:self.scan(page)
     except Exception as ex:
      # No full-page diagnostic screenshot: it might capture credentials.
      try:self.e.health(self.source,'ERROR',f'{type(ex).__name__}: 로그인·화면 선택자·팝업 설정 확인')
      except Exception:pass
     self.stop.wait(self.e.config['poll_seconds'])
    context.close()
  except Exception as ex:
   try:self.e.health(self.source,'STOPPED',f'{type(ex).__name__}: Playwright/브라우저 설치 및 설정 확인')
   except Exception:pass
 def scan(self,page):
  s=self.scope(page);c=self.c
  if shutil.disk_usage(self.root).free<self.e.config['min_free_mb']*1024**2:raise OSError('Low disk space')
  s.locator(c['ready_selector']).wait_for(state='visible')
  if c.get('loading_selector') and s.locator(c['loading_selector']).is_visible():raise ValueError('Still loading')
  if c.get('first_page_selector'):
   first=s.locator(c['first_page_selector'])
   if first.is_enabled():first.click();page.wait_for_timeout(c.get('settle_ms',500))
  seen=set();finished=False;page_signatures=set()
  for page_number in range(c.get('max_pages',20)):
   rows=self.list_scope(page).locator(c['row_selector']); count=rows.count(); events=[]
   for index in range(count):
    r=rows.nth(index)
    eid,job,msg=self.read_row(r)
    if not eid:raise ValueError('Event ID is missing. Configure an immutable execution/event ID.')
    if eid in seen:raise ValueError('Duplicate event ID or overlapping pagination; absence check skipped')
    seen.add(eid);events.append((eid,job,msg))
   signature=tuple(e[0] for e in events)
   if signature in page_signatures and page_number:raise ValueError('Pagination did not advance')
   page_signatures.add(signature)
   for eid,job,msg in events:
    if self.stop.is_set():return
    snapshot=self.e.store.snapshot()
    current=next((x for x in snapshot['incidents'] if x['source']==self.source and x['source_event_id']==eid),None)
    if current and current['capture_status']=='OK':continue
    saved={item['capture_type'] for item in snapshot['captures'] if current and item['incident_id']==current['incident_id']}
    # Re-resolve by stable key; never click the old row index after sorting.
    matches=[]
    for n in range(rows.count()):
     r=rows.nth(n); key,_,_=self.read_row(r)
     if key==eid:matches.append(r)
    if len(matches)!=1:raise ValueError('Event moved or disappeared')
    r=matches[0]
    if 'LIST' not in saved:
     relative=f'captures/{uid()}.png';path=self.root/relative
     try:
      masks=[s.locator(sel) for sel in c.get('mask_selectors',[])]
      r.screenshot(path=str(path),mask=masks)
      self.e.ingest(self.source,eid,job,msg,relative,'LIST')
     except Exception:
      path.unlink(missing_ok=True)
      self.e.ingest(self.source,eid,job,msg);continue
    if self.source=='jobpass':
     detail_page=page
     try:
      if c.get('detail_mode','modal')=='popup':
       with page.expect_popup() as info:self.open_detail(r)
       detail_page=info.value;detail_scope=detail_page
      else:
       self.open_detail(r);detail_scope=s
      panel=detail_scope.locator(c['detail_selector']);panel.wait_for(state='visible')
      if c.get('detail_ready_selector'):detail_scope.locator(c['detail_ready_selector']).wait_for(state='visible')
      self.wait_detail(detail_scope)
      expected=eid if c.get('detail_identity','event')=='event' else job
      identity=detail_scope.locator(c['detail_identity_selector']).inner_text().strip()
      if identity!=expected:raise ValueError('Wrong detail identity')
      masks=[detail_scope.locator(sel) for sel in c.get('detail_mask_selectors',[])]
      if 'DETAIL' not in saved:
       rel=f'captures/{uid()}.png';panel.screenshot(path=str(self.root/rel),mask=masks)
       self.e.ingest(self.source,eid,job,msg,rel,'DETAIL')
      for index,selector in enumerate(c.get('detail_extra_capture_selectors',[])):
       kind=f'DETAIL_EXTRA_{index+1}'
       if kind in saved:continue
       extra=detail_scope.locator(selector);extra.scroll_into_view_if_needed()
       rel=f'captures/{uid()}.png';extra.screenshot(path=str(self.root/rel),mask=masks)
       self.e.ingest(self.source,eid,job,msg,rel,kind)
      # Optional explicitly configured failing node. No color-based inference.
      if c.get('error_selector') and 'ERROR_LOCATION' not in saved:
       node=detail_scope.locator(c['error_selector'])
       if node.count()==1:
        node.scroll_into_view_if_needed();rel=f'captures/{uid()}.png';panel.screenshot(path=str(self.root/rel),mask=masks)
        self.e.ingest(self.source,eid,job,msg,rel,'ERROR_LOCATION')
     finally:
      if detail_page is not page:detail_page.close()
      elif c.get('close_selector') and s.locator(c['close_selector']).is_visible():s.locator(c['close_selector']).click()
   next_sel=c.get('next_page_selector')
   if not next_sel:finished=bool(c.get('single_page_complete',False));break
   nxt=s.locator(next_sel)
   if not nxt.count() or not nxt.is_enabled() or nxt.get_attribute('aria-disabled')=='true':finished=True;break
   nxt.click();page.wait_for_timeout(c.get('settle_ms',500))
  self.e.reconcile(self.source,seen,complete=finished and c.get('allow_absence_detection',False))
  self.e.health(self.source,'OK',f'{len(seen)}건 조회 / 전체 목록 확인: {finished}')
