# WPF 관제 화면 뼈대

기준 시안: `docs/02_관제형.html` (2026-11-16 사용자 제공 로컬 참고 자료이며, 이 WPF 소스 배포에는 포함하지 않음).

## 실행

Windows와 .NET 10 SDK에서:

```powershell
dotnet build .\RobotFactory.sln -c Release
dotnet run --project .\RobotFactory.Admin\RobotFactory.Admin.csproj
```

Visual Studio에서는 `RobotFactory.sln`을 열면 됩니다. `RobotFactory.Admin` 프로젝트가 연결되어 있으며, F5로 실행할 수 있습니다.

## 이번 범위

- C# WPF 관제 화면과 로그인 화면의 XAML 배치
- 공통 색상·버튼·카드 스타일
- View와 ViewModel 분리, 화면 전용 예시 데이터 바인딩
- 상단 버튼으로 두 화면의 미리보기 전환
- 설비 상태는 일시정지 왼쪽에 한 줄로 배치
- CAM 01 검사 영역과 CAM 02·03 추가 영상 자리
- 검사 결과 아래 주문/QR 대조, 오른쪽 팔레트 현황
- 스크롤 없이 전체 화면을 비율에 맞춰 표시

로그인 화면 이동은 인증이 아닙니다. 인증, 권한, 주문 선택/처리, QR 판정, 카메라, 서버/DB, 로봇팔, 컨베이어, AGV 연결 및 제어는 구현하지 않았습니다. 해당 버튼은 비활성화되어 있으며, 실물 정지를 보장하지 않습니다. 팔레트 수와 주문 번호는 HTML 시안의 예시입니다.

## 구조 및 후속 연결점

- `MainWindow`: 화면 껍데기, 상단 공통 영역
- `Views`: 로그인 및 관제 화면
- `ViewModels`: 화면 탐색과 예시 표시 모델
- `Resources/Theme.xaml`: 공통 스타일

후속 개발 시 서버 담당자와 API·DTO·권한 정책을 먼저 합의한 뒤 예시 데이터를 교체합니다. 현재 모델은 팀 공통 DTO가 아닙니다. 실제 영상은 `DashboardView`의 CAM 자리에서 ImageSource 또는 영상 뷰로 연결합니다. 실제 정지 버튼은 요청/전송/응답/설비 정지를 구분해 구현해야 합니다.

이 폴더는 WPF 화면 뼈대와 예시 데이터만 포함하며, 로봇 테스트 프로그램과 분리되어 있습니다. GitHub Actions의 `WPF skeleton build`는 이 솔루션의 Release 빌드를 검사합니다.
