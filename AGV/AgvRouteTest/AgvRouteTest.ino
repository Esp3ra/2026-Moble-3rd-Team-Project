/*
AGV 인계 / 2026-11-24: 사용자 지시에 따라 암실 정차를 제거한 운전 프로필.
기존 main v0.2.0의 암실 공정과 다르므로 서버 단계 전환도 함께 적용해야 합니다.

운전: 2번 지게85° 대기 -> 1번 전진 -> 110° 인수 -> 2번 후진
      -> 전진하여 8번 좌회전 -> 9번까지 정차 없이 진행
      -> 서버가 지정한 3~7번 -> 60° 하역 -> 2번 후진 -> 85° 대기.
목적지: 3 서울 / 4 평택 / 5 수원 / 6 오류1 / 7 오류2.
8~9 사이의 기존 검사 가로 테이프는 실물에서도 제거해야 합니다.
후진 복귀는 검사 가로선 건너뛰기 없이 실제 8번 교차로를 인식합니다.

ESP32 HTTP 직접 호출: /go?point=1 -> /servo?angle=110 -> /go?point=2
-> /go?point=3~7 -> /servo?angle=60 -> /go?point=2 -> /servo?angle=85.
매 단계에서 arrived/position 또는 fork_moving=false/servo_angle 완료를 확인합니다.
/inspection은 410 Gone입니다. inspecting은 이전 버전 상태 식별용이며 새로 진입하지 않습니다.

메인 PC 연결 모듈: PICK_UP -> DELIVER -> UNLOAD -> RETURN_STANDBY.
DELIVER가 1->2 후진과 2->목적지 전진을 연속 단계로 수행합니다.
서버가 목적지와 기존 계약의 inspection_id를 제공해야 합니다. 검사를 가짜 생성하지 않습니다.
GO_TO_INSPECTION은 지원하지 않습니다. 기존 서버 엔진은 연결 전에 수정해야 합니다.

센서: 전진 전방6개, 후진 진행방향 기준 좌35/중앙34/우36. 지게 GPIO23.
진행 방향의 모든 센서가 연속 10000ms 미검출이면 모터0, line_lost, 위치불명 처리.
한 센서라도 재감지하면 연속 이탈 타이머 초기화. 일시정지 시간은 제외합니다.
이 검사는 전진·후진·회전·후진 테스트에 적용하며 정상 보정 속도/주기는 유지합니다.
10초는 이번 사용자 지정 펌웨어 프로필이며 팀 공통 규약에 명시된 수치는 아닙니다.

인증: 비공개 agv_secrets.h의 AGV_DEVICE_KEY, Bearer 및 X-AGV-Controller.
/control/session 후 같은 제어기의 /heartbeat로 연결을 유지합니다.
시험 기본값 heartbeat500ms/만료2초, RC 유지400ms. 통신단절은 10초 이탈과 별개로 정지합니다.
/stop은 모든 상태의 취소. /pause와 /resume만 경로·타이머를 보존하는 정상 정지/재개입니다.
위치불명·통신장애·취소는 현장 확인과 연결 모듈 복구 절차 전 자동 재출발하지 않습니다.
서보값은 출력각이며 별도 적재 센서가 없으므로 실제 적재 피드백으로 오인하지 않습니다.
실제 설정·키·DB는 커밋하지 않습니다. 컴파일/모의 시험은 실차 검증과 구분합니다.
*/


#include <Arduino.h>
#include <WiFi.h>
#include <atomic>
#include <esp_http_server.h>
#include <esp_random.h>
#include "ControlSession.h"
#if __has_include("agv_secrets.h")
#include "agv_secrets.h"
#endif
#ifndef AGV_DEVICE_KEY
#define AGV_DEVICE_KEY ""
#endif

constexpr char WIFI_SSID[] = "YOUR_WIFI_SSID";
constexpr char WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";

constexpr int MOTOR_RX = 16;
constexpr char CONTROL_KEY[] = AGV_DEVICE_KEY;
constexpr unsigned long SERVER_HEARTBEAT_TIMEOUT_MS = 2000;
portMUX_TYPE controlLock = portMUX_INITIALIZER_UNLOCKED;
ControlSession controlSession(CONTROL_KEY, SERVER_HEARTBEAT_TIMEOUT_MS);
constexpr int MOTOR_TX = 17;
// 핀 이름의 좌우는 차체 전진 방향 기준이다.
constexpr uint8_t REAR_RIGHT_PIN = 35;
constexpr uint8_t REAR_LEFT_PIN = 36;
constexpr uint8_t REAR_CENTER_PIN = 34;
// 흰 바닥 LOW, 검은 테이프 HIGH 기준. 실측이 반대면 LOW로 바꾼다.
constexpr int REAR_BLACK_LEVEL = HIGH;
constexpr uint8_t FORK_SERVO_PIN = 23;
constexpr uint32_t SERVO_PWM_HZ = 50;
constexpr uint8_t SERVO_PWM_BITS = 14;
constexpr uint32_t SERVO_MIN_PULSE_US = 1000;
constexpr uint32_t SERVO_MAX_PULSE_US = 2000;
constexpr int SERVO_MIN_ANGLE = 0;
constexpr int SERVO_MAX_ANGLE = 180;
constexpr int FORK_LOWERED_ANGLE = 85;
constexpr int FORK_LIFTED_ANGLE = 110;
constexpr unsigned long FORK_MOVE_MS = 1600;
constexpr unsigned long FORK_UPDATE_MS = 20;
constexpr uint8_t FORWARD_DIRECTION[] = {1, 2, 1, 2};
constexpr uint8_t LEFT_MOTORS[] = {1, 3};
constexpr uint8_t RIGHT_MOTORS[] = {2, 4};

struct LineSensor {
  int pin;
  unsigned long thresholdUs;
};

// 물리적 왼쪽(S13)부터 오른쪽(S03) 순서.
constexpr LineSensor SENSORS[] = {
  {27, 450}, {26, 450}, {25, 500}, {12, 500}, {14, 500}, {13, 500},
};
constexpr int SENSOR_COUNT = sizeof(SENSORS) / sizeof(SENSORS[0]);
constexpr unsigned long SENSOR_TIMEOUT_US = 5000;
constexpr unsigned long CONTROL_INTERVAL_MS = 25;
constexpr unsigned long REVERSE_CONTROL_INTERVAL_MS = 5;
constexpr unsigned long MOTOR_REFRESH_MS = 120;
constexpr unsigned long WIFI_REPORT_MS = 5000;

// 실차에서 먼저 조정할 값. CENTER_MS는 센서 줄이 분기선을 읽은 뒤
// 회전 중심이 교차점까지 전진하는 시간이다.
constexpr int FOLLOW_SPEED = 58;
constexpr int APPROACH_SPEED = 48;
constexpr int REVERSE_SPEED = 55;
constexpr int REVERSE_CORRECTION_SPEED = 70;
constexpr int REVERSE_RECOVERY_SPEED = 85;
constexpr unsigned long REVERSE_CORRECTION_PULSE_MS = 5;
constexpr int TURN_SPEED = 75;
constexpr int LINE_CORRECTION_REVERSE_PERCENT = 60;
constexpr int MIN_LINE_CORRECTION_REVERSE_SPEED = 35;
constexpr unsigned long CENTER_MS = 180;
constexpr unsigned long CHANGE_DIRECTION_MS = 250;
constexpr unsigned long TURN_MIN_MS = 120;
constexpr unsigned long JUNCTION_9_DETECT_DELAY_MS = 2000;
constexpr uint8_t TURN_EXIT_STABLE_READS = 3;
// 후진 중 뒤 센서가 교차로를 읽은 뒤 차체 회전축까지 더 후진하는 시간.
constexpr unsigned long REVERSE_CENTER_MS = 180;
constexpr unsigned long CROSS_TIMEOUT_MS = 1000;
constexpr unsigned long DEPARTURE_TIMEOUT_MS = 1400;
constexpr unsigned long LINE_LOST_MS = 10000; // Active-direction sensors absent continuously for 10 seconds.
constexpr unsigned long SEGMENT_TIMEOUT_MS = 20000;
constexpr uint8_t STABLE_READS = 2;

// 사진 기준 노드. 배송지와 오류 구역으로 이어지는 직각 코너도 각각 한 노드다.
//        1 ────── 8 ────── 2
//                 │
//                 │      코너 ── 3 (서울)
//                 │        │
//                 9 ───── 10 ─── 4 (평택)
//                 │        │
//       코너 ─── 11 ─ 코너  코너 ── 5 (수원)
//         │             │
//         7 (오류2)     6 (오류1)
enum Node : int8_t {
  NO_NODE = -1, POINT_1, POINT_2, POINT_3, POINT_4, POINT_5, POINT_6, POINT_7,
  JUNCTION_8, JUNCTION_9, JUNCTION_10, JUNCTION_11,
  BEND_3, BEND_5, BEND_6, BEND_7, NODE_COUNT,
};
constexpr int POINT_COUNT = POINT_7 + 1;

// 각 노드에서 북, 동, 남, 서 방향의 이웃.
constexpr Node NEIGHBORS[NODE_COUNT][4] = {
  {NO_NODE,     JUNCTION_8,  NO_NODE,      NO_NODE},      // 1 인수
  {NO_NODE,     NO_NODE,     NO_NODE,      JUNCTION_8},   // 2 대기·복귀
  {NO_NODE,     NO_NODE,     NO_NODE,      BEND_3},       // 3 서울 HUB
  {NO_NODE,     NO_NODE,     NO_NODE,      JUNCTION_10},  // 4 평택 HUB
  {NO_NODE,     NO_NODE,     NO_NODE,      BEND_5},       // 5 수원 HUB
  {BEND_6,      NO_NODE,     NO_NODE,      NO_NODE},      // 6 오류1
  {BEND_7,      NO_NODE,     NO_NODE,      NO_NODE},      // 7 오류2
  {NO_NODE,     POINT_2,     JUNCTION_9,   POINT_1},      // 8 상단 T자
  {JUNCTION_8,  JUNCTION_10, JUNCTION_11,  NO_NODE},      // 9 분기
  {BEND_3,      POINT_4,     BEND_5,       JUNCTION_9},   // 10 배송 사거리
  {JUNCTION_9,  BEND_6,      NO_NODE,      BEND_7},       // 11 오류 T자
  {NO_NODE,     POINT_3,     JUNCTION_10,  NO_NODE},      // 서울 코너
  {JUNCTION_10, POINT_5,     NO_NODE,      NO_NODE},      // 수원 코너
  {NO_NODE,     NO_NODE,     POINT_6,      JUNCTION_11},  // 오류1 코너
  {NO_NODE,     JUNCTION_11, POINT_7,      NO_NODE},      // 오류2 코너
};

enum class State : uint8_t {
  Unknown, Ready, Moving, Arrived, Stopped, WifiLost, LineLost, TimedOut,
  ReverseTesting, ControlLost, Inspecting, Paused,
};
enum class Phase : uint8_t { Follow, Center, Turn, Cross, ChangeDirection, ExitTurn };
enum class WebCommand : uint8_t { SetPosition, Go, Stop, Servo, ReverseTest, Mode, RcServo };

constexpr int STOP_COMMAND = -1;
constexpr int SET_POSITION_OFFSET = 10;
constexpr int FORK_LOWER_COMMAND = 31;
constexpr int FORK_LIFT_COMMAND = 32;
constexpr int REVERSE_TEST_COMMAND = 41;
constexpr int AUTO_MODE_COMMAND = 42;
constexpr int RC_MODE_COMMAND = 43;
constexpr int INSPECTION_COMMAND = 44;
constexpr int PAUSE_COMMAND = 45;
constexpr int RESUME_COMMAND = 46;
char deviceBootId[37] = {};
char pendingOperationId[49] = {};
char activeOperationId[49] = {};
constexpr int FORK_ANGLE_COMMAND_BASE = 100;
constexpr int RC_FORK_ANGLE_COMMAND_BASE = 400;
WebCommand modeRequest = WebCommand::Mode;
WebCommand setPositionRequest = WebCommand::SetPosition;
WebCommand goRequest = WebCommand::Go;
WebCommand stopRequest = WebCommand::Stop;
WebCommand servoRequest = WebCommand::Servo;
WebCommand rcServoRequest = WebCommand::RcServo;
WebCommand reverseTestRequest = WebCommand::ReverseTest;
std::atomic<int> pendingCommand{0};
constexpr uint8_t MIN_DRIVE_SPEED = 50;
constexpr uint8_t MAX_DRIVE_SPEED = 100;
constexpr uint8_t DEFAULT_DRIVE_SPEED = 70;
constexpr int TURN_REVERSE_PERCENT = 60;
constexpr unsigned long MOTOR_UPDATE_MS = 30;
constexpr unsigned long COMMAND_TIMEOUT_MS = 400;

