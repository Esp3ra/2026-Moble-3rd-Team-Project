/*
  UNO commissioning controller v0.2
  Commands: F/B conveyor, X/Y actuator, S stop all, E latch stop, ? status.
  Send one command per line (New Line or Both NL & CR), 9600 baud.
  No commands are sent to hardware automatically at boot or after reset.
  Software monitoring is not a safety-rated emergency-stop circuit.
*/
#include <Arduino.h>
#include <AccelStepper.h>
#include "Config.h"

static_assert(CONVEYOR_MAX_SPEED > 0, "Conveyor speed must be positive");
static_assert(CONVEYOR_ACCELERATION > 0, "Acceleration must be positive");
static_assert(ACTUATOR_PWM > 0, "Actuator PWM must be positive");

AccelStepper conveyor(AccelStepper::DRIVER,
                     CONVEYOR_STEP_PIN, CONVEYOR_DIR_PIN);

enum class ActuatorState : uint8_t { IDLE, EXTENDING, RETRACTING };
bool conveyorRunning = false;
ActuatorState actuatorState = ActuatorState::IDLE;
unsigned long conveyorStartedAt = 0;
unsigned long actuatorStartedAt = 0;

// Start locked. A physical reset press followed by release is required.
bool stopLatched = true;
bool actuatorFaultLatched = false;
volatile bool estopEdgeSeen = false;
bool healthyTiming = false;
unsigned long healthySince = 0;
bool resetRaw = HIGH;
bool resetStable = HIGH;
unsigned long resetChangedAt = 0;
bool resetArmed = false;

char rxBuffer[8];
uint8_t rxLength = 0;
bool rxDiscard = false;
unsigned long rxLastAt = 0;

void estopISR() {
  // Never print or call AccelStepper from the interrupt handler.
  if (digitalRead(ESTOP_PIN) == HIGH) estopEdgeSeen = true;
}

// Avoid waiting for UART space inside the motor/safety polling loop.
// A busy terminal can cause diagnostics to be dropped, never delayed motion.
void emit(const __FlashStringHelper* text) {
  const size_t needed = strlen_P(reinterpret_cast<PGM_P>(text)) + 2;
  if (Serial.availableForWrite() >= static_cast<int>(needed)) Serial.println(text);
}

void clearRx() {
  rxLength = 0;
  rxDiscard = false;
}

void discardPendingCommands() {
  clearRx();
  // UNO's standard receive buffer is bounded; do not wait for new bytes.
  for (uint8_t i = 0; i < 64 && Serial.available() > 0; ++i) {
    Serial.read();
  }
}

void stopConveyor() {
  conveyorRunning = false;
  // Cancels the target and resets the library's speed to zero immediately.
  conveyor.setCurrentPosition(conveyor.currentPosition());
  digitalWrite(CONVEYOR_STEP_PIN, LOW);
}

void stopActuator() {
  analogWrite(ACTUATOR_ENA_PIN, 0);
  digitalWrite(ACTUATOR_IN1_PIN, LOW);
  digitalWrite(ACTUATOR_IN2_PIN, LOW);
  actuatorState = ActuatorState::IDLE;
}

void stopAll() {
  stopConveyor();
  stopActuator();
}

void latchStop(const __FlashStringHelper* reason) {
  const bool firstTrip = !stopLatched;
  stopLatched = true;
  resetArmed = false;
  stopAll();
  if (firstTrip) {
    discardPendingCommands();
    emit(reason);
  }
}

bool physicalEstopOpen() {
  return digitalRead(ESTOP_PIN) == HIGH;
}

bool extendLimitOpen() {
  return digitalRead(ACTUATOR_EXTEND_LIMIT_PIN) == HIGH;
}

bool retractLimitOpen() {
  return digitalRead(ACTUATOR_RETRACT_LIMIT_PIN) == HIGH;
}

bool conflictingLimits() {
  return ACTUATOR_HARDWARE_VERIFIED &&
         extendLimitOpen() && retractLimitOpen();
}

void pollSafety(unsigned long now) {
  noInterrupts();
  const bool edge = estopEdgeSeen;
  estopEdgeSeen = false;
  interrupts();

  if (physicalEstopOpen() || edge) {
    healthyTiming = false;
    latchStop(F("LOCK:ESTOP_OPEN_OR_EDGE"));
  } else if (!healthyTiming) {
    healthyTiming = true;
    healthySince = now;
  }

  if (conflictingLimits()) {
    const bool firstFault = !actuatorFaultLatched;
    actuatorFaultLatched = true;
    latchStop(F("LOCK:LIMIT_INPUTS_CONFLICT"));
    if (firstFault) emit(F("FAULT:LIMIT_INPUTS_CONFLICT"));
  }
}

