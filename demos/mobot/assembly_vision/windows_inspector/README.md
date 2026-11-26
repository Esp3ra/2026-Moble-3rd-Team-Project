# 조립 직후 부품 검사 테스트

조립 위치의 웹캠에서 머리와 양팔을 검출하고, 직접 선택한 주문 부품과 대조하는 C# 독립 테스트 도구입니다. 화면과 JSON에 정상, 누락, 오장착 또는 판정 보류를 표시합니다. [Alpha의 공정·통신 기준](../../../../docs/HARDWARE_WORKFLOW.md)에서 정의한 부품 코드와 판정명을 사용합니다.

## 빌드 및 실행

Windows 10 22H2 또는 Windows 11 x64, .NET SDK 10, .NET Framework 4.8 개발자 팩 및 Windows SDK 10.0.26100.0에서 빌드합니다. 다른 Windows SDK를 사용할 때는 `-p:WindowsWinMd=경로`로 해당 SDK의 `UnionMetadata/<버전>/Windows.winmd`를 지정하세요.

1. 기존 8개 클래스, 640×640 입력을 사용하는 ONNX 모델을 이 폴더의 `model/best.onnx`에 놓습니다. 클래스 순서는 `model/classes.txt`와 같아야 합니다. 모델 파일은 로컬에서 배치합니다.
2. 이 폴더에서 `dotnet build RobotCamera.csproj -c Release`를 실행하고, `bin/Release/net48/win-x64/RobotCamera.exe`를 엽니다. 실행 PC에 .NET Framework 4.8 및 ONNX Runtime에 필요한 x64 Visual C++ 재배포 가능 패키지가 설치돼 있어야 합니다.
3. 웹캠 영상에서 **검사 영역 지정**을 눌러 머리·로봇 기준 왼팔·오른팔 영역을 순서대로 드래그합니다. 카메라를 옮기거나 회전하면 영역을 다시 지정합니다.
4. 주문 부품을 선택합니다. 몸통의 위치와 해당 자리의 가림 여부를 확인해 체크하고 **검사**를 누릅니다. 결과는 **결과 JSON 복사**로 확인할 수 있습니다.

부품 후보가 없는 서로 다른 프레임 3장과 정위치·가림 없음 확인을 바탕으로 누락을 판정합니다. 확인이 필요한 경우 촬영 화면과 함께 해당 부위를 표시합니다. 약한 후보나 검출 불일치는 부위별로 보류 사유를 보여줍니다.

주문 부품 선택을 변경하면 화면의 판정과 JSON 복사 버튼이 초기화됩니다. 촬영 시간이 초과되면 프레임이 없는 경우 `NO_FRAME`, 서로 다른 프레임이 부족한 경우 `INSUFFICIENT_FRAMES`라는 보류 사유를 기록하고 JSON을 복사할 수 있습니다. `INSUFFICIENT_FRAMES`는 독립 시험 도구에서 쓰는 사유입니다.

사진으로 누락을 확인할 때는 몸통의 위치와 가림 여부를 확인한 뒤 `RobotCamera.exe --inspect-images regions.json H1 L1 R2 image1.jpg image2.jpg image3.jpg --confirm-visible`을 실행합니다. 확인 옵션을 생략하면 검출되지 않은 부위는 판정 보류입니다.

## 검증

`dotnet run --project tests/InspectionSmoke.csproj -c Release`는 모의 검출을 사용해 일치, 오장착, 누락, 확인 대기, 중복 프레임, 낮은 점수와 검출 불일치를 점검합니다.

실제 검사 위치의 웹캠에서는 완성품과 한 부품을 뺀 제품을 각각 배치해 정상과 누락을 확인하고, 기대 코드가 다른 주문으로 오장착을 확인합니다. 이 도구의 주문 입력과 JSON은 독립 시험용입니다. 촬영 시작 조건, 실제 주문·QR·검사 ID 매칭과 AGV 배송 연결은 [통합 이슈 #25](https://github.com/Esp3ra/2026-Moble-3rd-Team-Project/issues/25)에서 협의합니다.
