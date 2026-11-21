/* UNO-owned two-IR test with a timed, simulated robot work pause.
   a: cycle, q: QR information, s: stop, j/k: 1s actuator jog.
   T revision ir1DelayMs robotWaitMs ir2DelayMs: configure only while idle.
*/
#include <Arduino.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
constexpr uint8_t STEP_PIN=5,DIR_PIN=6,ACT_ENA=9,ACT_IN1=7,ACT_IN2=8;
constexpr uint8_t IR1_PIN=4,IR2_PIN=2;
constexpr unsigned long HALF_PERIOD_US=80UL,FILTER_MS=20UL;
constexpr unsigned long PUSH_MS=10000UL,RETURN_MS=10000UL,REVERSAL_MS=500UL,JOG_MS=1000UL;
constexpr unsigned long BELT_LIMIT_MS=30000UL;
unsigned long ir1Delay=5500UL,robotWait=3000UL,ir2Delay=2000UL;
unsigned long runIr1Delay=0,runRobotWait=0,runIr2Delay=0;
enum class Phase:uint8_t{IDLE,PUSH,PAUSE,RUN,JOG};
enum class Stage:uint8_t{FIRST,ROBOT_WAIT,SECOND,DONE};
Phase phase=Phase::IDLE;Stage stage=Stage::FIRST;
bool beltRunning=false,pulseLow=false,returning=false,ir1Latched=false,ir2Latched=false;
unsigned long phaseAt=0,returnAt=0,beltAt=0,lastEdge=0,ir1At=0,ir2At=0,waitAt=0,actStopAt=0;
char input[64];uint8_t used=0;bool discard=false;unsigned long lastByteAt=0;
struct Sensor{
 uint8_t pin;const char* name;uint8_t raw=HIGH,stable=HIGH;bool ready=false;unsigned long changed=0;
 Sensor(uint8_t p,const char* n):pin(p),name(n){}
};
Sensor ir1(IR1_PIN,"IR"),ir2(IR2_PIN,"IR2");
void emit(const __FlashStringHelper* text){Serial.println(text);}
void stopAct(){analogWrite(ACT_ENA,0);digitalWrite(ACT_IN1,LOW);digitalWrite(ACT_IN2,LOW);actStopAt=millis();}
void stopBelt(){beltRunning=false;digitalWrite(STEP_PIN,HIGH);pulseLow=false;}
void stopAll(){stopAct();stopBelt();returning=false;phase=Phase::IDLE;}
void actuator(bool forward){stopAct();digitalWrite(ACT_IN1,forward?HIGH:LOW);digitalWrite(ACT_IN2,forward?LOW:HIGH);analogWrite(ACT_ENA,255);}
void startBelt(){digitalWrite(STEP_PIN,HIGH);pulseLow=false;beltAt=millis();lastEdge=micros();beltRunning=true;}
void reportTiming(unsigned long revision){
 Serial.print(F("TIMING:"));Serial.print(revision);Serial.print(':');Serial.print(ir1Delay);Serial.print(':');Serial.print(robotWait);Serial.print(':');Serial.println(ir2Delay);
}
bool number(const char* text,unsigned long& result){
 if(!text||!*text)return false;
 for(const char* p=text;*p;++p)if(*p<'0'||*p>'9')return false;
 errno=0;char* end=nullptr;result=strtoul(text,&end,10);return errno!=ERANGE&&end&&!*end;
}
bool clearSensor(const Sensor& sensor){return sensor.ready&&sensor.stable==HIGH&&sensor.raw==HIGH&&millis()-sensor.changed>=FILTER_MS;}
void serviceSensor(Sensor& sensor){
 const unsigned long now=millis();const uint8_t value=digitalRead(sensor.pin);
 if(value!=sensor.raw){sensor.raw=value;sensor.changed=now;}
 if((!sensor.ready||sensor.stable!=sensor.raw)&&now-sensor.changed>=FILTER_MS){
  sensor.stable=sensor.raw;sensor.ready=true;Serial.print(sensor.name);Serial.println(sensor.stable==LOW?F(":DETECTED"):F(":CLEAR"));
 }
}
void command(char* text){
 if(!strcmp(text,"s")){stopAll();emit(F("STOPPED:BOTH_READY_A"));return;}
 if(!strcmp(text,"?")){reportTiming(0);return;}
 if(text[0]=='T'&&text[1]==' '){
  if(phase!=Phase::IDLE){emit(F("BUSY:TIMING_LOCKED"));return;}
  strtok(text," ");char* r=strtok(nullptr," ");char* a=strtok(nullptr," ");char* b=strtok(nullptr," ");char* c=strtok(nullptr," ");char* extra=strtok(nullptr," ");
  unsigned long revision=0,x=0,y=0,z=0;
  if(extra||!number(r,revision)||revision==0||!number(a,x)||!number(b,y)||!number(c,z)||x>25000UL||y>30000UL||z>25000UL||x%500UL||y%500UL||z%500UL){emit(F("ERR:TIMING_RANGE"));return;}
  ir1Delay=x;robotWait=y;ir2Delay=z;reportTiming(revision);return;
 }
 if(!strcmp(text,"j")||!strcmp(text,"k")){
  if(phase!=Phase::IDLE){emit(F("BUSY:NO_JOG_DURING_MOTION"));return;}
  if(millis()-actStopAt<500UL){emit(F("ERR:ACTUATOR_COOLDOWN"));return;}
  phase=Phase::JOG;phaseAt=millis();stopBelt();actuator(text[0]=='j');
  emit(text[0]=='j'?F("JOG:FORWARD_1000MS"):F("JOG:REVERSE_1000MS"));return;
 }
 if(!strcmp(text,"q")){
  if(phase==Phase::IDLE||phase==Phase::JOG){emit(F("ERR:NO_ACTIVE_CYCLE"));return;}
  emit(F("QR:RECOGNIZED_CONTINUE_TO_IR"));return;
 }
 if(strcmp(text,"a")){emit(F("ERR:USE_A_Q_S_J_K_OR_T"));return;}
 if(phase!=Phase::IDLE){emit(F("BUSY:NO_QUEUED_RESTART"));return;}
 if(millis()-actStopAt<500UL){emit(F("ERR:ACTUATOR_COOLDOWN"));return;}
 if(!clearSensor(ir1)||!clearSensor(ir2)){emit(F("ERR:IR_SENSORS_NOT_CLEAR"));return;}
 runIr1Delay=ir1Delay;runRobotWait=robotWait;runIr2Delay=ir2Delay;
 ir1Latched=ir2Latched=false;stage=Stage::FIRST;
 phase=Phase::PUSH;phaseAt=millis();actuator(true);emit(F("ACTUATOR:PUSH_10000MS"));
}
void serviceCycle(){
 const unsigned long now=millis();
 if(phase==Phase::JOG){if(now-phaseAt>=JOG_MS){stopAll();emit(F("JOG:DONE_POSITION_UNVERIFIED"));}return;}
 if(phase==Phase::PUSH&&now-phaseAt>=PUSH_MS){stopAct();phase=Phase::PAUSE;phaseAt=now;emit(F("ACTUATOR:PAUSE_500MS"));}
 else if(phase==Phase::PAUSE&&now-phaseAt>=REVERSAL_MS){
  phase=Phase::RUN;returnAt=now;returning=true;actuator(false);startBelt();emit(F("RETURN_AND_BELT:STARTED"));
 }
 if(phase!=Phase::RUN)return;
 if(returning&&now-returnAt>=RETURN_MS){stopAct();returning=false;emit(F("RETURN_TIMER_DONE:POSITION_UNVERIFIED"));}
 if(beltRunning&&now-beltAt>=BELT_LIMIT_MS){stopAll();emit(F("ERR:IR_TRAVEL_TIMEOUT_STOP_BOTH"));return;}
 if(stage==Stage::FIRST){
  if(!ir1Latched&&ir1.ready&&ir1.stable==LOW){ir1Latched=true;ir1At=now;emit(F("IR_TIMER:STARTED"));}
  if(ir1Latched&&now-ir1At>=runIr1Delay){stopBelt();stage=Stage::ROBOT_WAIT;waitAt=now;emit(F("SIM:STATION_STOPPED"));}
 }else if(stage==Stage::ROBOT_WAIT&&now-waitAt>=runRobotWait){
  if(!clearSensor(ir2)){stopAll();emit(F("ERR:IR2_NOT_CLEAR_AT_RESTART"));return;}
  stage=Stage::SECOND;startBelt();emit(F("SIM:BELT_RESTARTED"));
 }else if(stage==Stage::SECOND){
  if(!ir2Latched&&ir2.ready&&ir2.stable==LOW){ir2Latched=true;ir2At=now;emit(F("IR2_TIMER:STARTED"));}
  if(ir2Latched&&now-ir2At>=runIr2Delay){stopBelt();stage=Stage::DONE;emit(F("BELT_STOPPED:IR2_DELAY_DONE"));}
 }
 if(stage==Stage::DONE&&!returning){stopAll();emit(F("CYCLE_DONE:READY_A"));}
}
void setup(){
 digitalWrite(STEP_PIN,HIGH);pinMode(STEP_PIN,OUTPUT);digitalWrite(DIR_PIN,HIGH);pinMode(DIR_PIN,OUTPUT);
 digitalWrite(ACT_ENA,LOW);digitalWrite(ACT_IN1,LOW);digitalWrite(ACT_IN2,LOW);pinMode(ACT_ENA,OUTPUT);pinMode(ACT_IN1,OUTPUT);pinMode(ACT_IN2,OUTPUT);
 pinMode(IR1_PIN,INPUT_PULLUP);pinMode(IR2_PIN,INPUT_PULLUP);ir1.raw=ir1.stable=digitalRead(IR1_PIN);ir2.raw=ir2.stable=digitalRead(IR2_PIN);ir1.changed=ir2.changed=millis();stopAll();
 Serial.begin(115200);emit(F("READY UNO_SIM_V1"));emit(F("MANUAL_JOG_MS:1000"));reportTiming(0);
}
void loop(){
 if(used&&millis()-lastByteAt>=500UL){used=0;discard=false;emit(F("ERR:PARTIAL_COMMAND"));}
 for(uint8_t n=0;n<8&&Serial.available();++n){
  const char ch=Serial.read();lastByteAt=millis();
  if(ch=='\n'||ch=='\r'){
   if(discard){emit(F("ERR:COMMAND_TOO_LONG"));}else if(used){input[used]=0;command(input);}
   used=0;discard=false;
  }else if(used<sizeof(input)-1&&!discard){input[used++]=ch;}else discard=true;
 }
 serviceSensor(ir1);serviceSensor(ir2);serviceCycle();
 if(beltRunning&&micros()-lastEdge>=HALF_PERIOD_US){lastEdge=micros();pulseLow=!pulseLow;digitalWrite(STEP_PIN,pulseLow?LOW:HIGH);}
}