void pollReset(unsigned long now) {
  const bool value = digitalRead(RESET_PIN);
  if (value != resetRaw) {
    resetRaw = value;
    resetChangedAt = now;
  }
  if (now - resetChangedAt < RESET_DEBOUNCE_MS ||
      resetStable == resetRaw) return;

  resetStable = resetRaw;
  const bool healthy = healthyTiming && !physicalEstopOpen() &&
                       now - healthySince >= ESTOP_HEALTHY_MS &&
                       !conflictingLimits();
  if (resetStable == LOW) {
    // A button held during an E-stop must not clear the stop on release.
    resetArmed = stopLatched && healthy;
    return;
  }

  if (resetArmed && stopLatched && healthy) {
    stopAll();
    discardPendingCommands();
    actuatorFaultLatched = false;
    stopLatched = false;
    emit(F("READY:NEW_START_REQUIRED"));
  }
  resetArmed = false;
}

void showStatus() {
  emit(stopLatched ? F("STATE:LOCKED") : F("STATE:READY"));
  emit(conveyorRunning ? F("CONVEYOR:RUNNING") : F("CONVEYOR:IDLE"));
  if (!ACTUATOR_HARDWARE_VERIFIED) emit(F("ACTUATOR:DISABLED_UNVERIFIED"));
  else if (actuatorState == ActuatorState::EXTENDING) emit(F("ACTUATOR:EXTENDING"));
  else if (actuatorState == ActuatorState::RETRACTING) emit(F("ACTUATOR:RETRACTING"));
  else emit(F("ACTUATOR:IDLE"));
  // If some status lines are dropped because UART is busy, query again.
}

void startConveyor(bool forward, unsigned long now) {
  if (conveyorRunning || actuatorState != ActuatorState::IDLE) {
    emit(F("ERR:BUSY_STOP_FIRST"));
    return;
  }
  conveyor.setCurrentPosition(0);
  conveyor.moveTo(forward ? 1000000L : -1000000L);
  conveyorStartedAt = now;
  conveyorRunning = true;
  emit(forward ? F("CONVEYOR:FORWARD") : F("CONVEYOR:REVERSE"));
}

void startActuator(bool extend, unsigned long now) {
  if (!ACTUATOR_HARDWARE_VERIFIED) {
    emit(F("ERR:ACTUATOR_UNVERIFIED"));
    return;
  }
  if (conveyorRunning || actuatorState != ActuatorState::IDLE) {
    emit(F("ERR:BUSY_STOP_FIRST"));
    return;
  }
  if (actuatorFaultLatched || conflictingLimits()) {
    emit(F("ERR:ACTUATOR_FAULT"));
    return;
  }
  if (extend ? extendLimitOpen() : retractLimitOpen()) {
    emit(F("ERR:TARGET_LIMIT_OR_WIRE_OPEN"));
    return;
  }

  stopActuator();
  actuatorStartedAt = now;
  actuatorState = extend ? ActuatorState::EXTENDING : ActuatorState::RETRACTING;
  digitalWrite(ACTUATOR_IN1_PIN, extend ? HIGH : LOW);
  digitalWrite(ACTUATOR_IN2_PIN, extend ? LOW : HIGH);
  analogWrite(ACTUATOR_ENA_PIN, ACTUATOR_PWM);
  emit(extend ? F("ACTUATOR:EXTENDING") : F("ACTUATOR:RETRACTING"));
}

void handleCommand(char command, unsigned long now) {
  if (command == '?') { showStatus(); return; }
  if (command == 'S') {
    stopAll();
    emit(F("STOP:ALL_OUTPUT_COMMANDS_STOPPED"));
    return;
  }
  if (command == 'E') {
    latchStop(F("LOCK:SOFTWARE_ESTOP"));
    discardPendingCommands();
    return;
  }
  if (command != 'F' && command != 'B' && command != 'X' && command != 'Y') {
    emit(F("ERR:UNKNOWN_COMMAND"));
    return;
  }
  // Re-read the physical input immediately before admitting motion.
  if (physicalEstopOpen()) {
    latchStop(F("LOCK:ESTOP_OPEN"));
    return;
  }
  if (stopLatched) { emit(F("ERR:LOCKED_USE_PHYSICAL_RESET")); return; }
  if (command == 'F' || command == 'B') startConveyor(command == 'F', now);
  else startActuator(command == 'X', now);
}

