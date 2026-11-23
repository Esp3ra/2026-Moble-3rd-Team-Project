"""현장 Raspberry Pi의 LINE 연결 모듈 자리.

서버의 /device/v1 명령과 조회를 받고, command_id와 UNO의 세션·회차·순번을
대응시킨다. 완료 회신은 같은 생산 회차의 이벤트로 전달한다.
"""
