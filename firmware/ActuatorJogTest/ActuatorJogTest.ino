/*
  UNO + L298N channel A: unloaded actuator commissioning only.
  ENA D9 (jumper removed), IN1 D7, IN2 D8, common GND.
  Conveyor driver power MUST be disconnected during this test.
  No physical E-stop or limit input is configured in this sketch.
  This is not the integrated controller or a safety-rated stop.
  Serial: 115200, one lowercase command per line, New Line/CRLF.
  r: arm one jog, j/k: opposite directions for 500ms, s: stop, e: latch.
  d/u full-stroke commands are intentionally rejected.
*/
#include <Arduino.h>

constexpr uint8_t ENA = 9, IN1 = 7, IN2 = 8;
constexpr unsigned long JOG_MS = 500UL;
constexpr unsigned long ARM_MS = 10000UL;
constexpr uint8_t SPEED = 255;
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

void stopMotor() {
  analogWrite(ENA, 0);  // Disable bridge before changing direction inputs.
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  running = false;
  armed = false;
  locked = true;
  clearPending();
}

void handleCommand(char c) {
  if (c == 's' || c == 'e') {
    stopMotor();
    emit(c == 's' ? F("STOP:LOCKED") : F("SOFTWARE_STOP:LOCKED"));
    return;
  }
  if (c == '?') {
    emit(running ? F("RUNNING") : (armed ? F("ARMED") : F("LOCKED:R_REQUIRED")));
    return;
  }
  if (c == 'd' || c == 'u') {
    emit(F("ERR:FULL_STROKE_DISABLED_USE_J_K"));
    return;
  }
  if (c != 'r' && c != 'j' && c != 'k') {
    emit(F("ERR:UNKNOWN_COMMAND"));
    return;
  }
  if (running) {
    emit(F("ERR:BUSY"));
    return;
  }
  if (c == 'r') {
    stopMotor();
    locked = false;
    armed = true;
    armedAt = millis();
    emit(F("ARMED:ONE_JOG_WITHIN_10S"));
    return;
  }
  if (locked || !armed || millis() - armedAt >= ARM_MS) {
    stopMotor();
    emit(F("ERR:SEND_R_FIRST"));
    return;
  }
  armed = false;
  digitalWrite(IN1, c == 'j' ? HIGH : LOW);
  digitalWrite(IN2, c == 'j' ? LOW : HIGH);
  startedAt = millis();
  running = true;
  analogWrite(ENA, SPEED);
  emit(c == 'j' ? F("JOG:J_500MS") : F("JOG:K_500MS"));
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
  emit(F("BOOT:LOCKED R=ARM J/K=JOG S=STOP"));
}

void loop() {
  if (running && millis() - startedAt >= JOG_MS) {
    stopMotor();
    emit(F("JOG_DONE:LOCKED_R_REQUIRED"));
  }
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
}
