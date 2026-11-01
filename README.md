# tonghap_monitoring_bot

바코챌 · MonitoringBot 모니터링봇 프로그램

Windows 11 x64에서 서버와 수집기를 함께 실행한다. 운영자 웹에서 폴스타/잡패스 화면을 직접 열고 시연 오류를 생성하며, 심사위원은 브라우저의 Mattermost·폴스타·잡패스에서 확인한다. 모든 시연 데이터는 회차 단위이며 정상 종료 후 폐기된다.

실행자는 최종 ZIP 안의 README_KO.txt를 따른다. Windows 서비스 구성은 windows/portable_bootstrap.py, 소유 회차 정리는 windows/demo_sessions.py, 운영 화면은 windows/web_launcher, 사건 처리·수집·채널 명령은 app 아래에 있다.

## 빌드

소스에는 Windows Python/QEMU, 초기 게스트 이미지 같은 대용량 구성품이 별도로 필요하다. monitoringbot-part1.zip과 monitoringbot-part2.zip을 같이 사용한다. 같은 상위 폴더에 모두 풀고 monitoringbot/monitoringbot.exe를 실행한다.

1. 개발용 Python에서 `python release/make_release.py build`를 실행해 최신 코드를 포함하는 생성기를 만든다.
2. `python build/build_zip.py inputs`는 두 원본에서 배포 대상 파일을 선택하고 해시를 검증해 Windows 전체 ZIP을 만든다. Windows에서 추가 Python 설치 없이 만들려면 두 원본을 생성기 옆에 두고 MAKE-ZIP.cmd를 실행한다.
3. 최종 ZIP을 새 폴더에 풀어 검수한다. monitoringbot 폴더 안의 monitoringbot.exe를 실행하고 운영자 웹에서 버전 1.4.6을 확인한다.

## 시험

app 폴더에서 `python -m unittest discover -s tests -v`. 묶음 내부에서는 실행 폴더에서 번들 Python으로 같은 명령을 실행할 수 있다. 원본 화면 브라우저 시험에는 Playwright Chromium이 필요하다. Windows의 VERIFY-WINDOWS.cmd는 두 회차를 새로 시작해 실제 Mattermost 및 브라우저를 검사한다. 실행 전에 기존 시연을 종료해야 한다. 이 검사는 사용자 시연 중인 회차에는 붙지 않는다.

요구사항별 판정은 REQUIREMENTS-TRACE.md, 검증 환경 구분은 VERIFICATION.txt에 있다. 사내 실사이트의 URL·DOM·봇 권한·콜백 경로는 승인된 현장에서 별도 확인한다.

## 현재 시연 업무

각 회차의 폴스타 첫 오류는 기업회계, 잡패스 첫 오류는 금융거래입니다. 이후 오류는 CMS, 자동이체통합관리, 온누리상품권정보교환, 네트워크론 중 하나와 원본별 6가지 오류 문구 중 하나로 생성합니다.

| 업무 | 폴스타 팀 경로 | 잡패스 팀 경로 |
| --- | --- | --- |
| CMS | IT운영팀 | 02. IT운영팀 |
| 자동이체통합관리 | IT운영팀 | 02. IT운영팀 |
| 온누리상품권정보교환 | IT운영팀 | 02. IT운영팀 |
| 네트워크론 | 결제개발팀 | 05. 결제개발팀 |

운영자 웹에서 원본 화면 열기, 오류 발생, Mattermost 접속, 사용법 확인, 서버 종료를 진행합니다. Mattermost에서는 업무별 채널 매핑과 사건 이관, 접수·완료, 답글·이모지 동기화, 중복 억제 및 실패한 전송 재시도를 수행합니다.

## 개발 준비

개발용 Python 환경에서 의존성을 설치합니다. Playwright를 사용하는 브라우저 테스트에는 Chromium이 필요합니다.

```sh
python -m pip install -r requirements-server.txt
python -m playwright install chromium
cd app
python -m unittest discover -s tests -v
```

브라우저 시험을 포함하려면 마지막 명령 실행 전에 `BRIDGE_BROWSER_TEST=1`을 설정합니다. PowerShell에서는 `$env:BRIDGE_BROWSER_TEST='1'`을 사용합니다.

Windows용 Python, QEMU 및 Mattermost·PostgreSQL 초기 이미지는 배포용 `monitoringbot-part1.zip`과 `monitoringbot-part2.zip`에서 가져옵니다. 개발 시 두 ZIP을 저장소의 `inputs` 폴더에 두고 위 빌드 절차를 실행합니다. 소스만 내려받은 상태에서 `windows/monitoringbot.exe`를 실행하면 이 구성품들이 추가로 필요합니다.

프로그램 설명서는 [PDF](windows/monitoringbot.pdf)와 [편집용 Word](windows/monitoringbot.docx), 실행 절차는 [Windows 사용법](windows/README_KO.txt)에서 확인할 수 있습니다.
