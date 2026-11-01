(() => {
  'use strict';

  const source = document.body.dataset.source;
  const operatorView = location.pathname.startsWith('/operator-sources/');
  const dataUrl = operatorView ? '/operator-sources/data/' + source
    : location.pathname.startsWith('/ib-sources/') ? '/ib-sources/data/' + source : '/mock-data/' + source;
  const rows = document.querySelector('#rows');
  const list = document.querySelector('[data-source-list]');
  const status = document.querySelector('#sync-status');
  const detail = document.querySelector('#popup');
  let lastSnapshot;
  let loading = false;

  function cell(row, text, className) {
    const column = row.insertCell();
    column.textContent = text;
    if (className) column.className = className;
    return column;
  }

  function chip(row, text, className) {
    const label = document.createElement('span');
    label.textContent = text;
    label.className = className;
    row.insertCell().append(label);
  }

  function seoulTime(date) {
    return date.toLocaleString('sv-SE', { timeZone: 'Asia/Seoul' });
  }

  function eventBusiness(event) {
    if (event.business_name) return event.business_name;
    // Older saved demo rows used technical job keys with fixed screen paths.
    if (event.job_name && !['BATCH_DEMO', 'ONLINE_DEMO'].includes(event.job_name)) return event.job_name;
    return source === 'polestar' ? '기업회계' : '금융거래';
  }

  function eventPath(event, business) {
    if (event.group_path) return event.group_path;
    // Preserve how pre-catalog records were originally displayed.
    return (source === 'polestar' ? '/IT운영/' : '/02. IT운영/01. 서비스/') + business;
  }

  function alarmRow(row, event, severity) {
    const business = eventBusiness(event);
    row.dataset.severity = severity;
    chip(row, severity, severity + '-chip');
    cell(row, '로그관리');
    cell(row, seoulTime(new Date(event.created_at)));
    cell(row, '20초');
    cell(row, eventPath(event, business), 'job path');
    cell(row, '[demo-was1] ' + business + ' 관리서비스');
    cell(row, 'demo-was1');
    cell(row, '192.0.2.21');
    cell(row, event.operation || '트랜잭션 수신처리');
    cell(row, business + ' 서비스 /log/app ERROR');
    cell(row, event.message, 'message');
    chip(row, '미확인', 'status-chip');
    cell(row, '');
  }

  function showDetail(event, business, taskName) {
    const date = seoulTime(new Date(event.created_at));
    document.querySelector('#detail-job').textContent = '▣ ' + taskName;
    document.querySelector('#detail-task').textContent = taskName;
    document.querySelector('#detail-business').textContent = business;
    document.querySelector('#detail-path').textContent = eventPath(event, business);
    document.querySelector('#detail-date').textContent = date;
    document.querySelector('#detail-operation').textContent = event.operation || taskName;
    document.querySelector('#detail-side-task').textContent = taskName;
    document.querySelector('#detail-log').textContent = event.execution_log || [
      '[' + date + '] JOB ' + taskName,
      '실행 ID: ' + event.event_id,
      event.message,
      '※ 외부 시연용으로 작성한 가상 실행 로그입니다.',
    ].join('\n');
    document.querySelector('#detail-id').textContent = event.event_id;
    detail.hidden = false;
  }

  function jobRow(row, event, index) {
    const business = eventBusiness(event);
    const taskName = event.task_name || business + '_집계생성(영업일)';
    cell(row, index + 1);
    cell(row, eventPath(event, business), 'job');
    cell(row, taskName, 'task-name');
    const task = cell(row, event.operation || '집계생성');
    const message = document.createElement('div');
    message.className = 'message';
    message.textContent = event.message;
    task.append(message);
    cell(row, '⚑');
    cell(row, '⊗ 실행오류', 'fail');
    cell(row, '');
    row.ondblclick = () => showDetail(event, business, taskName);
  }

  function showStatus(message) {
    if (status.textContent !== message) status.textContent = message;
    status.hidden = !message;
  }

  function render(events) {
    const snapshot = JSON.stringify(events);
    if (lastSnapshot !== snapshot) {
      const items = document.createDocumentFragment();
      let criticalCount = 0;
      events.forEach((event, index) => {
        if (!event || !event.event_id) throw new Error('Missing event ID');
        const row = document.createElement('tr');
        row.dataset.eventId = event.event_id;
        if (source === 'polestar') {
          const severity = event.severity === 'critical' ? 'critical' : 'major';
          if (severity === 'critical') criticalCount++;
          alarmRow(row, event, severity);
        } else jobRow(row, event, index);
        items.append(row);
      });
      rows.replaceChildren(items);
      document.querySelector('#count').textContent = events.length - criticalCount;
      if (source === 'polestar') {
        document.querySelector('#critical-count').textContent = criticalCount;
        document.querySelector('#range').textContent = events.length
          ? '(1~' + events.length + ' / ' + events.length + ')' : '(0 / 0)';
      }
      lastSnapshot = snapshot;
    }
    showStatus(events.length ? '' : (source === 'polestar' ? '현재 발생한 알람이 없습니다.' : '현재 실행오류가 없습니다.'));
    // Collectors may treat an empty list as resolved only after a successful read.
    list.id = 'ready';
  }

  async function refresh() {
    if (loading || !detail.hidden) return;
    loading = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(dataUrl, {cache: 'no-store', signal: controller.signal});
      if (response.status === 401 || response.status === 403) {
        list.removeAttribute('id');
        rows.replaceChildren();
        lastSnapshot = undefined;
        document.querySelector('#count').textContent = '…';
        if (source === 'polestar') {
          document.querySelector('#critical-count').textContent = '…';
          document.querySelector('#range').textContent = '';
        }
        showStatus(operatorView ? '이번 회차의 운영자 웹에서 화면을 다시 여세요.' : '같은 브라우저에서 Mattermost 심사 계정으로 다시 로그인한 뒤 /ib 화면을 여세요.');
        return;
      }
      if (!response.ok) throw new Error('Source unavailable');
      const events = await response.json();
      if (!Array.isArray(events)) throw new Error('Expected an event list');
      render(events);
    } catch {
      list.removeAttribute('id');
      showStatus('실시간 목록을 갱신하지 못했습니다. 서버 연결을 확인하세요. 자동으로 다시 조회합니다.');
    } finally {
      clearTimeout(timer);
      loading = false;
    }
  }

  function tick() {
    for (const clock of document.querySelectorAll('.clock')) clock.textContent = seoulTime(new Date());
    refresh();
  }

  document.querySelector('#close').onclick = () => {
    detail.hidden = true;
    refresh();
  };
  tick();
  setInterval(tick, 1000);
})();
