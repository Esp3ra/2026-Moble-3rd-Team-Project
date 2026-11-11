/*
  Draft integration boundary: upper controller sends ORDER id / PALLET id.
  Camera inference is performed on the host, never on UNO.
  Existing test sketches are independent and remain unchanged.
  Not a complete production/safety controller: no physical E-stop/limits yet.
*/
#include <Arduino.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "Config.h"
#include "FeederLogic.h"

feeder::Controller controller({PUSH_MAX_MS, RETURN_MS,
                              REVERSAL_PAUSE_MS, CAMERA_TIMEOUT_MS});
feeder::Drive applied = feeder::Drive::OFF;
char input[48];
uint8_t length = 0;
bool discardLine = false;
uint32_t lastByteAt = 0;
bool discardAfterCommand = false;

const char* stateName(feeder::State s) {
  switch (s) {
    case feeder::State::LOCKED: return "LOCKED";
    case feeder::State::IDLE: return "IDLE";
    case feeder::State::PUSHING: return "PUSHING";
    case feeder::State::WAIT_CAMERA: return "WAIT_CAMERA";
    case feeder::State::REVERSAL_PAUSE: return "REVERSAL_PAUSE";
    case feeder::State::RETURNING: return "RETURNING";
    case feeder::State::RETURN_TIME_ELAPSED: return "RETURN_TIME_ELAPSED";
    default: return "FAULT_CAMERA_TIMEOUT";
  }
}

// Nonblocking, best-effort telemetry. Host must poll STATUS if it misses a line.
void emit(const char* message) {
  if (Serial.availableForWrite() >= static_cast<int>(strlen(message) + 2))
    Serial.println(message);
}

void status() {
  char message[64];
  snprintf(message, sizeof(message), "STATE %s JOB %lu %s",
           stateName(controller.state), static_cast<unsigned long>(controller.job),
           DRY_RUN ? "DRY" : "LIVE");
  emit(message);
}

void outputsOff() {
  analogWrite(ENA_PIN, 0);
  digitalWrite(IN1_PIN, LOW);
  digitalWrite(IN2_PIN, LOW);
}

void applyOutputs() {
  const feeder::Drive wanted = DRY_RUN ? feeder::Drive::OFF : controller.drive();
  if (wanted == applied) return;
  outputsOff();
  if (wanted != feeder::Drive::OFF) {
    const bool directionJ = (wanted == feeder::Drive::PUSH) == PUSH_IS_J;
    digitalWrite(IN1_PIN, directionJ ? HIGH : LOW);
    digitalWrite(IN2_PIN, directionJ ? LOW : HIGH);
    analogWrite(ENA_PIN, MOTOR_PWM);
  }
  applied = wanted;
}

void clearPending() {
  length = 0;
  discardLine = false;
  for (uint8_t i = 0; i < 64 && Serial.available(); ++i) Serial.read();
}

bool parseId(const char* text, uint32_t& value) {
  // Canonical positive decimal sequence 1..4294967295, no signs/spaces.
  if (!*text || *text == '0') return false;
  uint32_t result = 0;
  for (; *text; ++text) {
    if (*text < '0' || *text > '9') return false;
    const uint8_t digit = *text - '0';
    if (result > (UINT32_MAX - digit) / 10UL) return false;
    result = result * 10UL + digit;
  }
  value = result;
  return true;
}

void reply(feeder::Result result) {
  switch (result) {
    case feeder::Result::OK: emit("OK"); break;
    case feeder::Result::DUPLICATE: emit("OK DUPLICATE_NO_RESTART"); break;
    case feeder::Result::BUSY: emit("ERR NOT_READY"); break;
    case feeder::Result::BAD_ID: emit("ERR JOB_ID"); break;
    default: emit("ERR STATE"); break;
  }
}

void command(const char* text, uint32_t now) {
  if (!strcmp(text, "STOP") || !strcmp(text, "ESTOP")) {
    controller.stop();
    applyOutputs();
    clearPending();
    discardAfterCommand = true;
    emit("STOPPED LOCKED");
    return;
  }
  if (!strcmp(text, "STATUS")) { status(); return; }
  if (!strcmp(text, "HOME")) {
    // Manual/independent physical home confirmation, NOT automatic homing.
    reply(controller.confirmHome());
    clearPending();
    discardAfterCommand = true;
    return;
  }
  uint32_t id = 0;
  if (!strncmp(text, "ORDER ", 6) && parseId(text + 6, id)) {
    reply(controller.order(id, now));
  } else if (!strncmp(text, "PALLET ", 7) && parseId(text + 7, id)) {
    reply(controller.pallet(id, now));
  } else {
    emit("ERR COMMAND");
  }
  applyOutputs();
}

void setup() {
  digitalWrite(ENA_PIN, LOW);
  digitalWrite(IN1_PIN, LOW);
  digitalWrite(IN2_PIN, LOW);
  pinMode(ENA_PIN, OUTPUT);
  pinMode(IN1_PIN, OUTPUT);
  pinMode(IN2_PIN, OUTPUT);
  outputsOff();
  // This sketch does not run the conveyor; disconnect its motor power for tests.
  digitalWrite(5, LOW); pinMode(5, OUTPUT);
  digitalWrite(6, LOW); pinMode(6, OUTPUT);
  Serial.begin(SERIAL_BAUD);
  emit(DRY_RUN ? "BOOT DRY_RUN LOCKED" : "BOOT LIVE LOCKED");
}

void loop() {
  const feeder::State before = controller.state;
  controller.tick(millis());
  applyOutputs();
  if (controller.state != before) status();
  if ((length || discardLine) && millis() - lastByteAt >= 500UL) {
    length = 0;
    discardLine = false;
    emit("ERR PARTIAL_TIMEOUT");
  }
  for (uint8_t count = 0; count < 16 && Serial.available(); ++count) {
    const char c = static_cast<char>(Serial.read());
    lastByteAt = millis();
    if (c == '\n' || c == '\r') {
      if (discardLine) emit("ERR LINE_TOO_LONG");
      else if (length) {
        input[length] = '\0';
        // Expire deadlines before admitting a newly completed command.
        controller.tick(millis());
        applyOutputs();
        command(input, millis());
      }
      length = 0;
      discardLine = false;
      if (discardAfterCommand) { discardAfterCommand = false; break; }
    } else if (!discardLine) {
      // Reject embedded NUL/control bytes instead of accepting a valid prefix.
      if (c < 32 || c > 126 || length >= sizeof(input) - 1) discardLine = true;
      else input[length++] = c;
    }
  }
}
