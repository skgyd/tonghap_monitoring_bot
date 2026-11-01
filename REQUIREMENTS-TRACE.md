# IncidentBridge 1.4.6 요구사항 추적

기능별 인수 계약은 동봉 명세에 있다. 아래 구현 경로는 소스판 기준이다. 최종 배포에서는 app/와 windows/ 내용이 실행 폴더에 놓인다. 1.4.3의 검증을 새 버전의 실기기 결과로 승계하지 않는다.

| ID | 기능 | 구현 | 시험 | 현재 증거 범위 |
|---|---|---|---|---|
| WIN-01 | 무설치 실행 | windows/portable_bootstrap.py, windows/demo_sessions.py, release/ | AT-01 | 구현 · Windows 전체 흐름 미검증 |
| WIN-02 | 로컬 서비스 구성 | windows/portable_bootstrap.py, windows/demo_sessions.py, release/ | AT-01 | 구현 · Windows 전체 흐름 미검증 |
| WIN-03 | 매 실행 독립 디스크 | windows/portable_bootstrap.py, windows/demo_sessions.py, release/ | AT-01, AT-15 | 구현 · Windows 전체 흐름 미검증 |
| WIN-04 | 기동 상태와 오류 진단 | windows/portable_bootstrap.py, windows/demo_sessions.py, release/ | AT-01, AT-02 | 구현 · Windows 전체 흐름 미검증 |
| WIN-05 | 한글 경로와 단일 인스턴스 | windows/portable_bootstrap.py, windows/demo_sessions.py, release/ | AT-02 | 구현 · Windows 전체 흐름 미검증 |
| WIN-06 | 실행 폴더 내 임시 영역 | windows/portable_bootstrap.py, windows/demo_sessions.py, release/ | AT-02, AT-14 | 구현 · Windows 전체 흐름 미검증 |
| WIN-07 | 시작 및 무저장 종료 | windows/portable_bootstrap.py, windows/demo_sessions.py, release/ | AT-02, AT-14, AT-15 | 구현 · Windows 전체 흐름 미검증 |
| WIN-08 | 배포 및 초기 상태 보호 | windows/portable_bootstrap.py, windows/demo_sessions.py, release/ | AT-01, AT-02, AT-14 | 구현 · Windows 전체 흐름 미검증 |
| UI-01 | 로그인과 원본 열기 | app/web/, app/hosted.py, windows/web_launcher/ | AT-03 | 구현 · Windows 전체 흐름 미검증 |
| UI-02 | 폴스타 알람 콘솔 | app/web/, app/hosted.py, windows/web_launcher/ | AT-03, AT-04 | 구현 · Windows 전체 흐름 미검증 |
| UI-03 | 잡패스 작업 모니터 | app/web/, app/hosted.py, windows/web_launcher/ | AT-03, AT-04 | 구현 · Windows 전체 흐름 미검증 |
| UI-04 | 잡패스 오류 상세 | app/web/, app/hosted.py, windows/web_launcher/ | AT-03, AT-04 | 구현 · Windows 전체 흐름 미검증 |
| UI-05 | 화면 갱신과 실패 | app/web/, app/hosted.py, windows/web_launcher/ | AT-03 | 구현 · Windows 전체 흐름 미검증 |
| GEN-01 | 운영자 웹에 통합 | windows/web_launcher/operator.js, windows/portable_bootstrap.py, app/demo_events.py, app/app.py | AT-04 | 구현 · Windows 전체 흐름 미검증 |
| GEN-02 | 시연자 자동 인증 | windows/web_launcher/operator.js, windows/portable_bootstrap.py, app/demo_events.py, app/app.py | AT-04, AT-05 | 관련 로컬 회귀시험 통과 · 게스트 연동 미검증 |
| GEN-03 | 무작위 원본 이벤트 | windows/web_launcher/operator.js, windows/portable_bootstrap.py, app/demo_events.py, app/app.py | AT-04 | 구현 · Windows 전체 흐름 미검증 |
| GEN-04 | 회차 내 재시도와 중복 방지 | windows/web_launcher/operator.js, windows/portable_bootstrap.py, app/demo_events.py, app/app.py | AT-05 | 관련 로컬 회귀시험 통과 · 게스트 연동 미검증 |
| GEN-05 | 원본과 사건 분리 | windows/web_launcher/operator.js, windows/portable_bootstrap.py, app/demo_events.py, app/app.py | AT-04, AT-05 | 구현 · Windows 전체 흐름 미검증 |
| GEN-06 | 발생 결과와 실패 | windows/web_launcher/operator.js, windows/portable_bootstrap.py, app/demo_events.py, app/app.py | AT-05 | 구현 · Windows 전체 흐름 미검증 |
| COL-01 | 독립 브라우저 수집 | app/bridge/collector.py, app/bridge/engine.py | AT-06 | 구현 · Windows 전체 흐름 미검증 |
| COL-02 | 원본별 설정 | app/bridge/collector.py, app/bridge/engine.py | AT-06 | 구현 · Windows 전체 흐름 미검증 |
| COL-03 | 안정적인 오류 식별 | app/bridge/collector.py, app/bridge/engine.py | AT-06 | 구현 · Windows 전체 흐름 미검증 |
| COL-04 | 업무명 추출 | app/bridge/collector.py, app/bridge/engine.py | AT-06 | 구현 · Windows 전체 흐름 미검증 |
| COL-05 | 전체 스캔·부재 | app/bridge/collector.py, app/bridge/engine.py | AT-07 | 구현 · Windows 전체 흐름 미검증 |
| COL-06 | 목록 증거 | app/bridge/collector.py, app/bridge/engine.py | AT-06 | 구현 · Windows 전체 흐름 미검증 |
| COL-07 | 잡패스 상세 증거 | app/bridge/collector.py, app/bridge/engine.py | AT-06 | 구현 · Windows 전체 흐름 미검증 |
| COL-08 | 수집기 건강 | app/bridge/collector.py, app/bridge/engine.py | AT-06, AT-07 | 구현 · Windows 전체 흐름 미검증 |
| RT-01 | 팀·채널·계정 준비 | app/commands.py, app/provision.py, app/bridge/engine.py | AT-08 | 구현 · Windows 전체 흐름 미검증 |
| RT-02 | 담당 매핑 조회·추가·변경·삭제 | app/commands.py, app/provision.py, app/bridge/engine.py | AT-08, AT-09 | 구현 · Windows 전체 흐름 미검증 |
| RT-03 | 새 채널 활용 | app/commands.py, app/provision.py, app/bridge/engine.py | AT-08 | 구현 · Windows 전체 흐름 미검증 |
| RT-04 | 미분류 처리 | app/commands.py, app/provision.py, app/bridge/engine.py | AT-08 | 구현 · Windows 전체 흐름 미검증 |
| RT-05 | 기존 사건 한 건 이관 | app/commands.py, app/provision.py, app/bridge/engine.py | AT-09 | 구현 · Windows 전체 흐름 미검증 |
| RT-06 | 이관 시 권한 | app/commands.py, app/provision.py, app/bridge/engine.py | AT-09 | 구현 · Windows 전체 흐름 미검증 |
| MM-01 | 사건 알림 게시 | app/bridge/mattermost.py | AT-08 | 구현 · Windows 전체 흐름 미검증 |
| MM-02 | 게시물 갱신·첨부 보존 | app/bridge/mattermost.py | AT-09, AT-10 | 구현 · Windows 전체 흐름 미검증 |
| MM-03 | 발송 실패·응답 유실 | app/bridge/mattermost.py | AT-11 | 구현 · Windows 전체 흐름 미검증 |
| WF-01 | 접수와 완료 | app/bridge/engine.py, app/bridge/mattermost.py | AT-10 | 구현 · Windows 전체 흐름 미검증 |
| WF-02 | 담당자 변경·재개 | app/bridge/engine.py, app/bridge/mattermost.py | AT-10 | 구현 · Windows 전체 흐름 미검증 |
| WF-03 | 충돌·이력 | app/bridge/engine.py, app/bridge/mattermost.py | AT-09, AT-10 | 구현 · Windows 전체 흐름 미검증 |
| WF-04 | 원본·사람 처리 상태 독립 | app/bridge/engine.py, app/bridge/mattermost.py | AT-07, AT-10 | 구현 · Windows 전체 흐름 미검증 |
| WF-05 | 지연 알림 | app/bridge/engine.py, app/bridge/mattermost.py | AT-10 | 구현 · Windows 전체 흐름 미검증 |
| SY-01 | 업무 채널 → 관제팀 답글 동기화 | app/bridge/activity.py | AT-12 | 구현 · Windows 전체 흐름 미검증 |
| SY-02 | 답글 편집·삭제·첨부 | app/bridge/activity.py | AT-12 | 구현 · Windows 전체 흐름 미검증 |
| SY-03 | 이모지 추가·취소 | app/bridge/activity.py | AT-12 | 구현 · Windows 전체 흐름 미검증 |
| SY-04 | 회차 내 인계·통신 재개 | app/bridge/activity.py | AT-12 | 구현 · Windows 전체 흐름 미검증 |
| OPS-01 | 관제팀 명령과 조회 | app/commands.py, app/bridge/store.py, app/app.py, windows/demo_sessions.py | AT-13 | 구현 · Windows 전체 흐름 미검증 |
| OPS-02 | 재시도·실패 시연 | app/commands.py, app/bridge/store.py, app/app.py, windows/demo_sessions.py | AT-11, AT-13 | 구현 · Windows 전체 흐름 미검증 |
| OPS-03 | 회차 내 데이터 일관성 | app/commands.py, app/bridge/store.py, app/app.py, windows/demo_sessions.py | AT-11, AT-13, AT-14 | 관련 로컬 회귀시험 통과 · 게스트 연동 미검증 |
| OPS-04 | 실행 중 진단과 종료 정리 | app/commands.py, app/bridge/store.py, app/app.py, windows/demo_sessions.py | AT-13, AT-14 | 구현 · Windows 전체 흐름 미검증 |
| OPS-05 | 운영 도구의 경계 | app/commands.py, app/bridge/store.py, app/app.py, windows/demo_sessions.py | AT-14 | 구현 · Windows 전체 흐름 미검증 |
| SEC-01 | 인증 경계 분리 | windows/portable_bootstrap.py, app/hosted.py, app/bridge/mattermost.py | AT-03, AT-05 | 관련 로컬 회귀시험 통과 · 게스트 연동 미검증 |
| SEC-02 | 명령·버튼 위조 방지 | windows/portable_bootstrap.py, app/hosted.py, app/bridge/mattermost.py | AT-13 | 구현 · Windows 전체 흐름 미검증 |
| SEC-03 | 공개 경계와 비밀 관리 | windows/portable_bootstrap.py, app/hosted.py, app/bridge/mattermost.py | AT-13 | 구현 · Windows 전체 흐름 미검증 |
| HOST-01 | 필수 · 모든 서버의 위치 | windows/portable_bootstrap.py, windows/demo_sessions.py | AT-15 | 구현 · Windows 전체 흐름 미검증 |
| HOST-02 | 필수 · 로컬 접속 | windows/portable_bootstrap.py, windows/demo_sessions.py | AT-15 | 구현 · Windows 전체 흐름 미검증 |
| HOST-03 | 필수 · 회차 격리 | windows/portable_bootstrap.py, windows/demo_sessions.py | AT-15 | 관련 로컬 회귀시험 통과 · 게스트 연동 미검증 |
| SITE-01 | 현장 · 실제 폴스타/잡패스 | app/config.example.json, app/bridge/collector.py, app/bridge/mattermost.py | AT-16 | 현장 조건부 · 실사이트 미검증 |
| SITE-02 | 현장 · 기존 Mattermost | app/config.example.json, app/bridge/collector.py, app/bridge/mattermost.py | AT-16 | 현장 조건부 · 실사이트 미검증 |
