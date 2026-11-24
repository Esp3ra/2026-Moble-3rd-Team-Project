# 계정 표시 이름 길이 변경

2026-11-23 사용자 결정: 계정 생성 요청과 응답의 `display_name` 상한을 모두 128자로 통일한다. Flask 검증과 WPF 입력란에 반영했다. DB 스키마 변경은 필요하지 않으며 기존 계정은 유지한다.

확인한 GitHub main OpenAPI(`f805bfddf7e404364ce2a65b6d7ae6a548da989b`)는 `AccountCreate.display_name` 128자, `Account.display_name` 100자로 서로 다르다. 공통 문서 담당자는 응답 `Account.display_name.maxLength`도 128로 맞춰야 한다. 이번 작업에서는 원격 문서를 다운로드하거나 GitHub 문서를 수정하지 않았다.

`scripts/check_openapi.py`는 원격 명세를 메모리에서 읽고 이 결정 한 건만 명시적으로 적용해 검사한다. 변경 사실을 출력하며, 이를 수정 전 main 명세와 완전히 일치한 결과로 보고하지 않는다. 서버 경계 검사는 100·101·128자 생성 및 로그인/재조회, 129자 거절과 동일 요청 재전송을 확인한다.

ngrok URL, 토큰, Pi 폴링, USB 시리얼 전문, 펌웨어, DB 이력은 이번 변경 대상이 아니다. 실행 중인 서버와 장비 연결은 재시작하지 않는다. 서버 변경은 다음 서버 시작부터, WPF 변경은 새 빌드 실행부터 적용된다.