enum class Motion : uint8_t { Stop = 'S', Forward = 'F', Backward = 'B', Left = 'L', Right = 'R' };

portMUX_TYPE motionLock = portMUX_INITIALIZER_UNLOCKED;
Motion requestedMotion = Motion::Stop;
uint8_t requestedSpeed = DEFAULT_DRIVE_SPEED;
bool rcStopPending = false;
unsigned long lastCommandAt = 0;
int controllerSocket = -1;
char controlPath[26];
std::atomic<bool> rcMode{false};
bool inspectionTrip = false;


// 상태, 현재 위치, 목적지를 한 번에 게시한다.
std::atomic<uint32_t> publicStatus{0};
// 후진 기준 bit 0은 오른쪽 GPIO36, bit 1은 왼쪽 GPIO35, bit 2는 가운데 GPIO34.
std::atomic<int> reverseTestReadings{-1};
// PWM 설정에 실패하면 -1. 그 외에는 마지막으로 출력한 설정 각도다.
std::atomic<int> servoAngle{-1};
std::atomic<bool> forkMoving{false};
float forkAngle = FORK_LOWERED_ANGLE;
float forkStartAngle = FORK_LOWERED_ANGLE;
int forkTargetAngle = FORK_LOWERED_ANGLE;
unsigned long forkStartedAt = 0;
unsigned long forkUpdatedAt = 0;

struct Sample {
  uint8_t bits = 0;
  int count = 0;
  int positionSum = 0;

  bool left() const { return bits & 0x01; }
  bool right() const { return bits & 0x20; }
  bool center() const { return bits & 0x0c; }
  bool straightLine() const { return center() && !left() && !right(); }
  bool centeredLine() const { return straightLine() && count <= 3; }
  bool stopBar() const { return left() && right() && center() && count >= 5; }
};

const char* const STATE_NAMES[] = {
  "unknown", "ready", "moving", "arrived", "stopped",
  "wifi_lost", "line_lost", "timeout", "reverse_testing", "control_lost", "inspecting", "paused",
};
const char* const PHASE_NAMES[] = {"follow", "center", "turn", "cross", "change_direction", "exit_turn"};
State state = State::Unknown;
Phase phase = Phase::Follow;
Node location = NO_NODE;
Node destination = NO_NODE;
Node lastDetected = NO_NODE;
bool serverReady = false;
Node route[NODE_COUNT];
uint8_t routeLength = 0;
uint8_t nextIndex = 0;
unsigned long segmentAt = 0;
unsigned long phaseAt = 0;
unsigned long lineLostAt = 0;
bool lineMissing = false;
unsigned long lineMissingAt = 0;
bool recovering = false;
int lastLineSide = 0;
int rearLastLineSide = 0;
unsigned long rearCorrectionStartedAt = 0;
uint8_t matchReads = 0;
uint8_t centeredReads = 0;
bool readyForFeature = false;
bool sawCenterLeave = false;
bool turnRight = false;
int activeTurnSpeed = TURN_SPEED;
bool reversing = false;
int motorLeft = 101;
int motorRight = 101;
unsigned long motorSentAt = 0;
State pausedState = State::Unknown;
bool pausedForkMoving = false;
unsigned long pausedAt = 0;
int pausedMotorLeft = 0;
int pausedMotorRight = 0;

const char CONTROL_SCRIPT[] = R"JS(
(() => {
  const storageKey = 'agv-device-key';
  let key = sessionStorage.getItem(storageKey) || '';
  let controller = sessionStorage.getItem('agv-controller-id');
  if (!controller) {
    controller = 'browser-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
    sessionStorage.setItem('agv-controller-id', controller);
  }
  let timer = null;
  let resolveReady;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  const form = document.createElement('form');
  const label = document.createElement('label');
  label.textContent = '장치 제어키 ';
  const input = document.createElement('input');
  input.type = 'password';
  input.autocomplete = 'off';
  input.required = true;
  input.value = key;
  label.append(input);
  const button = document.createElement('button');
  button.type = 'submit';
  button.textContent = '제어 연결';
  const message = document.createElement('p');
  message.setAttribute('role', 'status');
  message.textContent = '승인된 장치 제어키로 연결하세요.';
  form.append(label, button, message);
  document.querySelector('main').prepend(form);
  const headers = () => ({
    'Authorization': 'Bearer ' + key,
    'X-AGV-Controller': controller,
    'X-AGV-Control': '1'
  });
  let heartbeatBusy = false;
  async function heartbeat() {
    if (heartbeatBusy) return;
    heartbeatBusy = true;
    try {
      const response = await fetch('/heartbeat', {
        method: 'POST', headers: headers(), signal: AbortSignal.timeout(900)
      });
      if (!response.ok) {
        clearInterval(timer);
        timer = null;
        message.textContent = '제어 연결이 만료되었습니다. 상태를 확인하고 다시 연결하세요.';
      }
    } catch {
      message.textContent = '제어 신호 전송 실패 · 통신 시간초과 시 장치가 정지합니다.';
    } finally {
      heartbeatBusy = false;
    }
  }
  async function connect(event) {
    event?.preventDefault();
    clearInterval(timer);
    key = input.value.trim();
    button.disabled = true;
    try {
      const response = await fetch('/control/session', {
        method: 'POST', headers: headers(), signal: AbortSignal.timeout(1500)
      });
      if (!response.ok) throw new Error(await response.text());
      sessionStorage.setItem(storageKey, key);
      message.textContent = '제어 연결됨 · 연결 유지 신호 전송 중';
      timer = setInterval(heartbeat, 500);
      resolveReady();
    } catch (error) {
      message.textContent = '제어 연결 실패 · 키, 다른 제어기 사용 여부와 장치 정지 상태를 확인하세요.';
    } finally {
      button.disabled = false;
    }
  }
  form.addEventListener('submit', connect);
  window.agvControl = {
    ready,
    async fetch(path, options = {}) {
      await ready;
      return fetch(path, {...options, headers: {...options.headers, ...headers()}});
    },
    socketUrl(path) {
      return 'ws://' + location.host + path +
        '?key=' + encodeURIComponent(key) + '&controller=' + encodeURIComponent(controller);
    }
  };
  window.addEventListener('pagehide', () => clearInterval(timer));
  if (key) connect();
})();
)JS";

