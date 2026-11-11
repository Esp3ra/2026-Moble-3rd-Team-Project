#include <assert.h>
#include <stdio.h>
// Test the actual sketch against fake time, serial and GPIO; no hardware.
#include "../ActuatorJogTest/ActuatorJogTest.ino"

void send(const char* s) { Serial.feed(s); loop(); }
void advance(unsigned long ms) { fakeNow += ms; loop(); }
void idle() {
  assert(cyclePhase == CyclePhase::IDLE);
  assert(fakePwm[ENA] == 0);
  assert(fakeDigital[IN1] == LOW && fakeDigital[IN2] == LOW);
}

int main() {
  setup(); idle();
  // Removed commands cannot move the motor.
  for (const char* s : {"r\n", "j\n", "k\n", "q\n", "w\n", "d\n", "u\n"}) {
    send(s); idle();
  }
  send("a\n");
  assert(cyclePhase == CyclePhase::FIRST && fakePwm[ENA] == 255);
  assert(fakeDigital[IN1] == HIGH && fakeDigital[IN2] == LOW);
  advance(100);
  const unsigned long firstStart = phaseStartedAt;
  send("a\n");
  assert(phaseStartedAt == firstStart);  // No restart or timer extension.
  advance(11399);
  assert(cyclePhase == CyclePhase::FIRST);
  advance(1);
  assert(cyclePhase == CyclePhase::PAUSE && fakePwm[ENA] == 0);
  advance(499);
  assert(cyclePhase == CyclePhase::PAUSE);
  advance(1);
  assert(cyclePhase == CyclePhase::RETURNING && fakePwm[ENA] == 255);
  assert(fakeDigital[IN1] == LOW && fakeDigital[IN2] == HIGH);
  advance(11500); idle();
  advance(30000); idle();  // Never repeat without a fresh a.

  // Stop first motion, reversal pause, and return motion independently.
  for (int phase = 0; phase < 3; ++phase) {
    send("a\n");
    if (phase >= 1) advance(11500);
    if (phase == 2) advance(500);
    send("s\n"); idle();
    advance(30000); idle();
  }
  send("aa\n"); idle();
  send("a"); idle(); advance(500); send("\n"); idle();
  send("a\r\n");
  assert(cyclePhase == CyclePhase::FIRST);
  send("s\na\n"); idle();  // Queued start after stop is discarded.
  puts("Actual actuator sketch passed with fake GPIO/serial/time.");
}
