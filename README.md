# 2026-Moble-3rd-Team-Project

3차 최종 팀 프로젝트의 개발·검토·정식 버전을 관리하는 저장소입니다.
팀원은 **포크 없이 이 저장소를 clone**하고 작업별 단기 브랜치에서 PR을 제출합니다.

현재는 협업 환경을 준비한 상태입니다. 제품의 주제·기술 스택·담당 분담은 아직 정해지지 않았으며,
초기 브랜치가 존재한다는 사실이 기능 구현이나 정식 배포 완료를 뜻하지 않습니다.

## 브랜치와 버전

| 브랜치 | 역할 | 태그 예시 |
| --- | --- | --- |
| [Alpha](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/tree/Alpha) | 초안·화면 설계·기능 구현을 진행하는 개발 통합본 | `0.0.1V`, `0.0.2V` |
| [Beta](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/tree/Beta) | 1차 뼈대와 주요 흐름을 검토하는 버전 | `0.1.0V`, `0.1.1V` |
| [Release](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/tree/Release) | 필요한 오류 수정과 버전 검증을 마친 정식 기준본 | `1.0.0V`, `1.0.1V` |
| main | 저장소 소개와 공통 협업 환경 안내 | 제품 버전 태그 대상 아님 |

일반 작업은 `작업 브랜치 → Alpha`, 단계 승격은 `Alpha → Beta → Release`입니다.
태그 이름에는 공백 없이 대문자 `V`를 붙입니다. 태그는 관리자가 검증된 커밋에 생성합니다.
초기 설정에서는 제품 버전 태그와 GitHub Release를 만들지 않습니다.

## 팀원 작업 시작

관리자가 이 저장소에 초대한 계정으로 초대를 수락한 뒤 실행합니다.
팀원 계정은 아직 미정이며 현재 임의의 계정에 권한을 부여하지 않습니다.

```powershell
git clone https://github.com/Esp3ra/2026-Moble-3rd-Team-Project.git
cd 2026-Moble-3rd-Team-Project
git switch Alpha
git pull --ff-only origin Alpha
git switch -c feature/your-name-task
```

수정·검증 후 필요한 파일만 커밋하고 `git push -u origin feature/your-name-task`로 올립니다.
PR의 base는 **Alpha**, head는 본인의 작업 브랜치입니다. `main`·`Alpha`·`Beta`·`Release`에 직접 push하지 않습니다.
권한이 없다면 관리자에게 초대를 요청하며 포크로 우회하지 않습니다.

**OWNER 예외:** 저장소 소유자 `Esp3ra`는 `main`·`Alpha`·`Beta`·`Release`에 PR 없이 일반 push할 수 있습니다.
직접 push 후에도 정책 CI는 실행됩니다. 강제 push·단계 브랜치 삭제·기존 태그의 이동과 삭제는 OWNER도 차단됩니다.

## 협업 안내

- [협업·검토·관리자 설정](docs/COLLABORATION.md)
- [초기 설정과 보호 규칙 기록](docs/SETUP.md)
- [브랜치 승격과 태그 번호](docs/VERSIONING.md)
- [담당 분담과 공통 계약](docs/TEAM.md)
- [관리자용 Codex 프롬프트](docs/prompts/codex-github-manager-prompt.txt)
- [팀원용 Codex 프롬프트](docs/prompts/codex-github-member-prompt.txt)
- [Codex 공통 작업 규칙](AGENTS.md)

## 초기 검사

```powershell
python -m unittest discover -s scripts/tests -v
python scripts/check_repository.py
```

GitHub Actions의 `repository-policy` 검사는 PR 출처·대상, 협업 필수 파일과 게시 제외 파일을 점검합니다.
**제품 빌드·기능 테스트·공유 서버 검증을 수행하는 검사는 아닙니다.**
실제 프로그램을 추가할 때 기술 스택에 맞는 빌드·테스트 CI를 함께 추가합니다.

관리자 로컬 작업 위치: `C:\Users\cos31\Desktop\ES\3차 팀 프로젝트`.
Codex의 ES 프로젝트에서 작업할 때도 이 하위 저장소를 명시적으로 사용합니다.
