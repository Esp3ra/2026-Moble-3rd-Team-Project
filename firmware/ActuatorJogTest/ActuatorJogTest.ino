/*
  UNO + L298N standalone test. Commands: a = one round trip, s = stop.
  Serial 115200 / New Line or CRLF. No arming command required.
  ENA D9 (jumper removed), IN1 D7, IN2 D8, common GND.
  Conveyor motor power stays disconnected. No physical E-stop/limit sensing.
*/
#include <Arduino.h>

constexpr uint8_t ENA = 9, IN1 = 7, IN2 = 8;
constexpr unsigned long AUTO_FORWARD_MS = 11500UL;
constexpr unsigned long AUTO_REVERSE_MS = 11500UL;
constexpr unsigned long DIRECTION_PAUSE_MS = 500UL;
constexpr uint8_t SPEED = 255;
// Existing J electrical direction. This does not identify physical position.
constexpr bool AUTO_FIRST_IS_J = true;

enum class CyclePhase : uint8_t { IDLE, FIRST, PAUSE, RETURNING };
CyclePhase cyclePhase = CyclePhase::IDLE;
unsigned long phaseStartedAt = 0, lastByteAt = 0;
char commandByte = 0;
uint8_t lineLength = 0;

void emit(const __FlashStringHelper* message) {
  const size_t needed = strlen_P(reinterpret_cast<PGM_P>(message)) + 2;
  if (Serial.availableForWrite() >= static_cast<int>(needed)) Serial.println(message);
}

void clearPending() {
  lineLength = 0;
  for (uint8_t i = 0; i < 64 && Serial.available(); ++i) Serial.read();
}

void disableOutput() {
  analogWrite(ENA, 0);
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
}

void stopMotor() {
  disableOutput();
  cyclePhase = CyclePhase::IDLE;
  clearPending();
}

void startMotion(bool directionJ) {
  // Output is disabled before every direction change.
  disableOutput();
  digitalWrite(IN1, directionJ ? HIGH : LOW);
  digitalWrite(IN2, directionJ ? LOW : HIGH);
  phaseStartedAt = millis();
  analogWrite(ENA, SPEED);
}

void handleCommand(char c) {
  if (c == 's') {
    stopMotor();
    emit(F("STOPPED:READY_A"));
    return;
  }
  if (c != 'a') {
    emit(F("ERR:USE_A_OR_S"));
    return;
  }
  if (cyclePhase != CyclePhase::IDLE) {
    emit(F("BUSY:NO_QUEUED_RESTART"));
    return;
  }
  cyclePhase = CyclePhase::FIRST;
  startMotion(AUTO_FIRST_IS_J);
  clearPending();
  emit(F("AUTO:FIRST_11500MS"));
}

void serviceMotion() {
  const unsigned long elapsed = millis() - phaseStartedAt;
  if (cyclePhase == CyclePhase::FIRST && elapsed >= AUTO_FORWARD_MS) {
    disableOutput();
    cyclePhase = CyclePhase::PAUSE;
    phaseStartedAt = millis();
    emit(F("AUTO:PAUSE_500MS"));
  } else if (cyclePhase == CyclePhase::PAUSE && elapsed >= DIRECTION_PAUSE_MS) {
    cyclePhase = CyclePhase::RETURNING;
    startMotion(!AUTO_FIRST_IS_J);
    emit(F("AUTO:RETURN_11500MS"));
  } else if (cyclePhase == CyclePhase::RETURNING && elapsed >= AUTO_REVERSE_MS) {
    stopMotor();
    emit(F("AUTO_DONE:READY_A"));
  }
}

void setup() {
  digitalWrite(ENA, LOW); digitalWrite(IN1, LOW); digitalWrite(IN2, LOW);
  pinMode(ENA, OUTPUT); pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
  digitalWrite(5, LOW); pinMode(5, OUTPUT);
  digitalWrite(6, LOW); pinMode(6, OUTPUT);
  Serial.begin(115200);
  stopMotor();
  emit(F("READY A=ROUND_TRIP S=STOP"));
}

void loop() {
  if (lineLength && millis() - lastByteAt >= 500UL) lineLength = 0;
  // Bound parsing so motion timing and stop servicing cannot be starved.
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
  // A pending s is processed before admitting an automatic reversal.
  serviceMotion();
}
