"""Point-and-click DOM calibration in a dedicated, visible browser profile."""
import json,queue,threading,time
from pathlib import Path
PICKER=r'''({field})=>{
 window.__bridgePickField=field;
 if(window.__bridgePickInstalled)return;
 window.__bridgePickInstalled=true;
 function css(e){if(e.id)return '#'+CSS.escape(e.id);let a=[];while(e&&e.tagName!=='HTML'){let t=e.tagName.toLowerCase(),p=e.parentElement;if(!p)break;let siblings=[...p.children].filter(x=>x.tagName===e.tagName);if(siblings.length>1)t+=':nth-of-type('+(siblings.indexOf(e)+1)+')';a.unshift(t);e=p}return a.join(' > ')}
 document.addEventListener('click',async e=>{if(!window.__bridgePickField)return;
 if(e.target.matches('input[type=password]'))return;
 e.preventDefault();e.stopImmediatePropagation();
 let field=window.__bridgePickField;window.__bridgePickField=null;let el=e.target,cell=el.closest('td,th'),tr=el.closest('tr'),table=el.closest('table'),value={field,text:(el.innerText||'').trim().slice(0,500)};
 if(['path','message','identity'].includes(field)){
 if(!cell||cell.tagName!=='TD'||!tr||!table){await window.bridgePicked({error:'표의 데이터 셀을 선택하세요. canvas/이미지 표는 이 방식으로 읽을 수 없습니다.'});return}
 value.row_selector=':scope > '+tr.parentElement.tagName.toLowerCase()+' > tr:has(td)';value.section_selector=css(table);value.selector=':scope > '+cell.tagName.toLowerCase()+':nth-child('+([...tr.children].indexOf(cell)+1)+')';
 }else{if(field==='detail_selector')el=el.closest('dialog,[role=dialog],.modal,.window,.k-window')||el;value.selector=css(el)}
 el.style.outline='3px solid #078f8d';await window.bridgePicked(value);
 },true);
}'''
class Calibration(threading.Thread):
 def __init__(self,root,source,config):
  super().__init__(daemon=True);self.root=Path(root);self.source=source;self.config=config;self.commands=queue.Queue();self.result={};self.error='';self.ready=False
 def run(self):
  try:
   from playwright.sync_api import sync_playwright
   with sync_playwright() as p:
    c=p.chromium.launch_persistent_context(str(self.root/'profiles'/self.source),channel=self.config.get('browser_channel','msedge'),headless=False,no_viewport=True,args=['--window-size=850,900','--window-position='+('0,0' if self.source=='polestar' else '850,0')])
    def picked(source,value):
     if value.get('error'):self.error=value['error'];return
     frame=source['frame'];value['frame_url']=frame.url
     if value.get('field','').startswith('detail_'):value['detail_mode']='popup' if source['page']!=page_initial else 'modal'
     if frame!=source['page'].main_frame:
      value['iframe_required']=True;self.error='iframe 안의 선택입니다. 고급 설정에 iframe 선택자를 입력하세요.'
     self.result=value
    c.expose_binding('bridgePicked',picked)
    page=c.pages[0] if c.pages else c.new_page();page_initial=page;page.goto(self.config['url']);self.ready=True
    while True:
     page=c.pages[-1] if c.pages else None
     if page is None:break
     try:cmd=self.commands.get_nowait()
     except queue.Empty:cmd=None
     if cmd=='close':break
     if isinstance(cmd,dict):
      for frame in page.frames:frame.evaluate(PICKER,cmd)
     page.wait_for_timeout(100)
    c.close()
  except Exception as e:self.error=type(e).__name__+': '+str(e)[:250]
  finally:self.ready=False
 def close(self):self.commands.put('close');self.join(timeout=15)
