/*
  UNO + L298N channel A: unloaded actuator commissioning only.
  ENA D9 (jumper removed), IN1 D7, IN2 D8, common GND.
  Conveyor driver power MUST be disconnected during this test.
  No physical E-stop or limit input is configured in this sketch.
  This is not the integrated controller or a safety-rated stop.
  Serial: 115200, one lowercase command per line, New Line/CRLF.
  r: arm one jog, j/k: opposite directions for 2000ms, s: stop, e: latch.
  a: one timed J / pause / K cycle; q/w: fine 500ms jog.
  d/u: bounded long J/K test, disabled until both end cutoffs are verified.
  No command detects position or guarantees full stroke.
*/
#include <Arduino.h>

constexpr uint8_t ENA = 9, IN1 = 7, IN2 = 8;
constexpr unsigned long JOG_MS = 2000UL;
constexpr unsigned long FINE_JOG_MS = 500UL;
// 100mm / rated 8mm/s = 12.5s ideal no-load travel. 15s is only a
// provisional time ceiling, NOT proof that this unit reaches full stroke.
constexpr unsigned long FULL_STROKE_TEST_MS = 15000UL;
// Product information does not confirm built-in cutoff at BOTH ends.
// Keep false until this is verified from the maker/seller or a proper limit circuit.
constexpr bool END_CUTOFF_VERIFIED = false;
constexpr unsigned long AUTO_FORWARD_MS = 5000UL;
constexpr unsigned long AUTO_REVERSE_MS = 5000UL;
constexpr unsigned long DIRECTION_PAUSE_MS = 500UL;
// J is not verified as physical extension. Set false to start in K direction.
constexpr bool AUTO_FIRST_IS_J = true;
constexpr unsigned long ARM_MS = 10000UL;
constexpr uint8_t SPEED = 255;
enum class CyclePhase : uint8_t { IDLE, FIRST, PAUSE, RETURNING };
CyclePhase cyclePhase = CyclePhase::IDLE;
unsigned long motionDuration = JOG_MS, pausedAt = 0;
bool running = false, armed = false, locked = true;
unsigned long startedAt = 0, armedAt = 0, lastByteAt = 0;
char commandByte = 0;
uint8_t lineLength = 0;

void emit(const __FlashStringHelper* message) {
  const size_t needed = strlen_P(reinterpret_cast<PGM_P>(message)) + 2;
  if (Serial.availableForWrite() >= static_cast<int>(needed)) {
    Serial.println(message);
  }
}

void clearPending() {
  lineLength = 0;
  for (uint8_t i = 0; i < 64 && Serial.available(); ++i) Serial.read();
}

void disableOutput() {
  analogWrite(ENA, 0);  // Disable bridge before changing direction inputs.
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  running = false;
}

void stopMotor() {
  disableOutput();
  cyclePhase = CyclePhase::IDLE;
  armed = false;
  locked = true;
  clearPending();
}

void startMotion(bool directionJ, unsigned long duration) {
  // Called only from idle/disabled outputs, never reverse an enabled bridge.
  digitalWrite(IN1, directionJ ? HIGH : LOW);
  digitalWrite(IN2, directionJ ? LOW : HIGH);
  startedAt = millis();
  motionDuration = duration;
  running = true;
  analogWrite(ENA, SPEED);
}

void serviceMotion() {
  if (running && millis() - startedAt >= motionDuration) {
    disableOutput();
    if (cyclePhase == CyclePhase::FIRST) {
      cyclePhase = CyclePhase::PAUSE;
      pausedAt = millis();
      emit(F("AUTO:PAUSE"));
    } else {
      const bool cycleDone = cyclePhase == CyclePhase::RETURNING;
      stopMotor();
      emit(cycleDone ? F("AUTO_DONE:LOCKED_R_REQUIRED")
                     : F("JOG_DONE:LOCKED_R_REQUIRED"));
    }
  }
  if (cyclePhase == CyclePhase::PAUSE &&
      millis() - pausedAt >= DIRECTION_PAUSE_MS) {
    cyclePhase = CyclePhase::RETURNING;
    startMotion(!AUTO_FIRST_IS_J, AUTO_REVERSE_MS);
    emit(F("AUTO:RETURNING"));
  }
}