const char CONTROL_PAGE[] = R"HTML(
<!doctype html>
<html lang="ko">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AGV 경로 시험</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; font: 16px system-ui, sans-serif; color: #172531; background: #eef2f4; }
  main { max-width: 460px; margin: 0 auto; padding: 24px; background: white; border-radius: 12px; }
  h1 { margin: 0 0 16px; font-size: 1.55rem; }
  h2 { margin: 20px 0 8px; font-size: 1.1rem; }
  p { line-height: 1.5; }
  .track { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 8px 0 16px; }
  .delivery { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .fork-control { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin: 12px 0 16px; }
  .fork-control form { display: grid; gap: 8px; min-width: 0; }
  .fork-control input { min-width: 0; width: 100%; }
  button, select, input { padding: 10px; border: 1px solid #8795a0; border-radius: 7px; font: inherit; }
  button { cursor: pointer; background: #165c67; color: white; }
  button:disabled { cursor: default; opacity: .5; }
  #stop { width: 100%; background: #a22222; }
  #drive-step { font-weight: 600; }
  #message { min-height: 1.5em; margin-bottom: 0; }
  .position { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
</style>
<main>
  <h1>AGV 자동 경로</h1>
  <div class="position">
    <a href="/rc">수동 RC</a>
    <button id="auto-mode" type="button" hidden>자동 모드로 전환</button>
  </div>
  <p id="state" aria-live="polite">연결 확인 중</p>
  <p id="drive-step" role="status" hidden></p>
  <p id="last-detection" hidden></p>
  <div class="position">
    <label for="position">출발 위치</label>
    <select id="position">
      <option value="1">1번 인수</option>
      <option value="2">2번 대기·복귀</option>
      <option value="3">3번 서울 HUB</option>
      <option value="4">4번 평택 HUB</option>
      <option value="5">5번 수원 HUB</option>
      <option value="6">6번 오류1</option>
      <option value="7">7번 오류2</option>
    </select>
    <button id="set" type="button" disabled>위치 설정</button>
  </div>
  <p>1·2번에서는 지게를 1번 쪽으로, 3~7번에서는 각 구역의 끝 쪽으로 향하게 두세요.</p>
  <div class="track" aria-label="인수·대기 이동">
    <button data-point="1" type="button" disabled>1번 인수</button>
    <button data-point="2" type="button" disabled>2번 대기·복귀</button>
  </div>
  <h2>배송지</h2>
  <div class="track delivery" aria-label="배송지 선택">
    <button data-point="3" type="button" disabled>3번<br>서울 HUB</button>
    <button data-point="4" type="button" disabled>4번<br>평택 HUB</button>
    <button data-point="5" type="button" disabled>5번<br>수원 HUB</button>
  </div>
  <h2>오류 분류</h2>
  <div class="track" aria-label="오류 분류 구역 선택">
    <button data-point="6" type="button" disabled>6번 오류1</button>
    <button data-point="7" type="button" disabled>7번 오류2</button>
  </div>
  <p>3~7번에서 2번을 누르면 왔던 길을 후진으로 복귀합니다.</p>
  <h2>후진 라인트레이싱</h2>
  <p>검은 선을 뒤쪽 좌우 센서 사이에 놓고 스타트를 누르세요.</p>
  <div class="fork-control">
    <button id="reverse-test" type="button" disabled>스타트</button>
    <button id="stop" type="button">스탑</button>
  </div>
  <p id="reverse-readings" hidden></p>
  <h2>지게차</h2>
  <div class="fork-control">
    <form data-fork>
      <label for="lift-angle">들기 각도 (°)</label>
      <input id="lift-angle" type="number" min="0" max="180" step="1" value="110" required>
      <button type="submit" disabled>들기</button>
    </form>
    <form data-fork>
      <label for="lower-angle">내리기 각도 (°)</label>
      <input id="lower-angle" type="number" min="0" max="180" step="1" value="85" required>
      <button type="submit" disabled>내리기</button>
    </form>
  </div>
  <p id="fork-output" hidden></p>
  <p id="message" aria-live="polite"></p>
</main>
<script src="/control.js"></script>
<script>
  const buttons = [...document.querySelectorAll('[data-point]')];
  const forkForms = [...document.querySelectorAll('[data-fork]')];
  const forkOutput = document.querySelector('#fork-output');
  const position = document.querySelector('#position');
  const pointNames = new Map(Array.from(position.options, option => [Number(option.value), option.textContent]));
  const setButton = document.querySelector('#set');
  const reverseTest = document.querySelector('#reverse-test');
  const reverseReadings = document.querySelector('#reverse-readings');
  const statusText = document.querySelector('#state');
  const driveStep = document.querySelector('#drive-step');
  const lastDetection = document.querySelector('#last-detection');
  const message = document.querySelector('#message');
  const labels = {
    unknown: '출발 위치를 설정하세요', ready: '출발 대기', moving: '이동 중',
    arrived: '도착', stopped: '정지 · 출발 위치를 다시 설정하세요',
    wifi_lost: 'Wi-Fi 끊김 · 출발 위치를 다시 설정하세요',
    line_lost: '라인 이탈 · 출발 위치를 다시 설정하세요',
    timeout: '이동 시간 초과 · 출발 위치를 다시 설정하세요',
    reverse_testing: '후진 라인트레이싱 모드',
    control_lost: '제어 통신 끊김 · 위치와 공정 상태를 확인하세요',
    inspecting: '이전 버전 검사 대기 · 복구 필요'
  };
  let waiting = false;
  let commandVersion = 0;
  let latestStatus = null;

  const autoMode = document.querySelector('#auto-mode');
  autoMode.onclick = () => command('/mode?value=auto');

  const cornerNames = ['서울 코너', '수원 코너', '오류1 코너', '오류2 코너'];
  function nodeName(number) {
    return pointNames.get(number) || cornerNames[number - 12] || `${number}번`;
  }

  function describeDriving(status) {
    const node = nodeName(status.next_node);
    switch (status.phase) {
      case 'center': return `${node} 인식 · 교차로 중심으로 이동 중`;
      case 'turn': return `${node} ${status.turn_right ? '우회전' : '좌회전'} 중`;
      case 'cross': return `${node} 교차로 통과 중`;
      case 'exit_turn': return `${node} 회전 후 직선 확인 중`;
      case 'change_direction': return `${node} 정차 · 전진 전환 대기 중`;
      default: return `${node} 쪽으로 ${status.reversing ? '후진' : '전진'} 중`;
    }
  }

  function draw() {
    autoMode.hidden = latestStatus?.mode !== 'rc';
    autoMode.disabled = waiting;
    const moving = latestStatus?.state === 'moving';
    const busy = latestStatus?.mode !== 'auto' || moving || latestStatus?.state === 'reverse_testing' || latestStatus?.fork_moving;
    position.disabled = setButton.disabled = reverseTest.disabled =
      !latestStatus || waiting || busy;
    const servoAngle = latestStatus?.servo_angle;
    const servoReady = Number.isInteger(servoAngle) && servoAngle >= 0;
    forkForms.forEach(form => {
      form.querySelector('button').disabled = !servoReady || waiting || busy;
    });
    forkOutput.hidden = !servoReady;
    if (servoReady) forkOutput.textContent = '지게 출력 각도 ' + servoAngle + '°';
    buttons.forEach(button => {
      const target = Number(button.dataset.point);
      const current = latestStatus?.position;
      const atInspection = latestStatus?.state === 'inspecting';
      button.disabled = waiting || busy ||
        (atInspection || !current || target === current || (current >= 3 && target !== 2));
    });
    statusText.textContent = latestStatus?.mode === 'rc'
      ? '수동 RC 모드 · 자동 운행을 시작하려면 위에서 모드를 전환하세요.'
      : latestStatus
      ? labels[latestStatus.state] +
        (latestStatus.position ? ' · 현재 ' + pointNames.get(latestStatus.position) : '') +
        (moving ? ' → ' + pointNames.get(latestStatus.target) : '') +
        (latestStatus.fork_moving ? ' · 지게 움직이는 중' : '')
      : 'AGV 연결 끊김';
    driveStep.hidden = !moving || latestStatus.mode !== 'auto' || !latestStatus.next_node;
    if (!driveStep.hidden) {
      const step = describeDriving(latestStatus);
      if (driveStep.textContent !== step) driveStep.textContent = step;
    }
    lastDetection.hidden = latestStatus?.mode !== 'auto' || !latestStatus.last_detected;
    if (!lastDetection.hidden) lastDetection.textContent = `최근 감지 · ${nodeName(latestStatus.last_detected)} 인식`;
    const readings = latestStatus?.reverse_readings;
    reverseReadings.hidden = !Number.isInteger(readings) || readings < 0;
    if (!reverseReadings.hidden) {
      const rearLeft = (readings >> 1) & 1;
      const rearRight = readings & 1;
      const rearCenter = (readings >> 2) & 1;
      const tracking = latestStatus.state === 'reverse_testing' ||
        (latestStatus.state === 'moving' && latestStatus.reversing);
      const actions = [
        '미감지 · 마지막 감지 방향으로 탐색', '뒤쪽 오른쪽 보정', '뒤쪽 왼쪽 보정', '직선 후진',
        '직선 후진', '뒤쪽 오른쪽 보정', '뒤쪽 왼쪽 보정', '직선 후진'
      ];
      reverseReadings.textContent = (tracking ? '뒤 센서' : '마지막 뒤 센서값') +
        ' (후진 기준 · 검은색 1) · 왼쪽 35번 ' + rearLeft + ' / 가운데 34번 ' + rearCenter + ' / 오른쪽 36번 ' + rearRight +
        (latestStatus.state === 'reverse_testing' ? ' · ' + actions[readings] : '');
    }
  }

  async function readStatus() {
    const response = await agvControl.fetch('/status', {cache: 'no-store', signal: AbortSignal.timeout(1500)});
    if (!response.ok) throw new Error('상태를 읽지 못했습니다.');
    return response.json();
  }

  async function refresh() {
    const version = commandVersion;
    try {
      const value = await readStatus();
      if (version === commandVersion) latestStatus = value;
    } catch {
      if (version === commandVersion) latestStatus = null;
    }
    draw();
  }

  async function command(path) {
    const version = ++commandVersion;
    waiting = true;
    draw();
    message.textContent = '';
    try {
      const response = await agvControl.fetch(path, {
        method: 'POST',
        headers: {'X-AGV-Control': '1'},
        signal: AbortSignal.timeout(1800)
      });
      if (!response.ok) throw new Error(await response.text());
    } catch (error) {
      if (version === commandVersion) message.textContent = error.message || '명령 확인 실패';
    } finally {
      if (version === commandVersion) {
        await refresh();
        if (version === commandVersion) {
          waiting = false;
          draw();
        }
      }
    }
  }

  setButton.onclick = () => command('/position?point=' + position.value);
  reverseTest.onclick = () => command('/reverse-test');
  buttons.forEach(button => button.onclick = () => command('/go?point=' + button.dataset.point));
  forkForms.forEach(form => form.onsubmit = event => {
    event.preventDefault();
    if (!form.querySelector('button').disabled && form.reportValidity()) {
      command('/servo?angle=' + form.querySelector('input').valueAsNumber);
    }
  });
  document.querySelector('#stop').onclick = () => command('/stop');
  async function poll() {
    if (!waiting) await refresh();
    setTimeout(poll, latestStatus?.state === 'moving' ? 125 : 350);
  }
  poll();
</script>
</html>
)HTML";

const char RC_PAGE[] = R"HTML(
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AGV 방향키 조종</title>
  <style>
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
           background: #eef2f6; color: #142333; font: 1rem "Inter", system-ui, sans-serif; }
    main { text-align: center; padding: 24px; }
    h1 { font-size: 1.4rem; margin: 0 0 12px; }
    #status { margin: 0 0 20px; }
    .speed { display: grid; grid-template-columns: 1fr auto; gap: 8px; margin: 0 auto 18px; max-width: 244px; text-align: left; }
    .speed input { grid-column: 1 / -1; width: 100%; margin: 0; accent-color: #164d75; }
    .speed output { font-variant-numeric: tabular-nums; }
    .keys { display: grid; grid-template-columns: repeat(3, 76px); gap: 8px; justify-content: center; }
    kbd { display: grid; place-content: center; gap: 3px; min-height: 58px; background: #fff;
          border: 1px solid #a8b6c4; border-radius: 8px; font: inherit; }
    kbd[data-key="ArrowUp"] { grid-column: 2; }
    kbd[data-key="ArrowLeft"] { grid-column: 1; }
    kbd.active { color: #fff; background: #164d75; border-color: #164d75; }
    kbd span { font-size: 0.8rem; }
    .servo-controls { display: flex; flex-wrap: wrap; justify-content: center; align-items: center; gap: 8px; }
    .servo-controls button, .servo-controls input { padding: 8px; font: inherit; }
    .servo-controls input { width: 5em; }
  </style>
</head>
<body>
  <main>
    <a href="/">자동 경로</a>
    <h1>AGV 방향키 조종</h1>
    <p id="status" role="status">연결 중</p>
    <div class="speed">
      <label for="drive-speed">속도</label>
      <output id="speed-value" for="drive-speed"></output>
      <input id="drive-speed" type="range" min="{{MIN_SPEED}}" max="{{MAX_SPEED}}" step="5" value="{{DEFAULT_SPEED}}">
    </div>
    <div class="keys" aria-label="방향키 안내">
      <kbd data-key="ArrowUp">↑<span>전진</span></kbd>
      <kbd data-key="ArrowLeft">←<span>좌회전</span></kbd>
      <kbd data-key="ArrowDown">↓<span>후진</span></kbd>
      <kbd data-key="ArrowRight">→<span>우회전</span></kbd>
    </div>
    <button id="rc-stop" type="button">정지</button>
    <p>방향키를 누르는 동안 움직이고, 떼면 멈춥니다.</p>
    <h2>지게 각도 테스트</h2>
    <form id="fork-form" class="servo-controls">
      <label for="fork-angle">목표 각도 (0~180°)</label>
      <input id="fork-angle" type="number" min="0" max="180" step="1" value="85" required>
      <button id="fork-apply" type="submit" disabled>적용</button>
    </form>
    <p id="fork-output" hidden></p>
    <p id="servo-message" role="status" hidden></p>
  </main>
  <script src="/control.js"></script>
  <script>
    const commands = new Map([
      ['ArrowUp', 'F'], ['ArrowDown', 'B'], ['ArrowLeft', 'L'], ['ArrowRight', 'R']
    ]);
    const status = document.querySelector('#status');
    const keys = document.querySelectorAll('kbd[data-key]');
    const speed = document.querySelector('#drive-speed');
    const speedValue = document.querySelector('#speed-value');
    const forkForm = document.querySelector('#fork-form');
    const forkInput = document.querySelector('#fork-angle');
    const forkApply = document.querySelector('#fork-apply');
    const forkOutput = document.querySelector('#fork-output');
    const servoMessage = document.querySelector('#servo-message');
    let socket;
    let activeKey = null;
    let rcConnected = false;
    let servoAvailable = false;
    let servoSending = false;

    function updateServoControls() {
      forkApply.disabled = !rcConnected || !servoAvailable || servoSending || activeKey !== null;
    }

    function showFork(status) {
      servoAvailable = status.servo_angle >= 0;
      forkOutput.hidden = !servoAvailable;
      if (servoAvailable) forkOutput.textContent = '지게 출력 각도 ' + status.servo_angle + '°';
    }

    async function readStatus() {
      const response = await agvControl.fetch('/status', {cache: 'no-store', signal: AbortSignal.timeout(1500)});
      if (!response.ok) throw new Error();
      return response.json();
    }

    forkForm.onsubmit = async event => {
      event.preventDefault();
      if (forkApply.disabled || !forkForm.reportValidity()) return;
      const angle = forkInput.valueAsNumber;
      servoSending = true;
      servoMessage.hidden = true;
      updateServoControls();
      try {
        const response = await agvControl.fetch('/rc/servo?angle=' + angle, {
          method: 'POST', headers: {'X-AGV-Control': '1'}, signal: AbortSignal.timeout(1800)
        });
        if (!response.ok) throw new Error();
        for (let attempt = 0; attempt < 15; ++attempt) {
          await new Promise(resolve => setTimeout(resolve, 150));
          const state = await readStatus();
          showFork(state);
          if (state.mode !== 'rc' || !servoAvailable) break;
          if (!state.fork_moving && state.servo_angle === angle) return;
        }
        throw new Error();
      } catch {
        servoMessage.textContent = '지게 각도를 적용하지 못했습니다. AGV가 멈춘 뒤 다시 누르세요.';
        servoMessage.hidden = false;
      } finally {
        servoSending = false;
        updateServoControls();
      }
    };

    function sendDrive(key) {
      socket.send(commands.get(key) + speed.value);
    }

    function updateSpeed() {
      speedValue.textContent = speed.value;
      if (activeKey !== null && socket?.readyState === WebSocket.OPEN) sendDrive(activeKey);
    }

    function showActiveKey(key) {
      activeKey = key;
      for (const element of keys) {
        element.classList.toggle('active', element.dataset.key === key);
      }
      updateServoControls();
    }

    function stopDrive() {
      if (activeKey === null) return;
      showActiveKey(null);
      if (socket?.readyState === WebSocket.OPEN) socket.send('S');
    }

    function connect() {
      socket = new WebSocket(agvControl.socketUrl('{{CONTROL_PATH}}'));
      socket.addEventListener('open', () => {
        rcConnected = true;
        status.textContent = '연결됨';
        updateServoControls();
      });
      socket.addEventListener('message', event => {
        if (event.data === 'inactive') {
          rcConnected = false;
          showActiveKey(null);
          status.textContent = '자동 모드 사용 중 · 다시 조종하려면 새로고침하세요.';
        }
      });
      socket.addEventListener('close', () => {
        rcConnected = false;
        showActiveKey(null);
        status.textContent = '연결 끊김 · 재연결 중';
        setTimeout(connect, 1000);
      });
    }

    window.addEventListener('keydown', event => {
      if (!commands.has(event.key) || event.altKey || event.ctrlKey || event.metaKey) return;
      if ((event.target === speed || event.target === forkInput) && activeKey === null) return;
      event.preventDefault();
      if (event.repeat || servoSending || socket?.readyState !== WebSocket.OPEN || document.hidden || !document.hasFocus()) return;
      sendDrive(event.key);
      showActiveKey(event.key);
    });

    window.addEventListener('keyup', event => {
      if (event.key === activeKey) {
        event.preventDefault();
        stopDrive();
      }
    });
    window.addEventListener('blur', stopDrive);
    window.addEventListener('pagehide', stopDrive);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stopDrive();
    });

    setInterval(() => {
      if (activeKey !== null && socket?.readyState === WebSocket.OPEN && socket.bufferedAmount === 0) {
        socket.send('K');
      }
    }, 100);
    speed.addEventListener('input', updateSpeed);
    speed.addEventListener('pointerup', () => speed.blur());
    speed.addEventListener('keydown', event => {
      if (event.key === 'Escape') speed.blur();
    });
    updateSpeed();
    async function enableRc() {
      try {
        const response = await agvControl.fetch('/mode?value=rc', {
          method: 'POST', headers: {'X-AGV-Control': '1'}, signal: AbortSignal.timeout(1800)
        });
        if (!response.ok) throw new Error();
        for (let attempt = 0; attempt < 15; ++attempt) {
          const state = await readStatus();
          if (state.mode === 'rc') {
            showFork(state);
            if (!servoAvailable) {
              servoMessage.textContent = '지게 서보를 사용할 수 없습니다.';
              servoMessage.hidden = false;
            } else {
              forkInput.value = state.servo_angle;
            }
            connect();
            return;
          }
          await new Promise(resolve => setTimeout(resolve, 100));
        }
        throw new Error();
      } catch {
        status.textContent = '수동 모드 전환 실패 · 새로고침해서 다시 시도하세요.';
      }
    }
    document.querySelector('#rc-stop').onclick = async () => {
      stopDrive();
      try {
        const response = await agvControl.fetch('/stop', {
          method: 'POST', headers: {'X-AGV-Control': '1'}, signal: AbortSignal.timeout(1800)
        });
        if (!response.ok) throw new Error();
      } catch {
        status.textContent = '정지 응답을 받지 못했습니다.';
      }
    };
    enableRc();
  </script>
</body>
</html>
)HTML";

void sendMotor(uint8_t motor, int speed) {
  const uint8_t forward = FORWARD_DIRECTION[motor - 1];
  const uint8_t direction = speed < 0 ? (forward == 1 ? 2 : 1) : forward;
  const uint8_t packet[] = {motor, direction, static_cast<uint8_t>(abs(speed)), '\r', '\n'};
  Serial2.write(packet, sizeof(packet));
  Serial2.flush();
}

void drive(int left, int right, bool force = false) {
  left = constrain(left, -100, 100);
  right = constrain(right, -100, 100);
  const unsigned long now = millis();
  if (!force && left == motorLeft && right == motorRight && now - motorSentAt < MOTOR_REFRESH_MS) {
    return;
  }
  motorLeft = left;
  motorRight = right;
  motorSentAt = now;
  for (uint8_t motor : LEFT_MOTORS) sendMotor(motor, left);
  for (uint8_t motor : RIGHT_MOTORS) sendMotor(motor, right);
}

void applyMotion(Motion motion, int speed) {
  const int turnReverseSpeed = speed * TURN_REVERSE_PERCENT / 100;
  switch (motion) {
    case Motion::Forward: drive(speed, speed); break;
    case Motion::Backward: drive(-speed, -speed); break;
    case Motion::Left: drive(-turnReverseSpeed, speed); break;
    case Motion::Right: drive(speed, -turnReverseSpeed); break;
    case Motion::Stop: drive(0, 0); break;
  }
}

void clearRcCommand() {
  portENTER_CRITICAL(&motionLock);
  requestedMotion = Motion::Stop;
  controllerSocket = -1;
  rcStopPending = true;
  portEXIT_CRITICAL(&motionLock);
}

bool rcDriveStopped() {
  portENTER_CRITICAL(&motionLock);
  const bool stopped = requestedMotion == Motion::Stop && !rcStopPending;
  portEXIT_CRITICAL(&motionLock);
  return stopped;
}

void updateRcDrive() {
  static Motion applied = Motion::Stop;
  portENTER_CRITICAL(&motionLock);
  if (rcStopPending || millis() - lastCommandAt >= COMMAND_TIMEOUT_MS) {
    requestedMotion = Motion::Stop;
    controllerSocket = -1;
  }
  const Motion motion = requestedMotion;
  const uint8_t speed = requestedSpeed;
  if (motion == Motion::Stop && applied == Motion::Stop && !rcStopPending) {
    portEXIT_CRITICAL(&motionLock);
    return;
  }
  if (motion == Motion::Stop) rcStopPending = true;
  portEXIT_CRITICAL(&motionLock);
  applyMotion(motion, speed);
  applied = motion;
  if (motion == Motion::Stop) {
    portENTER_CRITICAL(&motionLock);
    rcStopPending = false;
    portEXIT_CRITICAL(&motionLock);
  }
}

void pivot(int speed, bool right) {
  drive(right ? speed : -speed, right ? -speed : speed);
}

void correctLine(int speed, bool right, bool backward = false) {
  const int reverse = max(MIN_LINE_CORRECTION_REVERSE_SPEED, speed * LINE_CORRECTION_REVERSE_PERCENT / 100);
  const int left = right ? speed : -reverse;
  const int rightSpeed = right ? -reverse : speed;
  // 후진은 좌우 바퀴를 바꾸고 부호를 반전해 같은 방향으로 조향한다.
  drive(backward ? -rightSpeed : left, backward ? -left : rightSpeed);
}

Sample readLine() {
  unsigned long releasedAt[SENSOR_COUNT];
  bool pending[SENSOR_COUNT];
  int remaining = SENSOR_COUNT;

  for (const LineSensor& sensor : SENSORS) {
    digitalWrite(sensor.pin, HIGH);
    pinMode(sensor.pin, OUTPUT);
  }
  delayMicroseconds(10);
  for (int i = 0; i < SENSOR_COUNT; i++) {
    pinMode(SENSORS[i].pin, INPUT);
    releasedAt[i] = micros();
    pending[i] = true;
  }

  Sample sample;
  while (remaining > 0) {
    for (int i = 0; i < SENSOR_COUNT; i++) {
      if (!pending[i]) continue;
      const unsigned long duration = micros() - releasedAt[i];
      if (duration < SENSOR_TIMEOUT_US && digitalRead(SENSORS[i].pin) != LOW) continue;
      pending[i] = false;
      remaining--;
      // 타임아웃한 채널은 첨부 코드와 동일하게 제외한다.
      if (duration >= SENSOR_TIMEOUT_US || duration <= SENSORS[i].thresholdUs) continue;
      sample.bits |= 1u << i;
      sample.count++;
      sample.positionSum += 2 * i - (SENSOR_COUNT - 1);
    }
  }
  return sample;
}

void publishStatus() {
  // 같은 주행 주기의 상태, 경로 번호, 동작을 웹 서버에 한 번에 전달한다.
  static_assert(NODE_COUNT <= 15, "Status node fields use four bits");
  uint32_t value = static_cast<uint32_t>(state) |
    (static_cast<uint32_t>(location + 1) << 4) |
    (static_cast<uint32_t>(destination + 1) << 8) |
    (static_cast<uint32_t>(lastDetected + 1) << 21) |
    (static_cast<uint32_t>(inspectionTrip) << 25);
  if (state == State::Moving) {
    value |= (static_cast<uint32_t>(route[nextIndex] + 1) << 12) |
      (static_cast<uint32_t>(phase) << 16) |
      (static_cast<uint32_t>(reversing) << 19) |
      (static_cast<uint32_t>(phase == Phase::Turn && turnRight) << 20);
  }
  publicStatus.store(value);
}

void publish(State next) {
  state = next;
  if (next == State::Unknown || next == State::Ready || next == State::Moving || next == State::ReverseTesting) {
    lastDetected = NO_NODE;
  }
  publishStatus();
  Serial.printf("AGV %s position=%d target=%d\n", STATE_NAMES[static_cast<int>(state)],
    location + 1, destination + 1);
}

bool appendRoute(Node from, Node to) {
  Node parents[NODE_COUNT];
  Node queue[NODE_COUNT];
  for (Node& parent : parents) parent = NO_NODE;
  parents[from] = from;
  uint8_t head = 0;
  uint8_t tail = 0;
  queue[tail++] = from;
  while (head < tail && parents[to] == NO_NODE) {
    const Node node = queue[head++];
    for (Node neighbor : NEIGHBORS[node]) {
      if (neighbor == NO_NODE || parents[neighbor] != NO_NODE) continue;
      parents[neighbor] = node;
      queue[tail++] = neighbor;
    }
  }
  if (parents[to] == NO_NODE) return false;

  Node path[NODE_COUNT];
  uint8_t length = 0;
  for (Node node = to; node != from; node = parents[node]) path[length++] = node;
  if (routeLength + length > NODE_COUNT) return false;
  while (length > 0) route[routeLength++] = path[--length];
  return true;
}

bool isDispatchPoint(Node node) {
  return node >= POINT_3 && node <= POINT_7;
}

bool canTravel(Node from, Node to) {
  return from >= POINT_1 && from <= POINT_7 && to >= POINT_1 && to <= POINT_7 &&
    from != to && (!isDispatchPoint(from) || to == POINT_2);
}

bool makeRoute(Node from, Node to) {
  routeLength = 0;
  route[routeLength++] = from;
  // 1번에서 배송·오류 구역으로 갈 때 2번까지 후진한 뒤 전진으로 바꾼다.
  if (from == POINT_1 && isDispatchPoint(to)) {
    return appendRoute(POINT_1, POINT_2) && appendRoute(POINT_2, to);
  }
  return appendRoute(from, to);
}

int headingBetween(Node from, Node to) {
  for (int heading = 0; heading < 4; heading++) {
    if (NEIGHBORS[from][heading] == to) return heading;
  }
  return -1;
}

int approachHeading() {
  return headingBetween(route[nextIndex - 1], route[nextIndex]);
}

int nextTurn() {
  const int outgoing = headingBetween(route[nextIndex], route[nextIndex + 1]);
  return (outgoing - approachHeading() + 4) % 4;
}

uint8_t rearLineMask() {
  const bool left = digitalRead(REAR_LEFT_PIN) == REAR_BLACK_LEVEL;
  const bool right = digitalRead(REAR_RIGHT_PIN) == REAR_BLACK_LEVEL;
  const bool center = digitalRead(REAR_CENTER_PIN) == REAR_BLACK_LEVEL;
  const uint8_t mask = static_cast<uint8_t>(left + 2 * right + 4 * center);
  reverseTestReadings.store(mask);
  return mask;
}

void driveAlongLine(int speed) {
  const int signedSpeed = reversing ? -REVERSE_SPEED : speed;
  drive(signedSpeed, signedSpeed);
}

void stopTrip(State reason) {
  drive(0, 0, true);
  if (state == State::Moving || (state == State::Paused && pausedState == State::Moving)) {
    location = NO_NODE;
  }
  destination = NO_NODE;
  publish(reason);
}

bool isDriving(State current);

// Only the loop mutates route/fork state. A normal pause freezes elapsed time;
// the independent control watchdog still runs and can cancel this snapshot.
void pauseTrip(unsigned long now) {
  if (state == State::Paused) return;
  pausedState = state;
  pausedAt = now;
  pausedMotorLeft = motorLeft;
  pausedMotorRight = motorRight;
  pausedForkMoving = forkMoving.load();
  forkMoving.store(false);
  clearRcCommand();
  drive(0, 0, true);
  publish(State::Paused);
}

void resumeTrip(unsigned long now) {
  if (state != State::Paused) return;
  const unsigned long elapsed = now - pausedAt;
  segmentAt += elapsed;
  phaseAt += elapsed;
  lineLostAt += elapsed;
  lineMissingAt += elapsed;
  rearCorrectionStartedAt += elapsed;
  forkStartedAt += elapsed;
  forkUpdatedAt += elapsed;
  forkMoving.store(pausedForkMoving);
  state = pausedState;
  // publish(Moving) would reset lastDetected. Preserve the complete route state.
  publishStatus();
  if (!rcMode.load() && isDriving(state)) drive(pausedMotorLeft, pausedMotorRight, true);
}

bool isDriving(State current) {
  return current == State::Moving || current == State::ReverseTesting;
}

bool setForkAngle(float angle) {
  constexpr float pulsePerDegree =
    static_cast<float>(SERVO_MAX_PULSE_US - SERVO_MIN_PULSE_US) / (SERVO_MAX_ANGLE - SERVO_MIN_ANGLE);
  const float pulseUs = SERVO_MIN_PULSE_US + pulsePerDegree * (angle - SERVO_MIN_ANGLE);
  constexpr uint32_t PERIOD_US = 1000000 / SERVO_PWM_HZ;
  const uint32_t duty = lroundf(pulseUs * (1u << SERVO_PWM_BITS) / PERIOD_US);
  if (ledcWrite(FORK_SERVO_PIN, duty)) {
    forkAngle = angle;
    servoAngle.store(lroundf(angle));
    return true;
  }
  servoAngle.store(-1);
  Serial.println("AGV fork servo PWM write failed");
  return false;
}

void startForkMove(int target, unsigned long now) {
  if (forkAngle == target) return;
  forkStartAngle = forkAngle;
  forkTargetAngle = target;
  forkStartedAt = forkUpdatedAt = now;
  forkMoving.store(true);
}

void updateForkMove(unsigned long now) {
  if (!forkMoving.load() || now - forkUpdatedAt < FORK_UPDATE_MS) return;
  forkUpdatedAt = now;

  const unsigned long elapsed = now - forkStartedAt;
  const bool finished = elapsed >= FORK_MOVE_MS;
  const float t = finished ? 1.0f : static_cast<float>(elapsed) / FORK_MOVE_MS;
  // 시작과 끝의 변화량을 줄여 포크가 부드럽게 출발하고 멈추게 한다.
  const float progress = t * t * (3.0f - 2.0f * t);
  const float angle = finished ? forkTargetAngle : forkStartAngle + (forkTargetAngle - forkStartAngle) * progress;
  if (!setForkAngle(angle) || finished) forkMoving.store(false);
}

void startFollowing(unsigned long now) {
  phase = Phase::Follow;
  segmentAt = now;
  recovering = false;
  lastLineSide = 0;
  rearLastLineSide = 0;
  readyForFeature = false;
  centeredReads = 0;
  matchReads = 0;
}

void startTurn(unsigned long now, bool right, int speed) {
  phase = Phase::Turn;
  phaseAt = now;
  turnRight = right;
  activeTurnSpeed = speed;
  centeredReads = 0;
  sawCenterLeave = false;
  pivot(activeTurnSpeed, turnRight);
  Serial.printf("AGV turn start: %s%s left=%d right=%d\n",
    reversing ? "reverse " : "", right ? "right" : "left", motorLeft, motorRight);
}

void startTrip(Node target, unsigned long now) {
  if (state == State::Moving || state == State::Inspecting || !canTravel(location, target)) return;
  if (!makeRoute(location, target)) return;
  inspectionTrip = false;
  lineMissing = false;

  Serial.printf("AGV route start: %d -> %d\n", location + 1, target + 1);
  destination = target;
  nextIndex = 1;
  reversing = location == POINT_1 || isDispatchPoint(location);
  location = NO_NODE;
  startFollowing(now);
  publish(State::Moving);
}

// No dark-room stop. A complete /go route proceeds directly through 8 -> 9.
bool checkLinePresence(bool seen, unsigned long now) {
  if (seen) {
    lineMissing = false;
    return true;
  }
  if (!lineMissing) {
    lineMissing = true;
    lineMissingAt = now;
  }
  if (now - lineMissingAt >= LINE_LOST_MS) {
    stopTrip(State::LineLost);
    return false;
  }
  return true;
}

void steerAlongLine(float error, int speedLimit, bool backward) {
  if (fabsf(error) <= 1.0f) {
    const int speed = backward ? -speedLimit : speedLimit;
    drive(speed, speed);
    return;
  }

  const int speed = min(speedLimit, 48 + static_cast<int>(8.0f * (fabsf(error) - 1.0f)));
  correctLine(speed, error > 0, backward);
}

void driveByRearLine(uint8_t rearMask) {
  // 후진 기준 왼쪽 GPIO35, 가운데 GPIO34, 오른쪽 GPIO36.
  const bool left = rearMask & 0x02;
  const bool center = rearMask & 0x04;
  const bool right = rearMask & 0x01;
  const int previousSide = rearLastLineSide;
  if (left != right) {
    rearLastLineSide = right ? 1 : -1;
  } else if (center || left || right) {
    rearLastLineSide = 0;
  }

  if (rearLastLineSide == 0) {
    drive(-REVERSE_SPEED, -REVERSE_SPEED);
    return;
  }

  if (previousSide == 0) rearCorrectionStartedAt = millis();
  // 5ms 보정과 5ms 직선 후진을 번갈아 적용해 보정 가동 시간을 절반으로 줄인다.
  const unsigned long cycleMs = 2 * REVERSE_CORRECTION_PULSE_MS;
  if ((millis() - rearCorrectionStartedAt) % cycleMs >= REVERSE_CORRECTION_PULSE_MS) {
    drive(-REVERSE_SPEED, -REVERSE_SPEED);
    return;
  }

  const int speed = center ? REVERSE_CORRECTION_SPEED : REVERSE_RECOVERY_SPEED;
  correctLine(speed, rearLastLineSide > 0, true);
}

void followLine(const Sample& sample, unsigned long now, int speedLimit) {
  if (sample.count > 0) {
    recovering = false;
    const float error = static_cast<float>(sample.positionSum) / sample.count;
    lastLineSide = (error > 0) - (error < 0);
    steerAlongLine(error, speedLimit, false);
    return;
  }

  if (!recovering) {
    recovering = true;
    lineLostAt = now;
  }
  if (now - lineLostAt >= LINE_LOST_MS) {
    stopTrip(State::LineLost);
  } else if (lastLineSide != 0) {
    correctLine(APPROACH_SPEED, lastLineSide > 0);
  } else {
    drive(0, 0);
  }
}

void startReverseTest() {
  lineMissing = false;
  rearLastLineSide = 0;
  reverseTestReadings.store(-1);
  location = destination = NO_NODE;
  publish(State::ReverseTesting);
}

void updateReverseTest() {
  const uint8_t rearMask = rearLineMask();
  if (!checkLinePresence(rearMask != 0, millis())) return;
  driveByRearLine(rearMask);
}

void trackSegment(const Sample& sample, unsigned long now) {
  const Node node = route[nextIndex];
  const bool deliveryTurn = node == JUNCTION_9 && route[nextIndex + 1] == JUNCTION_10;
  const bool isDestination = nextIndex == routeLength - 1;
  const bool station = isDestination || node == POINT_2;
  const bool stopMark = sample.stopBar();
  if (now - segmentAt >= SEGMENT_TIMEOUT_MS) {
    Serial.printf("AGV segment timeout: node=%d front=0x%02X\n",
      node + 1, static_cast<unsigned>(sample.bits));
    stopTrip(State::TimedOut);
    return;
  }

  if (!readyForFeature) {
    centeredReads = sample.centeredLine() ? centeredReads + 1 : 0;
    readyForFeature = centeredReads >= STABLE_READS;
    if (!readyForFeature && sample.count > 0 && now - segmentAt >= DEPARTURE_TIMEOUT_MS) {
      Serial.printf("AGV line not reacquired: node=%d front=0x%02X\n",
        node + 1, static_cast<unsigned>(sample.bits));
      stopTrip(State::LineLost);
      return;
    }
    if (!readyForFeature && (stopMark || (deliveryTurn && (sample.left() || sample.right())))) {
      driveAlongLine(APPROACH_SPEED);
      return;
    }
  }

  bool feature = false;
  uint8_t featureReads = STABLE_READS;
  if (readyForFeature) {
    if (station) {
      feature = stopMark;
    } else if (deliveryTurn) {
      // 왼쪽 바깥 또는 안쪽 분기 신호를 확인한다. 한 번의 샘플은 직선과 분기가 함께 보여야 한다.
      const bool innerBranch = (sample.bits & 0x0a) == 0x0a;
      feature = !sample.right() && (sample.left() || innerBranch);
      if ((sample.bits & 0x09) == 0x09) featureReads = 1;
    } else {
      const int heading = approachHeading();
      const bool hasLeft = NEIGHBORS[node][(heading + 3) % 4] != NO_NODE;
      const bool hasRight = NEIGHBORS[node][(heading + 1) % 4] != NO_NODE;
      feature = sample.center() && sample.left() == hasLeft && sample.right() == hasRight;
    }
  }
  matchReads = feature ? matchReads + 1 : 0;

  if (feature) {
    if (station) {
      // 첫 감지에서 즉시 제동하고 다음 샘플로 같은 정차선을 확인한다.
      drive(0, 0);
      if (matchReads == 1) {
        Serial.printf("AGV station mark: node=%d front=0x%02X\n",
          node + 1, static_cast<unsigned>(sample.bits));
      }
      if (matchReads >= STABLE_READS) {
        lastDetected = node;
        if (isDestination) {
          location = destination;
          destination = NO_NODE;
          publish(State::Arrived);
        } else {
          phase = Phase::ChangeDirection;
          phaseAt = now;
        }
      }
    } else if (matchReads >= featureReads) {
      lastDetected = node;
      phase = Phase::Center;
      phaseAt = now;
      centeredReads = 0;
      Serial.printf("AGV junction node=%d sensors=0x%02X next=%d\n",
        node + 1, static_cast<unsigned>(sample.bits), route[nextIndex + 1] + 1);
    }
    if (!station) driveAlongLine(APPROACH_SPEED);
    return;
  }

  // 양끝이 동시에 잡힌 넓은 구간에서 평균값으로 방향을 틀지 않는다.
  if (sample.stopBar() && readyForFeature) {
    Serial.printf("AGV unexpected mark: node=%d front=0x%02X\n",
      node + 1, static_cast<unsigned>(sample.bits));
    stopTrip(State::LineLost);
    return;
  }
  followLine(sample, now, FOLLOW_SPEED);
}

void trackReverseSegment(uint8_t rearMask, unsigned long now) {
  const Node node = route[nextIndex];
  const bool station = nextIndex == routeLength - 1 || node == POINT_2;
  const int heading = approachHeading();
  // 후진 진행 방향의 좌우는 차체 기준 좌우와 반대다.
  const bool hasLeft = NEIGHBORS[node][(heading + 3) % 4] != NO_NODE;
  const bool hasRight = NEIGHBORS[node][(heading + 1) % 4] != NO_NODE;
  const uint8_t expected = station ? 0x07 : 0x04 | (hasLeft ? 0x02 : 0) | (hasRight ? 0x01 : 0);
  const bool marker = (rearMask & 0x04) && (rearMask & 0x03);

  // 출발선과 지나온 분기에서 벗어나야 다음 분기를 감지한다.
  if (!readyForFeature) {
    centeredReads = marker ? 0 : centeredReads + 1;
    readyForFeature = centeredReads >= STABLE_READS;
    if (!readyForFeature) {
      driveByRearLine(rearMask);
      return;
    }
  }

  const bool feature = (rearMask & expected) == expected;
  matchReads = feature ? matchReads + 1 : 0;
  // 정차선은 짧게 지나가도 놓치지 않도록 첫 완전 감지에서 정지한다.
  const uint8_t requiredReads = station ? 1 : STABLE_READS;
  if (matchReads < requiredReads) {
    if (feature) driveAlongLine(APPROACH_SPEED);
    else driveByRearLine(rearMask);
    return;
  }

  lastDetected = node;
  matchReads = centeredReads = 0;
  readyForFeature = false;
  if (station) {
    drive(0, 0);
    if (nextIndex == routeLength - 1) {
      location = node;
      destination = NO_NODE;
      publish(State::Arrived);
    } else {
      phase = Phase::ChangeDirection;
      phaseAt = now;
    }
    return;
  }

  phase = nextTurn() == 0 ? Phase::Cross : Phase::Center;
  phaseAt = now;
  Serial.printf("AGV reverse junction: node=%d rear=0x%02X next=%d\n",
    node + 1, static_cast<unsigned>(rearMask), route[nextIndex + 1] + 1);
  driveAlongLine(APPROACH_SPEED);
}

void updateReverseTrip(uint8_t rearMask, unsigned long now) {
  switch (phase) {
    case Phase::Follow:
      trackReverseSegment(rearMask, now);
      break;
    case Phase::Center:
      // 뒤 센서가 코너를 읽은 뒤 차축까지 후진한다.
      driveAlongLine(APPROACH_SPEED);
      if (now - phaseAt >= REVERSE_CENTER_MS) {
        startTurn(now, nextTurn() == 1, TURN_SPEED);
        sawCenterLeave = !(rearMask & 0x04);
      }
      break;
    case Phase::Turn: {
      pivot(activeTurnSpeed, turnRight);
      if (!(rearMask & 0x04)) sawCenterLeave = true;
      // 전진과 같이 기존 선을 벗어난 뒤 가운데에서 새 직선을 확인한다.
      const bool lineFound = sawCenterLeave && rearMask == 0x04;
      centeredReads = lineFound && now - phaseAt >= TURN_MIN_MS ? centeredReads + 1 : 0;
      if (centeredReads >= STABLE_READS) {
        nextIndex++;
        startFollowing(now);
        driveByRearLine(rearMask);
      }
      break;
    }
    case Phase::Cross: {
      // 직진 통과 시 분기선의 좌우 신호에 끌려가지 않게 한다.
      driveAlongLine(APPROACH_SPEED);
      const bool marker = (rearMask & 0x04) && (rearMask & 0x03);
      centeredReads = marker ? 0 : centeredReads + 1;
      if (centeredReads >= STABLE_READS) {
        nextIndex++;
        startFollowing(now);
        readyForFeature = true;
      }
      break;
    }
    case Phase::ChangeDirection:
      if (now - phaseAt >= CHANGE_DIRECTION_MS) {
        reversing = false;
        nextIndex++;
        startFollowing(now);
      }
      break;
    case Phase::ExitTurn:
      break;
  }
  publishStatus();
}

void updateTrip(const Sample& sample, unsigned long now, uint8_t rearMask) {
  if (!checkLinePresence(reversing ? rearMask != 0 : sample.count > 0, now)) return;
  if (reversing) {
    updateReverseTrip(rearMask, now);
    return;
  }
  const Node node = route[nextIndex];
  const bool forward8To9 = node == JUNCTION_8 && route[nextIndex + 1] == JUNCTION_9;
  const bool junction = node >= JUNCTION_8 && node <= JUNCTION_11;
  const bool approaching9 = node == JUNCTION_9 &&
    (phase == Phase::Follow || phase == Phase::Center);
  static uint8_t lastFrontAt9 = 0xff;
  if (!approaching9) {
    lastFrontAt9 = 0xff;
  } else {
    if (phase == Phase::Follow && sample.bits != lastFrontAt9) {
      lastFrontAt9 = sample.bits;
      Serial.printf("AGV forward 9: target=%d front=0x%02X\n", destination + 1, static_cast<unsigned>(sample.bits));
    }
    if (route[nextIndex + 1] == JUNCTION_10 && readyForFeature && sample.left() && sample.right()) {
      Serial.printf("AGV node 9: expected left branch, front=0x%02X\n", static_cast<unsigned>(sample.bits));
      stopTrip(State::LineLost);
      return;
    }
  }
  switch (phase) {
    case Phase::Follow:
      trackSegment(sample, now);
      break;
    case Phase::Center:
      if (now - phaseAt < CENTER_MS) break;
      {
        const int delta = nextTurn();
        if (delta == 0) {
          phase = Phase::Cross;
          phaseAt = now;
          centeredReads = 0;
        } else {
          startTurn(now, delta == 1, TURN_SPEED);
        }
      }
      break;
    case Phase::Turn:
      {
        const bool centerOnLine = sample.center();
        if (!centerOnLine) sawCenterLeave = true;
        const bool lineFound = sawCenterLeave && sample.centeredLine();
        centeredReads = lineFound && now - phaseAt >= TURN_MIN_MS ? centeredReads + 1 : 0;
        if (centeredReads >= STABLE_READS) {
          Serial.printf("AGV turn complete: node=%d time=%lu ms %s=0x%02X\n",
            route[nextIndex] + 1, now - phaseAt, reversing ? "rear" : "front",
            static_cast<unsigned>(reversing ? rearMask : sample.bits));
          if (junction) {
            // 회전 중에 읽은 센서값과 회전 후 직선 확인을 분리한다.
            phase = Phase::ExitTurn;
            phaseAt = now;
            readyForFeature = false;
            centeredReads = 0;
            recovering = false;
            lastLineSide = 0;
          } else {
            nextIndex++;
            startFollowing(now);
            readyForFeature = true;
          }
          driveAlongLine(APPROACH_SPEED);
          break;
        }
      }
      pivot(activeTurnSpeed, turnRight);
      break;
    case Phase::ExitTurn: {
      const unsigned long elapsed = now - phaseAt;
      const unsigned long minimum = forward8To9 ? JUNCTION_9_DETECT_DELAY_MS : 0;
      if (sample.count == 0 || (!sample.centeredLine() && elapsed >= minimum + CROSS_TIMEOUT_MS)) {
        Serial.printf("AGV searching turn line again: node=%d front=0x%02X\n", node + 1,
          static_cast<unsigned>(sample.bits));
        startTurn(now, turnRight, APPROACH_SPEED);
        sawCenterLeave = !sample.center();
        break;
      }
      // 8번의 감지 대기 시간이 지난 뒤에도 새 직선 샘플을 확인한다.
      const bool centered = sample.centeredLine();
      centeredReads = elapsed >= minimum && centered ? centeredReads + 1 : 0;
      if (sample.left() || sample.right()) {
        recovering = false;
        driveAlongLine(APPROACH_SPEED);
      } else {
        followLine(sample, now, APPROACH_SPEED);
      }
      if (state == State::Moving && centeredReads >= TURN_EXIT_STABLE_READS) {
        nextIndex++;
        startFollowing(now);
        readyForFeature = true;
        Serial.printf("AGV junction cleared: node=%d next=%d\n", node + 1, route[nextIndex] + 1);
      }
      break;
    }
    case Phase::Cross:
      if (sample.count > 0 && now - phaseAt >= CROSS_TIMEOUT_MS) {
        Serial.printf("AGV crossing timeout: node=%d front=0x%02X\n", route[nextIndex] + 1,
          static_cast<unsigned>(sample.bits));
        stopTrip(State::TimedOut);
        break;
      }
      centeredReads = sample.centeredLine() ? centeredReads + 1 : 0;
      if (centeredReads >= STABLE_READS) {
        nextIndex++;
        startFollowing(now);
        readyForFeature = true;
      }
      driveAlongLine(APPROACH_SPEED);
      break;
    case Phase::ChangeDirection:
      if (now - phaseAt >= CHANGE_DIRECTION_MS) {
        reversing = false;
        nextIndex++;
        startFollowing(now);
      }
      break;
  }
  publishStatus();
}

esp_err_t showControlScript(httpd_req_t* request) {
  httpd_resp_set_type(request, "application/javascript; charset=utf-8");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  return httpd_resp_send(request, CONTROL_SCRIPT, HTTPD_RESP_USE_STRLEN);
}

bool requireControlKey(httpd_req_t* request);
bool sessionConnected(unsigned long now);
bool readController(httpd_req_t* request, char* controller, size_t size);

esp_err_t showControlPage(httpd_req_t* request) {
  httpd_resp_set_type(request, "text/html; charset=utf-8");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  httpd_resp_set_hdr(request, "X-Frame-Options", "DENY");
  return httpd_resp_send(request, CONTROL_PAGE, HTTPD_RESP_USE_STRLEN);
}

esp_err_t showStatus(httpd_req_t* request) {
  if (!requireControlKey(request)) return ESP_OK;
  const uint32_t value = publicStatus.load();
  const bool moving = (value & 0x0f) == static_cast<uint32_t>(State::Moving);
  char body[768];
  char operation[sizeof(activeOperationId)];
  portENTER_CRITICAL(&controlLock);
  memcpy(operation, activeOperationId, sizeof(operation));
  portEXIT_CRITICAL(&controlLock);
  snprintf(body, sizeof(body), "{\"boot_id\":\"%s\",\"operation_id\":\"%s\",\"state\":\"%s\",\"position\":%u,\"target\":%u,\"servo_angle\":%d,\"fork_moving\":%s,"
    "\"reverse_readings\":%d,\"mode\":\"%s\",\"next_node\":%u,\"phase\":\"%s\",\"reversing\":%s,\"turn_right\":%s,\"last_detected\":%u,"
    "\"control_connected\":%s,\"heartbeat_timeout_ms\":%lu,\"inspection_trip\":%s}",
    deviceBootId, operation, STATE_NAMES[value & 0x0f], static_cast<unsigned>((value >> 4) & 0x0f),
    static_cast<unsigned>((value >> 8) & 0x0f), servoAngle.load(), forkMoving.load() ? "true" : "false",
    reverseTestReadings.load(), rcMode.load() ? "rc" : "auto",
    static_cast<unsigned>((value >> 12) & 0x0f), moving ? PHASE_NAMES[(value >> 16) & 0x07] : "idle",
    (value & (1u << 19)) ? "true" : "false", (value & (1u << 20)) ? "true" : "false",
    static_cast<unsigned>((value >> 21) & 0x0f), sessionConnected(millis()) ? "true" : "false",
    SERVER_HEARTBEAT_TIMEOUT_MS, (value & (1u << 25)) ? "true" : "false");
  httpd_resp_set_type(request, "application/json; charset=utf-8");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  return httpd_resp_sendstr(request, body);
}

esp_err_t queueCommand(httpd_req_t* request, int command) {
  char controller[ControlSession::MAX_CONTROLLER_LENGTH + 1];
  if (!readController(request, controller, sizeof(controller))) {
    httpd_resp_set_status(request, "409 Conflict");
    return httpd_resp_sendstr(request, "Invalid controller ID");
  }
  char operation[sizeof(pendingOperationId)] = {};
  const size_t operationLength = httpd_req_get_hdr_value_len(request, "X-AGV-Operation");
  if (operationLength && (operationLength >= sizeof(operation) ||
      httpd_req_get_hdr_value_str(request, "X-AGV-Operation", operation, sizeof(operation)) != ESP_OK ||
      !ControlSession::validController(operation))) {
    return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Invalid operation ID");
  }
  int empty = 0;
  portENTER_CRITICAL(&controlLock);
  const bool queued = controlSession.permits(controller, millis()) &&
    pendingCommand.compare_exchange_strong(empty, command);
  if (queued) memcpy(pendingOperationId, operation, sizeof(operation));
  portEXIT_CRITICAL(&controlLock);
  if (!queued) {
    httpd_resp_set_status(request, "409 Conflict");
    return httpd_resp_sendstr(request, "Controller expired or another command is pending");
  }
  httpd_resp_set_status(request, "202 Accepted");
  return httpd_resp_send(request, nullptr, 0);
}


bool sessionConnected(unsigned long) {
  portENTER_CRITICAL(&controlLock);
  const bool connected = controlSession.connected(millis());
  portEXIT_CRITICAL(&controlLock);
  return connected;
}

bool controllerPermitted(const char* controller, unsigned long) {
  portENTER_CRITICAL(&controlLock);
  const bool permitted = controlSession.permits(controller, millis());
  portEXIT_CRITICAL(&controlLock);
  return permitted;
}

bool requireControlKey(httpd_req_t* request) {
  char authorization[112];
  if (httpd_req_get_hdr_value_str(request, "Authorization", authorization, sizeof(authorization)) == ESP_OK &&
      strncmp(authorization, "Bearer ", 7) == 0 && controlSession.authenticate(authorization + 7)) {
    return true;
  }
  httpd_resp_set_status(request, "401 Unauthorized");
  httpd_resp_sendstr(request, "A configured device control key is required");
  return false;
}

bool readController(httpd_req_t* request, char* controller, size_t size) {
  return httpd_req_get_hdr_value_str(request, "X-AGV-Controller", controller, size) == ESP_OK &&
    ControlSession::validController(controller);
}

bool requireController(httpd_req_t* request) {
  char controller[ControlSession::MAX_CONTROLLER_LENGTH + 1];
  if (readController(request, controller, sizeof(controller)) &&
      controllerPermitted(controller, millis())) {
    return true;
  }
  httpd_resp_set_status(request, "409 Conflict");
  httpd_resp_sendstr(request, "Claim a control session and keep its heartbeat alive");
  return false;
}

esp_err_t controlSessionRequest(httpd_req_t* request) {
  if (!requireControlKey(request)) return ESP_OK;
  char controller[ControlSession::MAX_CONTROLLER_LENGTH + 1];
  if (!readController(request, controller, sizeof(controller))) {
    return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Invalid controller ID");
  }
  portENTER_CRITICAL(&motionLock);
  const bool rcStopped = requestedMotion == Motion::Stop;
  portEXIT_CRITICAL(&motionLock);
  const bool idle = !isDriving(static_cast<State>(publicStatus.load() & 0x0f)) &&
    !forkMoving.load() && rcStopped && pendingCommand.load() == 0;
  portENTER_CRITICAL(&controlLock);
  const bool claimed = controlSession.claim(controller, millis(), idle);
  portEXIT_CRITICAL(&controlLock);
  httpd_resp_set_status(request, claimed ? "204 No Content" : "409 Conflict");
  return httpd_resp_sendstr(request, claimed ? "" : "Controller busy or timeout stop pending");
}

esp_err_t heartbeatRequest(httpd_req_t* request) {
  if (!requireControlKey(request)) return ESP_OK;
  char controller[ControlSession::MAX_CONTROLLER_LENGTH + 1];
  if (!readController(request, controller, sizeof(controller))) {
    return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Invalid controller ID");
  }
  portENTER_CRITICAL(&controlLock);
  const bool refreshed = controlSession.heartbeat(controller, millis());
  portEXIT_CRITICAL(&controlLock);
  httpd_resp_set_status(request, refreshed ? "204 No Content" : "409 Conflict");
  return httpd_resp_sendstr(request, refreshed ? "" : "Control session expired or owned by another controller");
}

esp_err_t inspectionRequest(httpd_req_t* request) {
  if (!requireControlKey(request) || !requireController(request)) return ESP_OK;
  httpd_resp_set_status(request, "410 Gone");
  return httpd_resp_sendstr(request, "Dark-room inspection is disabled; use /go with the final destination");
}

esp_err_t pauseRequest(httpd_req_t* request) {
  if (!requireControlKey(request) || !requireController(request)) return ESP_OK;
  return queueCommand(request, PAUSE_COMMAND);
}

esp_err_t resumeRequest(httpd_req_t* request) {
  if (!requireControlKey(request) || !requireController(request)) return ESP_OK;
  if ((publicStatus.load() & 0x0f) != static_cast<uint32_t>(State::Paused)) {
    httpd_resp_set_status(request, "409 Conflict");
    return httpd_resp_sendstr(request, "No normal pause to resume");
  }
  return queueCommand(request, RESUME_COMMAND);
}

void updateControlWatchdog(unsigned long) {
  portENTER_CRITICAL(&controlLock);
  // Sample time under the same lock as heartbeat updates. An older loop
  // timestamp would otherwise underflow after a concurrent newer heartbeat.
  const bool stop = controlSession.takeStopRequest(millis());
  portEXIT_CRITICAL(&controlLock);
  if (!stop) return;
  portENTER_CRITICAL(&motionLock);
  const bool rcMoving = requestedMotion != Motion::Stop;
  portEXIT_CRITICAL(&motionLock);
  const bool active = isDriving(state) || forkMoving.load() || rcMoving || state == State::Paused;
  pendingCommand.store(0);
  forkMoving.store(false);
  clearRcCommand();
  drive(0, 0, true);
  if (active) {
    inspectionTrip = false;
    stopTrip(State::ControlLost);
  }
}

struct ControlSocketContext {
  char controller[ControlSession::MAX_CONTROLLER_LENGTH + 1];
};

esp_err_t handleCommand(httpd_req_t* request) {
  if (!requireControlKey(request)) return ESP_OK;

  const WebCommand action = *static_cast<const WebCommand*>(request->user_ctx);
  if (action == WebCommand::Stop) {
    pendingCommand.store(STOP_COMMAND);
    clearRcCommand();
    httpd_resp_set_status(request, "202 Accepted");
    return httpd_resp_send(request, nullptr, 0);
  }

  if (!requireController(request)) return ESP_OK;

  if ((publicStatus.load() & 0x0f) == static_cast<uint32_t>(State::Paused)) {
    httpd_resp_set_status(request, "409 Conflict");
    return httpd_resp_sendstr(request, "Resume or cancel the paused operation first");
  }

  if (action == WebCommand::Mode) {
    char query[16];
    char mode[5];
    if (httpd_req_get_url_query_str(request, query, sizeof(query)) == ESP_OK &&
        httpd_query_key_value(query, "value", mode, sizeof(mode)) == ESP_OK) {
      if (strcmp(mode, "auto") == 0) return queueCommand(request, AUTO_MODE_COMMAND);
      if (strcmp(mode, "rc") == 0) return queueCommand(request, RC_MODE_COMMAND);
    }
    return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Invalid drive mode");
  }
  const bool rcServo = action == WebCommand::RcServo;
  if (rcMode.load() != rcServo) {
    httpd_resp_set_status(request, "409 Conflict");
    return httpd_resp_sendstr(request, rcServo ? "Select RC mode first" : "Select automatic mode first");
  }
  const uint32_t status = publicStatus.load();
  if (isDriving(static_cast<State>(status & 0x0f)) || forkMoving.load()) {
    httpd_resp_set_status(request, "409 Conflict");
    return httpd_resp_sendstr(request, "AGV is busy");
  }

  if (action == WebCommand::ReverseTest) return queueCommand(request, REVERSE_TEST_COMMAND);

  char query[24];
  if (httpd_req_get_url_query_str(request, query, sizeof(query)) != ESP_OK) {
    return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Missing command parameter");
  }
  if (action == WebCommand::Servo || rcServo) {
    if (servoAngle.load() < 0) {
      return httpd_resp_send_err(request, HTTPD_500_INTERNAL_SERVER_ERROR, "Servo PWM unavailable");
    }
    if (rcServo && !rcDriveStopped()) {
      httpd_resp_set_status(request, "409 Conflict");
      return httpd_resp_sendstr(request, "Stop the AGV first");
    }
    char value[4];
    const esp_err_t angleResult = httpd_query_key_value(query, "angle", value, sizeof(value));
    if (!rcServo && angleResult == ESP_ERR_NOT_FOUND) {
      char position[6];
      if (httpd_query_key_value(query, "position", position, sizeof(position)) == ESP_OK) {
        if (strcmp(position, "lower") == 0) return queueCommand(request, FORK_LOWER_COMMAND);
        if (strcmp(position, "lift") == 0) return queueCommand(request, FORK_LIFT_COMMAND);
      }
      return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Invalid fork position");
    }
    if (angleResult != ESP_OK ||
        value[0] == '\0' || value[strspn(value, "0123456789")] != '\0') {
      return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Angle must be an integer from 0 to 180");
    }
    const int angle = atoi(value);
    if (angle > SERVO_MAX_ANGLE) {
      return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Angle must be an integer from 0 to 180");
    }
    return queueCommand(request, (rcServo ? RC_FORK_ANGLE_COMMAND_BASE : FORK_ANGLE_COMMAND_BASE) + angle);
  }

  char point[3];
  if (httpd_query_key_value(query, "point", point, sizeof(point)) != ESP_OK ||
      strlen(point) != 1 || point[0] < '1' || point[0] > '0' + POINT_COUNT) {
    return httpd_resp_send_err(request, HTTPD_400_BAD_REQUEST, "Point must be 1-7");
  }
  if (action == WebCommand::SetPosition) {
    return queueCommand(request, point[0] - '0' + SET_POSITION_OFFSET);
  }
  const Node current = static_cast<Node>(static_cast<int>((status >> 4) & 0x0f) - 1);
  const Node target = static_cast<Node>(point[0] - '1');
  const bool fromInspection = static_cast<State>(status & 0x0f) == State::Inspecting;
  if (fromInspection || !canTravel(current, target)) {
    httpd_resp_set_status(request, "409 Conflict");
    return httpd_resp_sendstr(request, "Check the start position and destination");
  }
  return queueCommand(request, point[0] - '0');
}

esp_err_t showRcPage(httpd_req_t* request) {
  String page = RC_PAGE;
  page.replace("{{CONTROL_PATH}}", controlPath);
  page.replace("{{MIN_SPEED}}", String(MIN_DRIVE_SPEED));
  page.replace("{{MAX_SPEED}}", String(MAX_DRIVE_SPEED));
  page.replace("{{DEFAULT_SPEED}}", String(DEFAULT_DRIVE_SPEED));
  httpd_resp_set_type(request, "text/html; charset=utf-8");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  httpd_resp_set_hdr(request, "X-Frame-Options", "DENY");
  return httpd_resp_send(request, page.c_str(), page.length());
}

esp_err_t handleControlSocket(httpd_req_t* request) {
  if (request->method == HTTP_GET) {
    char query[224];
    char key[97];
    char controller[ControlSession::MAX_CONTROLLER_LENGTH + 1];
    if (httpd_req_get_url_query_str(request, query, sizeof(query)) != ESP_OK ||
        httpd_query_key_value(query, "key", key, sizeof(key)) != ESP_OK ||
        httpd_query_key_value(query, "controller", controller, sizeof(controller)) != ESP_OK ||
        !controlSession.authenticate(key) || !controllerPermitted(controller, millis())) {
      httpd_resp_set_status(request, "403 Forbidden");
      return httpd_resp_sendstr(request, "Unauthorized control connection");
    }
    auto* context = static_cast<ControlSocketContext*>(calloc(1, sizeof(ControlSocketContext)));
    if (context == nullptr) return ESP_ERR_NO_MEM;
    strcpy(context->controller, controller);
    request->sess_ctx = context;
    request->free_ctx = free;
    return ESP_OK;
  }
  const auto* context = static_cast<const ControlSocketContext*>(request->sess_ctx);
  if (context == nullptr || !controllerPermitted(context->controller, millis())) return ESP_FAIL;

  httpd_ws_frame_t frame{};
  esp_err_t result = httpd_ws_recv_frame(request, &frame, 0);
  if (result != ESP_OK) return result;
  if (frame.type != HTTPD_WS_TYPE_TEXT || frame.len < 1 || frame.len > 4) return ESP_FAIL;

  uint8_t payload[4];
  frame.payload = payload;
  result = httpd_ws_recv_frame(request, &frame, sizeof(payload));
  if (result != ESP_OK) return result;

  const uint8_t command = payload[0];
  const bool driving = command == 'F' || command == 'B' || command == 'L' || command == 'R';
  int speed = DEFAULT_DRIVE_SPEED;
  if (frame.len > 1) {
    if (!driving) return ESP_FAIL;
    speed = 0;
    for (size_t i = 1; i < frame.len; ++i) {
      if (payload[i] < '0' || payload[i] > '9') return ESP_FAIL;
      speed = speed * 10 + (payload[i] - '0');
    }
    if (speed < MIN_DRIVE_SPEED || speed > MAX_DRIVE_SPEED) return ESP_FAIL;
  }

  const int sender = httpd_req_to_sockfd(request);
  const bool connected = WiFi.status() == WL_CONNECTED;
  // The payload read may block past expiry. Revalidate at the mutation and
  // keep timeout handling serialized until the RC command is committed.
  portENTER_CRITICAL(&controlLock);
  if (!controlSession.permits(context->controller, millis())) {
    portEXIT_CRITICAL(&controlLock);
    return ESP_FAIL;
  }
  portENTER_CRITICAL(&motionLock);
  if (!rcMode.load() || !connected) {
    portEXIT_CRITICAL(&motionLock);
    portEXIT_CRITICAL(&controlLock);
    httpd_ws_frame_t reply{};
    char inactive[] = "inactive";
    reply.type = HTTPD_WS_TYPE_TEXT;
    reply.payload = reinterpret_cast<uint8_t*>(inactive);
    reply.len = sizeof(inactive) - 1;
    return httpd_ws_send_frame(request, &reply);
  }
  if (pendingCommand.load() != 0 || rcStopPending || forkMoving.load() ||
      (publicStatus.load() & 0x0f) == static_cast<uint32_t>(State::Paused)) {
    portEXIT_CRITICAL(&motionLock);
    portEXIT_CRITICAL(&controlLock);
    return ESP_OK;
  }
  const unsigned long now = millis();
  switch (command) {
    case 'F':
    case 'B':
    case 'L':
    case 'R':
      requestedMotion = static_cast<Motion>(command);
      requestedSpeed = static_cast<uint8_t>(speed);
      lastCommandAt = now;
      controllerSocket = sender;
      break;
    case 'S':
      if (controllerSocket == sender) {
        requestedMotion = Motion::Stop;
        controllerSocket = -1;
        rcStopPending = true;
      }
      break;
    case 'K':
      if (controllerSocket == sender && now - lastCommandAt < COMMAND_TIMEOUT_MS) {
        lastCommandAt = now;
      }
      break;
  }
  portEXIT_CRITICAL(&motionLock);
  portEXIT_CRITICAL(&controlLock);
  return ESP_OK;
}

bool startControlServer() {
  snprintf(controlPath, sizeof(controlPath), "/control-%08lx%08lx",
    static_cast<unsigned long>(esp_random()), static_cast<unsigned long>(esp_random()));
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.lru_purge_enable = true;
  config.max_uri_handlers = 17;
  httpd_handle_t server = nullptr;
  if (httpd_start(&server, &config) != ESP_OK) return false;
  const httpd_uri_t routes[] = {
    {.uri = "/", .method = HTTP_GET, .handler = showControlPage, .user_ctx = nullptr},
    {.uri = "/status", .method = HTTP_GET, .handler = showStatus, .user_ctx = nullptr},
    {.uri = "/control/session", .method = HTTP_POST, .handler = controlSessionRequest, .user_ctx = nullptr},
    {.uri = "/heartbeat", .method = HTTP_POST, .handler = heartbeatRequest, .user_ctx = nullptr},
    {.uri = "/inspection", .method = HTTP_POST, .handler = inspectionRequest, .user_ctx = nullptr},
    {.uri = "/pause", .method = HTTP_POST, .handler = pauseRequest, .user_ctx = nullptr},
    {.uri = "/resume", .method = HTTP_POST, .handler = resumeRequest, .user_ctx = nullptr},
    {.uri = "/control.js", .method = HTTP_GET, .handler = showControlScript, .user_ctx = nullptr},
    {.uri = "/position", .method = HTTP_POST, .handler = handleCommand, .user_ctx = &setPositionRequest},
    {.uri = "/go", .method = HTTP_POST, .handler = handleCommand, .user_ctx = &goRequest},
    {.uri = "/reverse-test", .method = HTTP_POST, .handler = handleCommand, .user_ctx = &reverseTestRequest},
    {.uri = "/servo", .method = HTTP_POST, .handler = handleCommand, .user_ctx = &servoRequest},
    {.uri = "/rc/servo", .method = HTTP_POST, .handler = handleCommand, .user_ctx = &rcServoRequest},
    {.uri = "/rc", .method = HTTP_GET, .handler = showRcPage, .user_ctx = nullptr},
    {.uri = controlPath, .method = HTTP_GET, .handler = handleControlSocket,
      .user_ctx = nullptr, .is_websocket = true},
    {.uri = "/mode", .method = HTTP_POST, .handler = handleCommand, .user_ctx = &modeRequest},
    {.uri = "/stop", .method = HTTP_POST, .handler = handleCommand, .user_ctx = &stopRequest},
  };
  for (const httpd_uri_t& routeHandler : routes) {
    if (httpd_register_uri_handler(server, &routeHandler) == ESP_OK) continue;
    httpd_stop(server);
    return false;
  }
  return true;
}

void setup() {
  uint8_t boot[16];
  esp_fill_random(boot, sizeof(boot));
  boot[6] = (boot[6] & 0x0f) | 0x40;
  boot[8] = (boot[8] & 0x3f) | 0x80;
  snprintf(deviceBootId, sizeof(deviceBootId),
    "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
    boot[0], boot[1], boot[2], boot[3], boot[4], boot[5], boot[6], boot[7],
    boot[8], boot[9], boot[10], boot[11], boot[12], boot[13], boot[14], boot[15]);
  Serial.begin(115200);
  Serial2.begin(9600, SERIAL_8N1, MOTOR_RX, MOTOR_TX);
  for (const LineSensor& sensor : SENSORS) pinMode(sensor.pin, INPUT);
  pinMode(REAR_RIGHT_PIN, INPUT);
  pinMode(REAR_LEFT_PIN, INPUT);
  pinMode(REAR_CENTER_PIN, INPUT);
  drive(0, 0, true);
  publish(State::Unknown);
  if (ledcAttach(FORK_SERVO_PIN, SERVO_PWM_HZ, SERVO_PWM_BITS)) {
    setForkAngle(FORK_LOWERED_ANGLE);
  } else {
    Serial.println("AGV fork servo PWM setup failed");
  }
  Serial.println("Connecting to 2.4 GHz Wi-Fi...");
  if (strlen(CONTROL_KEY) < 32) Serial.println("Configure a private device key in agv_secrets.h before controlling the AGV.");
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  serverReady = startControlServer();
}

void loop() {
  static bool wasConnected = false;
  static unsigned long lastWifiReport = 0;
  static unsigned long lastControl = 0;
  const unsigned long now = millis();
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (connected != wasConnected || now - lastWifiReport >= WIFI_REPORT_MS) {
    lastWifiReport = now;
    if (!connected) {
      Serial.println("Wi-Fi disconnected. Check the network and credentials.");
    } else if (serverReady) {
      Serial.print("AGV route test: http://");
      Serial.println(WiFi.localIP());
    } else {
      Serial.println("AGV HTTP server failed to start.");
    }
    wasConnected = connected;
  }

  if (!connected || !serverReady) {
    portENTER_CRITICAL(&controlLock);
    controlSession.revoke();
    portEXIT_CRITICAL(&controlLock);
    pendingCommand.store(0);
    forkMoving.store(false);
    if (rcMode.load()) {
      clearRcCommand();
      drive(0, 0);
    }
    if (isDriving(state) || state == State::Paused) stopTrip(State::WifiLost);
    delay(1);
    return;
  }

  updateControlWatchdog(now);
  char operation[sizeof(pendingOperationId)] = {};
  portENTER_CRITICAL(&controlLock);
  int command = pendingCommand.exchange(0);
  memcpy(operation, pendingOperationId, sizeof(operation));
  pendingOperationId[0] = '\0';
  portEXIT_CRITICAL(&controlLock);
  if (command != STOP_COMMAND && !sessionConnected(now)) command = 0;
  // Recheck expiry after draining the queue so a late request cannot restart motion.
  updateControlWatchdog(now);
  if (command != STOP_COMMAND && !sessionConnected(now)) command = 0;
  if (command != 0) {
    portENTER_CRITICAL(&controlLock);
    memcpy(activeOperationId, operation, sizeof(operation));
    portEXIT_CRITICAL(&controlLock);
  }
  if (command == STOP_COMMAND) {
    // 마지막 각도의 PWM을 유지한다.
    forkMoving.store(false);
    if (rcMode.load()) {
      clearRcCommand();
      drive(0, 0, true);
    }
    stopTrip(State::Stopped); // Cancellation includes idle/legacy inspection waits.
  } else if (command == PAUSE_COMMAND) {
    pauseTrip(now);
  } else if (command == RESUME_COMMAND) {
    resumeTrip(now);
  } else if (command == AUTO_MODE_COMMAND || command == RC_MODE_COMMAND) {
    const bool manual = command == RC_MODE_COMMAND;
    if (manual != rcMode.load()) {
      rcMode.store(false);
      clearRcCommand();
      forkMoving.store(false);
      location = NO_NODE;
      stopTrip(State::Stopped);
      if (manual) updateRcDrive();
      rcMode.store(manual);
    }
  } else if (command >= RC_FORK_ANGLE_COMMAND_BASE && command <= RC_FORK_ANGLE_COMMAND_BASE + SERVO_MAX_ANGLE) {
    if (rcMode.load() && !forkMoving.load() && servoAngle.load() >= 0) {
      portENTER_CRITICAL(&motionLock);
      if (requestedMotion == Motion::Stop && !rcStopPending) {
        startForkMove(command - RC_FORK_ANGLE_COMMAND_BASE, now);
      }
      portEXIT_CRITICAL(&motionLock);
    }
  } else if (!rcMode.load() && !isDriving(state) && !forkMoving.load()) {
    if ((command == FORK_LOWER_COMMAND || command == FORK_LIFT_COMMAND) && servoAngle.load() >= 0) {
      startForkMove(command == FORK_LIFT_COMMAND ? FORK_LIFTED_ANGLE : FORK_LOWERED_ANGLE, now);
    } else if (command >= FORK_ANGLE_COMMAND_BASE && command <= FORK_ANGLE_COMMAND_BASE + SERVO_MAX_ANGLE &&
               servoAngle.load() >= 0) {
      startForkMove(command - FORK_ANGLE_COMMAND_BASE, now);
    } else if (command == INSPECTION_COMMAND) {
      stopTrip(State::Stopped); // Reject any legacy queued inspection command.
    } else if (command == REVERSE_TEST_COMMAND) {
      startReverseTest();
    } else if (command > SET_POSITION_OFFSET && command <= SET_POSITION_OFFSET + POINT_COUNT) {
      location = static_cast<Node>(command - SET_POSITION_OFFSET - 1);
      destination = NO_NODE;
      inspectionTrip = false;
      publish(State::Ready);
    } else if (command > 0 && command <= POINT_COUNT) {
      startTrip(static_cast<Node>(command - 1), now);
    }
  }

  if (state == State::Paused) {
    delay(1);
    return;
  }
  updateForkMove(now);
  if (rcMode.load()) {
    if (now - lastControl >= MOTOR_UPDATE_MS) {
      lastControl = now;
      updateRcDrive();
    }
    delay(1);
    return;
  }
  const unsigned long controlInterval =
    state == State::ReverseTesting || reversing ? REVERSE_CONTROL_INTERVAL_MS : CONTROL_INTERVAL_MS;
  if (isDriving(state) && now - lastControl >= controlInterval) {
    lastControl = now;
    if (state == State::ReverseTesting) updateReverseTest();
    else if (reversing) updateTrip(Sample{}, now, rearLineMask());
    else updateTrip(readLine(), now, 0);
  }
  delay(1);
}
