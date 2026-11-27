// Live LINE controller, common serial v1. No timed robot-arm simulation.
#include <Arduino.h>
#include <stdint.h>
#include <string.h>
#include "hardware_profile.h"

enum Op:uint8_t {NONE,DISP,ASM,END,PAUSE_OP,RESUME_OP};
enum Phase:uint8_t {IDLE,PUSH,REVERSAL,RETRACT,TRAVEL};
enum Reply:uint8_t {ACK,RUN,DONE,FAIL,HOLD};
enum Detail:uint8_t {OK,DISP_TIMER,IR1_STOP,IR2_STOP,BOTH_PAUSED,RESUMED,
  PROFILE_REQUIRED,BAD_TRANSITION,SENSOR_NOT_CLEAR,TRAVEL_TIMEOUT,WATCHDOG,UNKNOWN_SEQ};
struct Record {uint32_t cyc=0,seq=0;Op op=NONE;Reply state=ACK;Detail detail=OK;};
constexpr uint8_t CACHE_SIZE=12;
Record records[CACHE_SIZE];uint8_t nextSlot=0;int8_t active=-1;
uint32_t sid=0,maxSeq=0,currentCycle=0;uint8_t completedStage=0;
Phase phase=IDLE;bool paused=false,faulted=false,pulseLow=false;
unsigned long phaseAt=0,pausedAt=0,lastHeartbeat=0,lastEdge=0,travelAt=0;
bool detected=false;unsigned long detectedAt=0;
char input[64];uint8_t used=0;bool discard=false,seenCR=false;unsigned long byteAt=0;
struct Sensor {
  uint8_t pin,raw=HIGH,stable=HIGH;bool ready=false;unsigned long changed=0;
  Sensor(uint8_t p):pin(p){}
};
Sensor ir1(IR1_PIN),ir2(IR2_PIN);
bool elapsed(unsigned long at,unsigned long start,unsigned long duration){return at-start>=duration;}

bool profileReady(){return WIRING_VERIFIED && TIMING_PROFILE_APPROVED &&
  PUSH_MS>0 && RETRACT_MS>0 && REVERSAL_MS>0 && TRAVEL_LIMIT_MS>0 &&
  HEARTBEAT_LIMIT_MS>0 && SENSOR_FILTER_MS>0 && STEP_HALF_PERIOD_US>0 &&
  PUSH_MS<0x80000000UL && RETRACT_MS<0x80000000UL && REVERSAL_MS<0x80000000UL &&
  IR1_DELAY_MS<TRAVEL_LIMIT_MS && IR2_DELAY_MS<TRAVEL_LIMIT_MS;}
