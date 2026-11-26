# 하드웨어 공정과 통신 규약

공정과 통신 규약 원본은 `main/docs`에서 관리한다. Alpha의 장비 구현에는 아래 기준을 적용한다.

- [장비 구성과 12단계 공정](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/HARDWARE_WORKFLOW.md)
- [프로젝트 전체 팀 규약](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/프로젝트_전체규약_팀답변_공유본.md)
- [공통 통신 규약 v0.3.0과 DTO](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/통신규약/공통_통신규약과_DTO.md)
- [OpenAPI](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/blob/main/docs/통신규약/openapi.yaml)

IR1 정차 뒤 QR 배정과 조립을 진행한다. 조립 완료·대기 복귀 뒤 팔레트를 세워 둔 채 검사하고, 같은 회차의 합격이 DB에 저장된 뒤에만 IR2로 이송한다. 구현 및 배포 검증은 [통합 시험표](../tests/integration/SCENARIOS.md)를 따른다.
