'use strict';
const el = id => document.getElementById(id);
if (location.hash.startsWith('#token=')) {
  sessionStorage.clear();
  sessionStorage.setItem('operator', location.hash.slice(7));
  history.replaceState(null, '', location.pathname);
}
const token = sessionStorage.getItem('operator') || '';
let ready = false, busy = false, stopping = false;
const phases = {starting:'환경 확인', copying:'이번 회차 준비',booting:'내부 서버 시작',
  provisioning:'계정·채널 준비', collectors:'화면 수집기 준비',ready:'시연 준비 완료',
  stopping:'서버 종료 중',cleanup:'이번 회차 정리 중',error:'시연 오류'};
async function api(path, body) {
  const options = {headers:{'X-Launcher-Token':token},cache:'no-store'};
  if (body !== undefined) {
    options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);
  }
  const response=await fetch(path,options);
  const data=await response.json();
  if (!response.ok) throw Object.assign(new Error(data.error || '운영자 창에서 다시 연결하세요.'),{status:response.status});
  return data;
}
function buttons() {
  const open = ready && !stopping;
  for (const link of document.querySelectorAll('[data-view]')) {
    if (open) link.href='/operator-sources/'+link.dataset.view;
    else link.removeAttribute('href');
    link.tabIndex=open ? 0 : -1;
    link.setAttribute('aria-disabled',String(!open));
  }
  el('view-help').textContent=open ? '화면이 새 탭에서 열립니다. 오류를 추가하려면 아래 ‘오류 발생’을 누르세요.' :
    stopping ? '서버를 종료하는 중입니다.' : '시연 준비가 끝나면 아래에서 원본 화면을 열 수 있습니다.';
  for(const key of ['polestar','jobpass']) el(key).disabled=!ready||busy||stopping;
  el('stop').disabled=!token||stopping;
}
async function fire(source) {
  if (busy||!ready||stopping) return;
  busy=true;buttons();
  const key='pending:'+source, id=sessionStorage.getItem(key)||crypto.randomUUID();
  sessionStorage.setItem(key,id);el('result').textContent='오류를 원본에 추가하고 있습니다.';
  try {
    const result=await api('/api/operator/events',{source,request_id:id});
    if(result.event_id!==id || result.source!==source) throw new Error('응답을 확인할 수 없습니다.');
    sessionStorage.removeItem(key);
    el('result').textContent=(source==='polestar'?'폴스타':'잡패스')+'에 등록했습니다. 이벤트 '+id;
  } catch(e) {
    if (e.status===400||e.status===409||e.status===415) sessionStorage.removeItem(key);
    el('result').textContent=e.message+' 같은 버튼을 눌러 다시 시도하세요.';
  } finally {busy=false;buttons();}
}
async function update() {
  try {
    const data=await api('/api/status');
    if (stopping) return;
    ready=data.ready;
    el('phase').textContent=phases[data.phase]||data.phase;el('message').textContent=data.message;
    el('elapsed').textContent='시작 후 '+Math.floor(data.elapsed/60)+'분 '+data.elapsed%60+'초';
    el('error').textContent=data.error;
    el('judges').hidden=!data.accounts?.length;
    el('mm-ready').hidden=!ready;el('mm-wait').hidden=ready;
    if(ready) el('mm').href=data.links.mattermost;
    else el('mm').removeAttribute('href');
    if(!data.accounts?.length) el('accounts').replaceChildren();
    else if (!el('accounts').childElementCount) {
      el('accounts').replaceChildren(...data.accounts.map(a=>{
        const tr=document.createElement('tr');
        for(const s of [a.username,a.password]){const td=tr.insertCell();td.textContent=s;}return tr;
      }));
    }
  } catch(e){ready=false;el('judges').hidden=true;el('accounts').replaceChildren();el('error').textContent=stopping?'서버를 종료하고 회차를 정리하는 중입니다.':e.message;}
  buttons();if(!stopping)setTimeout(update,1000);
}
for(const name of ['polestar','jobpass'])el(name).onclick=()=>fire(name);
el('stop').onclick=async()=>{
  if(!confirm('서버를 종료할까요? 이번 시연 기록과 채널 변경 사항이 삭제됩니다.'))return;
  stopping=true;ready=false;buttons();el('judges').hidden=true;el('accounts').replaceChildren();
  try{await api('/api/stop',{});el('phase').textContent='이번 회차 정리 중';el('message').textContent='정리가 끝나면 운영자 창이 닫힙니다.';}
  catch(e){stopping=false;el('error').textContent=e.message;buttons();update();}
};
update();
