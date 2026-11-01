"""Persist each simulated source event once, including its business and error."""
import random
from datetime import datetime, timedelta, timezone

# Each source opens the round with the business that has a default channel.
FIRST_BUSINESS = {'polestar': '기업회계', 'jobpass': '금융거래'}

# (operation, team name, JOB-PASS team number); iteration order is display order.
CATALOG = {
    'CMS': ('출금결과수집', 'IT운영팀', '02'),
    '자동이체통합관리': ('예약이체처리', 'IT운영팀', '02'),
    '온누리상품권정보교환': ('상품권거래정보교환', 'IT운영팀', '02'),
    '네트워크론': ('대출실행결과연계', '결제개발팀', '05'),
    '기업회계': ('트랜잭션 수신처리', 'IT운영팀', '02'),
    '금융거래': ('집계생성', 'IT운영팀', '02'),
}
BUSINESSES = tuple(name for name in CATALOG if name not in FIRST_BUSINESS.values())
OPERATIONS = {business: spec[0] for business, spec in CATALOG.items()}


def business_path(source, business):
    _, team, number = CATALOG[business]
    if source == 'polestar':
        return f'/{team}/{business}'
    if source == 'jobpass':
        return f'/{number}. {team}/01. 서비스/{business}'
    raise ValueError('Unknown source')


# These messages describe synthetic failures. They perform no banking operations.
SCENARIOS = {
    'polestar': (
        ('java.net.SocketException', 'Broken pipe (Write failed) · {business} 전문 송신 중 연결이 종료되었습니다.', 12),
        ('java.net.SocketTimeoutException', 'Read timed out · {business} 응답 전문 대기시간을 초과했습니다.', 12),
        ('java.net.SocketException', 'Connection reset · {business} 거래 응답 수신 중 연결이 초기화되었습니다.', 12),
        ('java.net.ConnectException', 'Connection refused · {business} 연계 서비스에 연결하지 못했습니다.', 12),
        ('java.sql.SQLTransientConnectionException', 'Connection pool exhausted · {business} 처리 결과 저장용 DB 연결 대기시간을 초과했습니다.', 16),
        ('java.sql.SQLRecoverableException', 'IO Error · {business} 처리 결과를 조회하던 중 DB 세션이 종료되었습니다.', 16),
    ),
    'jobpass': (
        ('SQLSTATE 08006', 'Connection timeout · {business} 처리 결과 저장 중 DB 연결 시간이 초과되었습니다.', 12),
        ('SQLSTATE 08003', 'Connection does not exist · {business} 결과 조회 중 DB 세션 연결이 끊어졌습니다.', 12),
        ('SQLSTATE 40001', 'Transaction rollback · {business} 결과 반영 중 동시 갱신 충돌이 발생했습니다.', 16),
        ('SQLSTATE 57014', 'Query timeout · {business} 처리 대상 조회시간이 초과되어 작업이 중단되었습니다.', 12),
        ('EAI_TIMEOUT', 'Read timed out · {business} 연계 전문의 응답을 받지 못했습니다.', 8),
        ('EAI_CONN_RESET', 'Connection reset · {business} 결과 전문 수신 중 연결이 초기화되었습니다.', 8),
    ),
}


def make_event(source, event_id, created_at, *, previous_severity=None, first_for_source=False):
    business = FIRST_BUSINESS[source] if first_for_source else random.choice(BUSINESSES)
    code, template, return_code = random.choice(SCENARIOS[source])
    operation = OPERATIONS[business]
    task_name = business + '_' + operation
    message = code + ': ' + template.format(business=business)
    if source == 'jobpass':
        message = 'STEP02 ' + task_name + ' 실행오류 / ' + message + ' / RETURN CODE ' + str(return_code)
    event = {
        'event_id': event_id, 'job_name': business, 'business_name': business,
        'operation': operation, 'task_name': task_name,
        'group_path': business_path(source, business),
        'message': message, 'created_at': created_at,
    }
    if source == 'polestar':
        event['severity'] = 'major' if previous_severity == 'critical' else 'critical'
    if source == 'jobpass':
        when = datetime.fromisoformat(created_at).astimezone(timezone(timedelta(hours=9)))
        started = when - timedelta(seconds=15)
        stamp = when.strftime('%Y/%m/%d %H:%M:%S')
        event['execution_log'] = '\n'.join((
            '[' + started.strftime('%Y/%m/%d %H:%M:%S') + '] JOB ' + task_name + ' START',
            '실행 ID: ' + event_id,
            '업무 경로: ' + event['group_path'],
            'STEP01 처리 대상 확인 result OK',
            'STEP02 ' + operation + ' starting',
            '[' + stamp + '] ' + message,
            'JOB ' + task_name + ' end - code: ERROR',
            '※ 외부 시연용으로 작성한 가상 실행 로그입니다.',
        ))
    return event
