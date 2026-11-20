#include "Arduino.h"
#include <cassert>
#include <iostream>

#include "../RobotWorkSimulation/RobotWorkSimulation.ino"

SerialStub Serial;
unsigned long clockMs = 0;
unsigned long beltPulses = 0;
uint8_t pins[20]{};

unsigned long millis() { return clockMs; }
unsigned long micros() { return clockMs * 1000UL; }
void pinMode(uint8_t, uint8_t) {}
int digitalRead(uint8_t pin) { return pins[pin]; }
void digitalWrite(uint8_t pin, uint8_t value) {
  if (pin == STEP_PIN && value == LOW) ++beltPulses;
  pins[pin] = value;
}
void analogWrite(uint8_t pin, uint8_t value) { pins[pin] = value; }

void advance(unsigned long elapsed) {
  clockMs += elapsed;
  loop();
}

void send(const std::string& command) {
  Serial.input += command + '\n';
  while (Serial.available()) loop();
}

bool reported(const char* event) {
  return Serial.output.find(std::string(event) + '\n') != std::string::npos;
}

void startCycle(const std::string& command) {
  advance(500);
  send(command);
  assert(phase == Phase::PUSH);
  assert(!beltRunning);
  if (command == "a 41") {
    send("START_TRANSFER 41");
    assert(reported("ERR:TRANSFER_CYCLE"));
    assert(!reported("PALLET_EJECTED:41"));
  }
  advance(PUSH_MS);
  advance(REVERSAL_MS);
}

void reachAssembly() {
  pins[IR1_PIN] = LOW;
  advance(1);
  advance(FILTER_MS);
  advance(ir1Delay);
  assert(stage == Stage::ROBOT_WAIT);
  assert(!beltRunning && pins[STEP_PIN] == HIGH);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string scenario = argv[1];
  pins[IR1_PIN] = pins[IR2_PIN] = HIGH;
  setup();
  advance(500);
  send("T 1 0 3000 0");
  send("a");
  assert(phase == Phase::IDLE && pins[ACT_ENA] == 0 && beltPulses == 0);

  if (scenario == "demo") {
    startCycle("demo");
    assert(beltRunning && !reported("PALLET_EJECTED:41"));
    reachAssembly();
    const auto stoppedPulses = beltPulses;
    send("HEARTBEAT 41");
    send("ASSEMBLY_DONE 41");
    advance(robotWait - 1);
    assert(!beltRunning && beltPulses == stoppedPulses);
    advance(1);
    assert(beltRunning && reported("SIM:BELT_RESTARTED"));
    return 0;
  }

  // A start approval cannot be queued before the pallet is ejected.
  send("START_TRANSFER 41");
  assert(reported("ERR:TRANSFER_CYCLE"));
  Serial.output.clear();
  startCycle("a 41");
  assert(stage == Stage::QR_WAIT && reported("PALLET_EJECTED:41"));
  assert(!beltRunning && beltPulses == 0);
  send("q");
  assert(reported("QR:INFORMATION_ONLY"));
  send("START_TRANSFER 40");
  send("START_TRANSFER 42");
  send("START_TRANSFER 41 extra");
  send("a 42");
  send("demo");
  advance(BELT_LIMIT_MS + 1);
  assert(stage == Stage::QR_WAIT && !beltRunning && beltPulses == 0);
  assert(pins[ACT_ENA] == 0 && !reported("TRANSFER_STARTED:41"));
  if (scenario == "qr-stop" || scenario == "qr-blocked") {
    if (scenario == "qr-stop") {
      send("s");
      assert(reported("STOPPED:TRANSFER_LOCKOUT"));
    } else {
      pins[IR1_PIN] = LOW;
      send("START_TRANSFER 41");
      assert(reported("ERR:IR_SENSORS_NOT_CLEAR_AT_START"));
    }
    pins[IR1_PIN] = HIGH;
    advance(FILTER_MS);
    send("START_TRANSFER 41");
    send("a 42");
    assert(phase == Phase::LOCKOUT && !beltRunning && beltPulses == 0);
    return 0;
  }
  send("START_TRANSFER 41");
  assert(stage == Stage::FIRST && beltRunning && reported("TRANSFER_STARTED:41"));
  assert(!reported("HANDOFF_READY:41"));
  send("START_TRANSFER 41");
  assert(beltRunning && stage == Stage::FIRST);
  send("HEARTBEAT 41");
  send("ASSEMBLY_DONE 41");
  assert(reported("ERR:ASSEMBLY_CYCLE"));
  reachAssembly();
  assert(reported("ASSEMBLY_WAIT:41"));
  const auto stoppedPulses = beltPulses;

  if (scenario == "no-heartbeat") {
    send("ASSEMBLY_DONE 41");
    assert(reported("ERR:ASSEMBLY_HEARTBEAT_REQUIRED"));
    advance(ASSEMBLY_LINK_TIMEOUT_MS);
  } else if (scenario == "link-loss") {
    send("HEARTBEAT 41");
    advance(ASSEMBLY_LINK_TIMEOUT_MS - 1);
    Serial.input = "HEARTBEAT 41";
    while (Serial.available()) loop();
    ++clockMs;
    // Finish reading the heartbeat at the deadline before the cycle check.
    send("");
  } else {
    send("HEARTBEAT 41");
    send("ASSEMBLY_DONE 40");
    send("ASSEMBLY_DONE 42");
    send("ASSEMBLY_DONE 41 extra");
    send("demo");
    for (unsigned int i = 0; i < 5; ++i) {
      advance(1000);
      send("HEARTBEAT 41");
      assert(!beltRunning && beltPulses == stoppedPulses);
    }
    assert(!reported("SIM:BELT_RESTARTED"));

    if (scenario == "completion") {
      send("ASSEMBLY_DONE 41");
      assert(beltRunning && stage == Stage::SECOND && reported("ASSEMBLY_RESTARTED:41"));
      advance(1);
      assert(beltPulses > stoppedPulses);
      send("ASSEMBLY_DONE 41");
      pins[IR2_PIN] = LOW;
      advance(1);
      advance(FILTER_MS);
      assert(!beltRunning && reported("BELT_STOPPED:IR2_DELAY_DONE") && reported("HANDOFF_READY:41"));
      advance(RETURN_MS);
      assert(reported("CYCLE_DONE:READY_A"));
      pins[IR1_PIN] = pins[IR2_PIN] = HIGH;
      advance(500);
      advance(FILTER_MS);
      send("a 41");
      assert(phase == Phase::IDLE && reported("ERR:ASSEMBLY_CYCLE_REUSED"));
      return 0;
    }
    if (scenario == "ir2-blocked") {
      pins[IR2_PIN] = LOW;
      send("ASSEMBLY_DONE 41");
      assert(reported("ERR:IR2_NOT_CLEAR_AT_RESTART"));
    } else {
      assert(scenario == "operator-stop");
      send("s");
      assert(reported("STOPPED:ASSEMBLY_LOCKOUT"));
    }
  }

  if (scenario == "no-heartbeat" || scenario == "link-loss") {
    assert(reported("ERR:ASSEMBLY_LINK_TIMEOUT"));
  }
  assert(phase == Phase::LOCKOUT && !beltRunning && pins[ACT_ENA] == 0);
  advance(5000);
  send("HEARTBEAT 41");
  send("ASSEMBLY_DONE 41");
  send("a 42");
  send("demo");
  send("j");
  send("s");
  assert(phase == Phase::LOCKOUT && !beltRunning && beltPulses == stoppedPulses);
  assert(!reported("ASSEMBLY_RESTARTED:41"));
}