void pollSerial(unsigned long now) {
  if ((rxLength || rxDiscard) && now - rxLastAt >= RX_PARTIAL_TIMEOUT_MS) {
    clearRx();
    emit(F("ERR:INCOMPLETE_COMMAND"));
  }
  // Bound work so a serial flood cannot monopolise motor/safety polling.
  for (uint8_t i = 0; i < 8 && Serial.available() > 0; ++i) {
    const char c = static_cast<char>(Serial.read());
    rxLastAt = now;
    if (c == '\r' || c == '\n') {
      if (rxDiscard) emit(F("ERR:COMMAND_TOO_LONG"));
      else if (rxLength == 1) handleCommand(rxBuffer[0], now);
      else if (rxLength > 1) emit(F("ERR:ONE_COMMAND_PER_LINE"));
      clearRx();
      // E-stop discards the pending receive queue in latchStop().
      if (stopLatched) return;
    } else if (!rxDiscard) {
      if (rxLength < sizeof(rxBuffer)) rxBuffer[rxLength++] = c;
      else rxDiscard = true;
    }
  }
}

void serviceConveyor(unsigned long now) {
  if (!conveyorRunning) return;
  if (now - conveyorStartedAt >= CONVEYOR_TIMEOUT_MS) {
    stopConveyor();
    emit(F("CONVEYOR:AUTO_STOP"));
    return;
  }
  conveyor.run();
}

void serviceActuator(unsigned long now) {
  if (actuatorState == ActuatorState::IDLE) return;
  if ((actuatorState == ActuatorState::EXTENDING && extendLimitOpen()) ||
      (actuatorState == ActuatorState::RETRACTING && retractLimitOpen())) {
    stopActuator();
    // An open NC contact is NOT sufficient proof of physical position.
    emit(F("ACTUATOR:LIMIT_OR_WIRE_OPEN_STOP"));
    return;
  }
  if (now - actuatorStartedAt >= ACTUATOR_TIMEOUT_MS) {
    actuatorFaultLatched = true;
    latchStop(F("LOCK:ACTUATOR_TIMEOUT"));
  }
}

void setup() {
  // Establish inactive actuator outputs before making them outputs.
  digitalWrite(ACTUATOR_ENA_PIN, LOW);
  digitalWrite(ACTUATOR_IN1_PIN, LOW);
  digitalWrite(ACTUATOR_IN2_PIN, LOW);
  pinMode(ACTUATOR_ENA_PIN, OUTPUT);
  pinMode(ACTUATOR_IN1_PIN, OUTPUT);
  pinMode(ACTUATOR_IN2_PIN, OUTPUT);
  pinMode(ESTOP_PIN, INPUT_PULLUP);
  pinMode(RESET_PIN, INPUT_PULLUP);
  pinMode(ACTUATOR_EXTEND_LIMIT_PIN, INPUT_PULLUP);
  pinMode(ACTUATOR_RETRACT_LIMIT_PIN, INPUT_PULLUP);
  resetRaw = resetStable = digitalRead(RESET_PIN);
  resetChangedAt = millis();

  conveyor.setMinPulseWidth(10);
  conveyor.setMaxSpeed(CONVEYOR_MAX_SPEED);
  conveyor.setAcceleration(CONVEYOR_ACCELERATION);
  conveyor.setPinsInverted(CONVEYOR_INVERT_DIRECTION, false, false);
  stopAll();
  attachInterrupt(digitalPinToInterrupt(ESTOP_PIN), estopISR, CHANGE);
  Serial.begin(SERIAL_BAUD);
  emit(F("BOOT:LOCKED_RESET_REQUIRED"));
}

void loop() {
  const unsigned long now = millis();
  pollSafety(now);
  pollReset(now);
  pollSerial(now);
  // A new edge while parsing/printing commands must still prevent motion.
  pollSafety(millis());
  if (stopLatched) return;
  serviceActuator(millis());
  if (!stopLatched && !physicalEstopOpen() && !estopEdgeSeen) {
    serviceConveyor(millis());
  }
}