void beltStop(){digitalWrite(STEP_PIN,HIGH);pulseLow=false;}
void actuatorStop(){analogWrite(ACT_ENA,0);digitalWrite(ACT_IN1,LOW);digitalWrite(ACT_IN2,LOW);}
void stopOutputs(){beltStop();actuatorStop();}
void actuator(bool forward){
  actuatorStop();digitalWrite(ACT_IN1,forward?HIGH:LOW);digitalWrite(ACT_IN2,forward?LOW:HIGH);analogWrite(ACT_ENA,255);
}
const __FlashStringHelper* replyText(Reply r){
  switch(r){case ACK:return F("ACK");case RUN:return F("RUN");case DONE:return F("DONE");case FAIL:return F("FAIL");default:return F("HOLD");}
}
const __FlashStringHelper* detailText(Detail d){
  switch(d){case OK:return F("OK");case DISP_TIMER:return F("DISP_TIMER");case IR1_STOP:return F("IR1_STOP");case IR2_STOP:return F("IR2_STOP");
    case BOTH_PAUSED:return F("BOTH_PAUSED");case RESUMED:return F("RESUMED");case PROFILE_REQUIRED:return F("PROFILE_REQUIRED");
    case BAD_TRANSITION:return F("BAD_TRANSITION");case SENSOR_NOT_CLEAR:return F("SENSOR_NOT_CLEAR");case TRAVEL_TIMEOUT:return F("TRAVEL_TIMEOUT");
    case WATCHDOG:return F("WATCHDOG");default:return F("UNKNOWN_SEQ");}
}
void emitRecord(const Record& r){
  Serial.print(F("R "));Serial.print(sid);Serial.print(' ');Serial.print(r.cyc);Serial.print(' ');Serial.print(r.seq);Serial.print(' ');
  Serial.print(replyText(r.state));Serial.print(' ');Serial.println(detailText(r.detail));
}
void error(const __FlashStringHelper* msg){Serial.print(F("ERR "));Serial.println(msg);}
int8_t findRecord(uint32_t seq){for(uint8_t i=0;i<CACHE_SIZE;i++)if(records[i].op!=NONE&&records[i].seq==seq)return i;return -1;}
void setReply(int8_t index,Reply state,Detail detail){records[index].state=state;records[index].detail=detail;emitRecord(records[index]);}
void finish(Detail detail){stopOutputs();int8_t old=active;active=-1;phase=IDLE;setReply(old,DONE,detail);}
void failActive(Detail detail){
  stopOutputs();faulted=true;paused=false;phase=IDLE;
  if(active>=0){int8_t old=active;active=-1;setReply(old,FAIL,detail);}else error(detailText(detail));
}
bool number(const char* s,uint32_t& value){
  if(!s||!*s)return false;value=0;
  for(;*s;s++){if(*s<'0'||*s>'9')return false;uint8_t n=*s-'0';if(value>(UINT32_MAX-n)/10UL)return false;value=value*10+n;}return true;
}
Op parseOp(const char* s){
  if(!strcmp(s,"DISP"))return DISP;if(!strcmp(s,"ASM"))return ASM;if(!strcmp(s,"END"))return END;
  if(!strcmp(s,"PAUSE"))return PAUSE_OP;if(!strcmp(s,"RESUME"))return RESUME_OP;return NONE;
}
void sensorUpdate(Sensor& sensor){
  unsigned long at=millis();uint8_t value=digitalRead(sensor.pin);
  if(value!=sensor.raw){sensor.raw=value;sensor.changed=at;}
  if((!sensor.ready||sensor.raw!=sensor.stable)&&elapsed(at,sensor.changed,SENSOR_FILTER_MS)){sensor.ready=true;sensor.stable=sensor.raw;}
}
bool clearSensor(const Sensor& sensor){return sensor.ready&&sensor.stable!=IR_DETECTED&&sensor.raw!=IR_DETECTED;}
void applyPhase(){
  if(paused||faulted)return;
  if(phase==PUSH)actuator(true);else if(phase==RETRACT)actuator(false);
  else if(phase==TRAVEL){beltStop();lastEdge=micros();}
}
void accept(uint32_t cyc,uint32_t seq,Op op){
  int8_t prior=findRecord(seq);
  if(prior>=0){
    if(records[prior].cyc!=cyc||records[prior].op!=op){error(F("SEQ_CONFLICT"));return;}
    emitRecord(records[prior]);return;
  }
  // A cache eviction must never turn an old C into a new physical operation.
  if(seq<=maxSeq){Record unknown;unknown.cyc=cyc;unknown.seq=seq;unknown.op=op;unknown.state=FAIL;unknown.detail=UNKNOWN_SEQ;emitRecord(unknown);return;}
  int8_t slot=-1;
  for(uint8_t n=0;n<CACHE_SIZE;n++){uint8_t candidate=(nextSlot+n)%CACHE_SIZE;if(candidate!=active){slot=candidate;break;}}
  if(slot<0){error(F("CACHE_BUSY"));return;}
  nextSlot=(slot+1)%CACHE_SIZE;maxSeq=seq;
  records[slot].cyc=cyc;records[slot].seq=seq;records[slot].op=op;records[slot].state=ACK;records[slot].detail=OK;
  if(op==PAUSE_OP){
    if((currentCycle&&completedStage<3&&cyc!=currentCycle)||(active>=0&&records[active].cyc!=cyc)){setReply(slot,FAIL,BAD_TRANSITION);return;}
    stopOutputs();if(!paused)pausedAt=millis();paused=true;
    if(active>=0)setReply(active,HOLD,OK);setReply(slot,DONE,BOTH_PAUSED);return;
  }
  if(op==RESUME_OP){
    if(!paused||faulted||(currentCycle&&completedStage<3&&cyc!=currentCycle)){setReply(slot,FAIL,BAD_TRANSITION);return;}
    unsigned long elapsed=millis()-pausedAt;phaseAt+=elapsed;travelAt+=elapsed;if(detected)detectedAt+=elapsed;
    paused=false;applyPhase();setReply(slot,DONE,RESUMED);if(active>=0)setReply(active,RUN,OK);return;
  }
  if(!profileReady()){setReply(slot,FAIL,PROFILE_REQUIRED);return;}
  if(faulted||paused||active>=0||!cyc){setReply(slot,FAIL,BAD_TRANSITION);return;}
  if(op==DISP){
    if((currentCycle&&completedStage!=3)||currentCycle==cyc){setReply(slot,FAIL,BAD_TRANSITION);return;}
    if(!clearSensor(ir1)||!clearSensor(ir2)){setReply(slot,FAIL,SENSOR_NOT_CLEAR);return;}
    currentCycle=cyc;completedStage=0;phase=PUSH;
  }else{
    if(cyc!=currentCycle||completedStage!=(op==ASM?1:2)){setReply(slot,FAIL,BAD_TRANSITION);return;}
    if(!clearSensor(op==ASM?ir1:ir2)){setReply(slot,FAIL,SENSOR_NOT_CLEAR);return;}
    phase=TRAVEL;detected=false;travelAt=millis();
  }
  active=slot;phaseAt=millis();stopOutputs();applyPhase();setReply(slot,ACK,OK);setReply(slot,RUN,OK);
}
void command(char* text){
  char* tokens[7];uint8_t count=0;char* save=nullptr;
  for(char* t=strtok_r(text," ",&save);t;t=strtok_r(nullptr," ",&save)){if(count==7){error(F("FORMAT"));return;}tokens[count++]=t;}
  uint32_t session=0,cyc=0,seq=0;
  if(count==3&&!strcmp(tokens[0],"V1")&&!strcmp(tokens[1],"SYNC")){
    if(!number(tokens[2],session)||!session){error(F("SESSION"));return;}
    if(session!=sid){
      if(active>=0||faulted){error(F("SESSION_BUSY"));return;}
      sid=session;maxSeq=0;for(auto& r:records)r=Record();
    }
    lastHeartbeat=millis();Serial.print(F("V1 OK "));Serial.println(sid);return;
  }
  if(count<2||!number(tokens[1],session)||!sid||session!=sid){error(F("SESSION"));return;}
  if(count==2&&!strcmp(tokens[0],"H")){lastHeartbeat=millis();Serial.print(F("V1 ALIVE "));Serial.println(sid);return;}
  if(count<4||!number(tokens[2],cyc)||!number(tokens[3],seq)||!seq){error(F("FORMAT"));return;}
  if(count==4&&!strcmp(tokens[0],"Q")){
    int8_t index=findRecord(seq);if(index>=0&&records[index].cyc==cyc)emitRecord(records[index]);
    else{Record r;r.cyc=cyc;r.seq=seq;r.op=DISP;r.state=FAIL;r.detail=UNKNOWN_SEQ;emitRecord(r);}return;
  }
  if(count==5&&!strcmp(tokens[0],"C")){
    Op op=parseOp(tokens[4]);if(op==NONE||(!cyc&&op!=PAUSE_OP&&op!=RESUME_OP)){error(F("FORMAT"));return;}
    accept(cyc,seq,op);return;
  }
  error(F("FORMAT"));
}
void serviceMotion(){
  unsigned long at=millis();
  if(active<0||paused||faulted)return;
  if(phase==PUSH&&elapsed(at,phaseAt,PUSH_MS)){actuatorStop();phase=REVERSAL;phaseAt=at;}
  else if(phase==REVERSAL&&elapsed(at,phaseAt,REVERSAL_MS)){phase=RETRACT;phaseAt=at;actuator(false);}
  else if(phase==RETRACT&&elapsed(at,phaseAt,RETRACT_MS)){completedStage=1;finish(DISP_TIMER);}
  else if(phase==TRAVEL){
    if(elapsed(at,travelAt,TRAVEL_LIMIT_MS)){failActive(TRAVEL_TIMEOUT);return;}
    Sensor& target=records[active].op==ASM?ir1:ir2;
    if(!detected&&target.ready&&target.stable==IR_DETECTED){detected=true;detectedAt=at;}
    unsigned long delayMs=records[active].op==ASM?IR1_DELAY_MS:IR2_DELAY_MS;
    if(detected&&at-detectedAt>=delayMs){bool first=records[active].op==ASM;completedStage=first?2:3;finish(first?IR1_STOP:IR2_STOP);return;}
    if(elapsed(micros(),lastEdge,STEP_HALF_PERIOD_US)){lastEdge=micros();pulseLow=!pulseLow;digitalWrite(STEP_PIN,pulseLow?LOW:HIGH);}
  }
}
void setup(){
  digitalWrite(STEP_PIN,HIGH);pinMode(STEP_PIN,OUTPUT);digitalWrite(DIR_PIN,HIGH);pinMode(DIR_PIN,OUTPUT);
  digitalWrite(ACT_ENA,LOW);digitalWrite(ACT_IN1,LOW);digitalWrite(ACT_IN2,LOW);
  pinMode(ACT_ENA,OUTPUT);pinMode(ACT_IN1,OUTPUT);pinMode(ACT_IN2,OUTPUT);
  pinMode(IR1_PIN,INPUT_PULLUP);pinMode(IR2_PIN,INPUT_PULLUP);stopOutputs();
  ir1.changed=ir2.changed=millis();Serial.begin(115200);Serial.println(F("READY V1"));
}
void loop(){
  // Keep an overflowed/partial line discarded through LF, never execute its suffix.
  if(used&&millis()-byteAt>=500UL){used=0;discard=true;}
  for(uint8_t n=0;n<16&&Serial.available();n++){
    char ch=Serial.read();byteAt=millis();
    if(ch=='\n'){
      if(discard)error(F("LINE_TOO_LONG_OR_PARTIAL"));else if(used){input[used]=0;command(input);}
      used=0;discard=false;seenCR=false;
    }else if(ch=='\r'){
      // CR may only terminate the body; an embedded CR is a malformed line.
      if(seenCR)discard=true;seenCR=true;
    }else if(ch<32||ch>126||seenCR){discard=true;}else if(used<63&&!discard)input[used++]=ch;else discard=true;
  }
  sensorUpdate(ir1);sensorUpdate(ir2);
  if(sid&&profileReady()&&!faulted&&millis()-lastHeartbeat>HEARTBEAT_LIMIT_MS){failActive(WATCHDOG);}
  serviceMotion();
}
