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
그 이후 이 설정 기록을 일반 Alpha PR과 단계 승격 PR로 반영하여 보호 규칙 아래의 정상 병합 경로를 확인합니다.

## 실제 등록한 GitHub 규칙

| 규칙 | 적용 범위 | 내용 |
| --- | --- | --- |
| [stage-pr-and-checks](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/rules/26107386) | main·Alpha·Beta·Release | PR, repository-policy 성공, 최신 base 검사, 리뷰 대화 해결, 강제 push·삭제 차단 |
| [manager-only-stage-merges](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/rules/26107390) | main·Alpha·Beta·Release | Esp3ra가 PR을 통해 업데이트; 팀원의 직접 병합 제한 |
| [promotion-merge-commits](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/rules/26107391) | Beta·Release | Merge commit만 허용 |
| [manager-only-tag-creation](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/rules/26107394) | 태그 전체 | Esp3ra만 생성 |
| [immutable-version-tags](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/rules/26107396) | 태그 전체 | 기존 태그의 이동·삭제 차단 |

관리자 업데이트 권한과 PR·검사 조건은 다른 규칙으로 분리했습니다.
관리자는 업데이트 제한을 PR 안에서만 통과할 수 있고, 필수 CI·PR 규칙 자체에는 우회 권한이 없습니다.
사람 승인 수는 0명으로 시작하여 관리자 본인 PR의 자기 승인 문제를 피합니다.
실제 적용 상태는 GitHub Rules 화면과 각 브랜치의 활성 규칙에서 다시 확인할 수 있습니다.

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