void handleCommand(char c) {
  if (c == 's' || c == 'e') {
    stopMotor();
    emit(c == 's' ? F("STOP:LOCKED") : F("SOFTWARE_STOP:LOCKED"));
    return;
  }
  if (c == '?') {
    if (cyclePhase != CyclePhase::IDLE) emit(F("AUTO:ACTIVE"));
    else emit(running ? F("RUNNING") : (armed ? F("ARMED") : F("LOCKED:R_REQUIRED")));
    return;
  }
  if ((c == 'd' || c == 'u') && !END_CUTOFF_VERIFIED) {
    emit(F("ERR:END_CUTOFF_UNVERIFIED_USE_Q_W"));
    return;
  }
  if (c != 'r' && c != 'j' && c != 'k' && c != 'a' &&
      c != 'q' && c != 'w' && c != 'd' && c != 'u') {
    emit(F("ERR:UNKNOWN_COMMAND"));
    return;
  }
  if (running || cyclePhase != CyclePhase::IDLE) {
    emit(F("ERR:BUSY"));
    return;
  }
  if (c == 'r') {
    stopMotor();
    locked = false;
    armed = true;
    armedAt = millis();
    emit(F("ARMED:ONE_RUN_WITHIN_10S"));
    return;
  }
  if (locked || !armed || millis() - armedAt >= ARM_MS) {
    stopMotor();
    emit(F("ERR:SEND_R_FIRST"));
    return;
  }
  armed = false;
  if (c == 'a') {
    cyclePhase = CyclePhase::FIRST;
    startMotion(AUTO_FIRST_IS_J, AUTO_FORWARD_MS);
    emit(F("AUTO:FIRST"));
  } else if (c == 'q' || c == 'w') {
    startMotion(c == 'q', FINE_JOG_MS);
    emit(c == 'q' ? F("FINE:J_500MS") : F("FINE:K_500MS"));
  } else if (c == 'd' || c == 'u') {
    startMotion(c == 'd', FULL_STROKE_TEST_MS);
    emit(c == 'd' ? F("LONG:J_TIME_LIMIT") : F("LONG:K_TIME_LIMIT"));
  } else {
    startMotion(c == 'j', JOG_MS);
    emit(c == 'j' ? F("JOG:J_2000MS") : F("JOG:K_2000MS"));
  }
  clearPending();
}

void setup() {
  // Keep all outputs inactive before changing their pin modes.
  digitalWrite(ENA, LOW);
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  pinMode(ENA, OUTPUT);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  // Preserve the existing conveyor STEP/DIR wiring; never generate pulses.
  digitalWrite(5, LOW);
  pinMode(5, OUTPUT);
  digitalWrite(6, LOW);
  pinMode(6, OUTPUT);
  Serial.begin(115200);
  stopMotor();
  emit(F("BOOT:LOCKED R=ARM J/K=JOG A=AUTO S=STOP"));
}

void loop() {
  if (armed && millis() - armedAt >= ARM_MS) {
    stopMotor();
    emit(F("ARM_EXPIRED:LOCKED"));
  }
  if (lineLength && millis() - lastByteAt >= 500UL) lineLength = 0;
  // Bound parsing time and avoid delay()/blocking motion loops.
  for (uint8_t i = 0; i < 8 && Serial.available(); ++i) {
    const char c = static_cast<char>(Serial.read());
    lastByteAt = millis();
    if (c == '\n' || c == '\r') {
      const bool valid = lineLength == 1;
      const bool nonempty = lineLength != 0;
      lineLength = 0;
      if (valid) handleCommand(commandByte);
      else if (nonempty) emit(F("ERR:ONE_COMMAND_PER_LINE"));
    } else {
      if (lineLength == 0) commandByte = c;
      if (lineLength < 2) ++lineLength;
    }
  }
  // Process queued stop commands before admitting an automatic reversal.
  serviceMotion();
}
