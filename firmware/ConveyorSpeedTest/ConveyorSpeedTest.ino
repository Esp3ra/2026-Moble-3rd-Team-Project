/*
  UNO / STEP D5 / DIR D6 conveyor speed trials. a=start, s=stop.
  Each NEW a after a timed run tries the next target, never auto-repeats.
  Pulse targets are not measured belt speed and timed completion is not a pass.
  Disconnect actuator motor power. No physical E-stop is configured here.
*/
#include <Arduino.h>
#include <AccelStepper.h>
#include <stdio.h>

constexpr uint8_t STEP_PIN = 5, DIR_PIN = 6;
constexpr bool INVERT_DIRECTION = true;
constexpr uint16_t FIRST_TARGET_PPS = 1000;
constexpr uint16_t TARGET_INCREMENT_PPS = 250;
// Initial exploration ceiling, NOT a verified hardware maximum.
constexpr uint16_t TEST_CEILING_PPS = 3000;
constexpr float ACCELERATION_PPS2 = 500.0f;
constexpr unsigned long RUN_MS = 10000UL;  // Includes acceleration.
static_assert(FIRST_TARGET_PPS > 0 && TARGET_INCREMENT_PPS > 0, "Invalid target");
static_assert(TEST_CEILING_PPS >= FIRST_TARGET_PPS, "Invalid ceiling");
static_assert(ACCELERATION_PPS2 > 0, "Invalid acceleration");

AccelStepper belt(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);
uint16_t nextTarget = FIRST_TARGET_PPS, activeTarget = FIRST_TARGET_PPS;
bool running = false;
unsigned long startedAt = 0, lastByteAt = 0;
char commandByte = 0;
uint8_t lineLength = 0;

void emit(const __FlashStringHelper* message) {
  const size_t n = strlen_P(reinterpret_cast<PGM_P>(message)) + 2;
  if (Serial.availableForWrite() >= static_cast<int>(n)) Serial.println(message);
}

void report(const char* label, uint16_t value) {
  char message[48];
  snprintf(message, sizeof(message), "%s:%u", label, static_cast<unsigned>(value));
  if (Serial.availableForWrite() >= static_cast<int>(strlen(message) + 2))
    Serial.println(message);
}

void clearPending() {
  lineLength = 0;
  for (uint8_t i = 0; i < 64 && Serial.available(); ++i) Serial.read();
}

void stopBelt() {
  running = false;
  belt.setCurrentPosition(belt.currentPosition());
  digitalWrite(STEP_PIN, LOW);
  clearPending();
}

void handleCommand(char c) {
  if (c == 's') {
    stopBelt();
    // An aborted run never advances its next target.
    emit(F("STOPPED:READY_A"));
    return;
  }
  if (c != 'a') { emit(F("ERR:USE_A_OR_S")); return; }
  if (running) { emit(F("BUSY:NO_QUEUED_RESTART")); return; }
  activeTarget = nextTarget;
  belt.setCurrentPosition(0);
  belt.setMaxSpeed(activeTarget);
  belt.moveTo(1000000L);
  startedAt = millis();
  running = true;
  clearPending();
  report("TEST_TARGET_PPS", activeTarget);
}

void setup() {
  // Keep L298N actuator outputs inactive throughout this conveyor-only sketch.
  digitalWrite(9, LOW); pinMode(9, OUTPUT);
  digitalWrite(7, LOW); pinMode(7, OUTPUT);
  digitalWrite(8, LOW); pinMode(8, OUTPUT);
  belt.setPinsInverted(INVERT_DIRECTION, false, false);
  belt.setMinPulseWidth(10);
  belt.setAcceleration(ACCELERATION_PPS2);
  belt.setMaxSpeed(FIRST_TARGET_PPS);
  stopBelt();
  Serial.begin(115200);
  emit(F("READY A=10S_TEST S=STOP START=1000PPS"));
}

void loop() {
  if (lineLength && millis() - lastByteAt >= 500UL) lineLength = 0;
  for (uint8_t i = 0; i < 8 && Serial.available(); ++i) {
    const char c = static_cast<char>(Serial.read());
    lastByteAt = millis();
    if (c == '\n' || c == '\r') {
      const bool valid = lineLength == 1, nonempty = lineLength != 0;
      lineLength = 0;
      if (valid) handleCommand(commandByte);
      else if (nonempty) emit(F("ERR:ONE_COMMAND_PER_LINE"));
    } else {
      if (!lineLength) commandByte = c;
      if (lineLength < 2) ++lineLength;
    }
  }
  if (!running) return;
  if (millis() - startedAt >= RUN_MS) {
    stopBelt();
    report("DONE_TIME_NOT_VERIFIED_PPS", activeTarget);
    const uint32_t candidate = static_cast<uint32_t>(activeTarget) + TARGET_INCREMENT_PPS;
    nextTarget = candidate > TEST_CEILING_PPS ? TEST_CEILING_PPS : candidate;
    if (activeTarget == TEST_CEILING_PPS) emit(F("TEST_CEILING_REACHED:NOT_HARDWARE_MAX"));
    else report("NEXT_A_TARGET_PPS", nextTarget);
    return;
  }
  belt.run();
}
