# 초기 협업 환경 설정 기록

설정일: 2026-10-29 (한국 시간)

## 저장소와 로컬 작업 위치

- 원본: https://github.com/Esp3ra/2026-Moble-3rd-Team-Project
- 관리자: Esp3ra
- 관리자 로컬: `C:\Users\cos31\Desktop\ES\3차 팀 프로젝트`
- Codex 프로젝트: 기존 ES 프로젝트를 사용하고 위 하위 저장소에서 Git 작업 수행
- 팀원 계정·기술 스택·기능 분담: 아직 미정

초기 협업 파일은 [PR #1](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/pull/1)에서
검토·정책 CI 성공 후 main에 반영했습니다. 초기 공통 커밋은
`5e9456b8520c27bf8054b05836308ef14b263617`이며 Alpha·Beta·Release는 이 커밋에서 시작했습니다.
그 이후 설정 기록을 작업 브랜치 → Alpha(PR #2), Alpha → Beta(PR #3), Beta → Release(PR #4)로
반영하여 각 PR의 정책 CI 성공과 보호 규칙 아래의 정상 병합 경로를 확인했습니다.

## 실제 등록한 GitHub 규칙

| 규칙 | 적용 범위 | 내용 |
| --- | --- | --- |
| [stage-pr-and-checks](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/rules/26107386) | main·Alpha·Beta·Release | 팀원은 PR·repository-policy 성공·최신 base 검사·리뷰 대화 해결 필요; OWNER 일반 push 예외 |
| [owner-stage-updates](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/rules/26107390) | main·Alpha·Beta·Release | Esp3ra가 일반 push 또는 PR 병합으로 업데이트; 팀원은 작업 브랜치에만 push |
| [promotion-merge-commits](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/rules/26107391) | Beta·Release | PR 승격은 Merge commit, OWNER 일반 push 예외 |
| [branch-history-protection](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/rules/26107973) | main·Alpha·Beta·Release | OWNER를 포함하여 강제 push·삭제 차단 |
| [manager-only-tag-creation](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/rules/26107394) | 태그 전체 | Esp3ra만 생성 |
| [immutable-version-tags](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/rules/26107396) | 태그 전체 | 기존 태그의 이동·삭제 차단 |

최초에는 관리자도 PR 안에서만 업데이트하도록 설정했으며, 2026-10-29 OWNER 요청으로 일반 push 예외를 추가했습니다.
예외 대상은 OWNER Esp3ra 계정 하나이며 업데이트·PR·사전 CI 규칙을 통과할 수 있습니다.
별도 이력 보호 규칙에는 우회 계정이 없어 강제 push와 삭제는 여전히 차단됩니다.
일반 push 뒤에도 정책 CI가 실행됩니다. 이 예외를 실제 검사 통과의 증거로 표현하지 않습니다.
사람 승인 수는 0명으로 시작하여 관리자 본인 PR의 자기 승인 문제를 피합니다.
실제 적용 상태는 GitHub Rules 화면과 각 브랜치의 활성 규칙에서 다시 확인할 수 있습니다.

## 브랜치의 앞섬·뒤처짐 표시

OWNER 예외 변경 직전에는 main 기준 Alpha가 1개, Beta가 2개, Release가 3개 **앞서 있었고**,
세 브랜치가 뒤처진 커밋은 모두 0개였습니다. 설정 기록 추가와 두 단계의 병합 커밋 때문에 생긴 차이입니다.
세 단계의 파일 내용은 동일했으며, 커밋 개수는 기능의 완성도나 오류 수를 의미하지 않습니다.

OWNER 예외와 공통 안내는 제품 코드가 없는 초기 설정 기준에서 한 번 수정해 네 브랜치에 반영합니다.
기존 커밋과 병합 이력을 보존하는 fast-forward만 사용하며 제품 버전 태그는 생성하지 않습니다.
이후 각 단계에서 개발·검토가 진행되면 앞선 커밋과 뒤처진 커밋 수는 다시 달라질 수 있습니다.

저장소의 Merge commit·Squash 병합을 허용하고 Rebase 병합은 비활성화했습니다.
단계 브랜치를 보존하도록 자동 브랜치 삭제는 비활성화했으며 기본 브랜치는 main으로 유지했습니다.

## 검증 근거와 범위

- 초기 로컬 정책 회귀 검사 12개와 정책·diff 검사를 통과했습니다.
- [PR #1 정책 CI](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/actions/runs/40840442147)가 성공했습니다.
- 초기 main·Alpha·Beta·Release push의 정책 CI 성공과 네 브랜치의 보호 상태를 API로 확인했습니다.
- 설정 기록 반영과 단계 승격은 각 PR의 최신 커밋에 대한 CI를 확인한 뒤 관리자가 병합합니다.
- 제품 코드가 없어 제품 빌드·기능·DB·공유 서버·장치·다른 PC 시험은 수행하지 않았습니다.
- 초기 브랜치 준비와 설정 기록 승격은 제품의 Beta·정식 출시 완료를 의미하지 않으며, 제품 태그·배포 Release를 생성하지 않았습니다.

팀원은 GitHub 아이디가 정해진 뒤 초대합니다. 초대 수락과 실제 팀원 계정의 push·PR 시험은 그 이후 확인합니다.
