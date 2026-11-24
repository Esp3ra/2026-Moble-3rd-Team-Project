#include <Arduino.h>
#include <cassert>
#include <cstdio>
#include <algorithm>
unsigned long fakeMillis=0;
int inPins[32],outPins[32];
SerialStub Serial;
#include "../../pallet_system_uno_sim/firmware/RobotWorkSimulation/RobotWorkSimulation.ino"
void send(const char* text){char buffer[64];std::strcpy(buffer,text);command(buffer);}
void advance(unsigned long ms){fakeMillis+=ms;loop();}
void sensor(uint8_t pin,int level){inPins[pin]=level;loop();advance(20);}
int main(){
 std::fill_n(inPins,32,HIGH);setup();advance(600);
 send("a 7");assert(phase==Phase::PUSH);assert(outPins[ACT_ENA]==255);
 send("q 7");assert(phase==Phase::PUSH);
 advance(10000);assert(phase==Phase::PAUSE);advance(500);assert(beltRunning&&returning);
 sensor(IR1_PIN,LOW);advance(runIr1Delay);assert(stage==Stage::WAIT_QR&&!beltRunning);
 advance(20000);assert(stage==Stage::WAIT_QR&&!beltRunning&&!returning);
 send("q 8");assert(stage==Stage::WAIT_QR&&!beltRunning);
 send("q 7");assert(stage==Stage::ROBOT_WAIT&&!beltRunning);
 advance(2999);assert(!beltRunning);advance(1);assert(stage==Stage::SECOND&&beltRunning);
 sensor(IR2_PIN,LOW);advance(runIr2Delay);assert(phase==Phase::IDLE&&!beltRunning&&outPins[ACT_ENA]==0);
 assert(Serial.output.find("EV 7 QR:WAITING")!=std::string::npos);
 assert(Serial.output.find("EV 7 QR:RECOGNIZED_ROBOT_WAIT")!=std::string::npos);
 assert(Serial.output.find("EV 7 CYCLE_DONE:READY_A")!=std::string::npos);
 sensor(IR1_PIN,HIGH);sensor(IR2_PIN,HIGH);advance(600);send("a 8");assert(phase==Phase::PUSH);
 send("s");assert(phase==Phase::IDLE&&!beltRunning&&outPins[ACT_ENA]==0&&cycleId==0);
 advance(600);send("a 9");advance(10000);advance(500);advance(30000);
 assert(phase==Phase::IDLE&&!beltRunning&&outPins[ACT_ENA]==0);
 assert(Serial.output.find("EV 9 ERR:IR_TRAVEL_TIMEOUT_STOP_BOTH")!=std::string::npos);
 std::puts("PASS: actual UNO source waits for matching QR, holds belt, waits 3 seconds, finishes on IR2; stop and sensor timeout work.");
}
