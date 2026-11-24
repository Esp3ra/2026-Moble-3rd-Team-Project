// Temporary ngrok bench firmware. Not the production process controller.
// Existing user-confirmed wiring; actuator <=1000 ms; belt <=500 ms.
#include <Arduino.h>
#include <stdint.h>
#include <string.h>
constexpr uint8_t STEP_PIN=5,DIR_PIN=6,ACT_ENA=9,ACT_IN1=7,ACT_IN2=8,IR1=4,IR2=2;
constexpr unsigned long STEP_HALF_US=80,LINK_LIMIT_MS=1500,COOLDOWN_MS=500;
enum Op:uint8_t {NONE,PING,STOP,BELT,EXTEND,RETRACT};
struct Record {uint32_t id=0;Op op=NONE;uint16_t duration=0;uint8_t state=0;};
Record records[8];int8_t active=-1;uint8_t slot=0;uint32_t maxId=0;
unsigned long started=0,lastAlive=0,lastEdge=0,actStopped=0;bool pulse=false;
char line[64];uint8_t used=0;bool discard=false,cr=false;
void outputsOff(){digitalWrite(STEP_PIN,HIGH);pulse=false;analogWrite(ACT_ENA,0);digitalWrite(ACT_IN1,LOW);digitalWrite(ACT_IN2,LOW);actStopped=millis();}
void reply(uint32_t id,const __FlashStringHelper* state,const __FlashStringHelper* detail){Serial.print(F("R "));Serial.print(id);Serial.print(' ');Serial.print(state);Serial.print(' ');Serial.println(detail);}
void halt(const __FlashStringHelper* why){outputsOff();if(active>=0){records[active].state=3;reply(records[active].id,F("STOPPED"),why);active=-1;}}
bool number(const char* p,uint32_t& out){if(!p||!*p)return false;out=0;for(;*p;p++){if(*p<'0'||*p>'9')return false;uint8_t n=*p-'0';if(out>(UINT32_MAX-n)/10UL)return false;out=out*10+n;}return true;}
Op operation(const char* p){if(!strcmp(p,"PING"))return PING;if(!strcmp(p,"STOP"))return STOP;if(!strcmp(p,"BELT"))return BELT;if(!strcmp(p,"EXTEND"))return EXTEND;if(!strcmp(p,"RETRACT"))return RETRACT;return NONE;}
void command(char* input){
  if(!strcmp(input,"?")){lastAlive=millis();Serial.println(F("ALIVE MOBOT_WIRELESS_TEST"));Serial.print(F("S IR1="));Serial.print(digitalRead(IR1)==LOW);Serial.print(F(" IR2="));Serial.println(digitalRead(IR2)==LOW);return;}
  char* save=nullptr;char* t=strtok_r(input," ",&save);char* idText=strtok_r(nullptr," ",&save);char* opText=strtok_r(nullptr," ",&save);char* msText=strtok_r(nullptr," ",&save);char* extra=strtok_r(nullptr," ",&save);
  uint32_t id=0,ms=0;
  if(!t||strcmp(t,"T")||extra||!opText||!number(idText,id)||!number(msText,ms)){Serial.println(F("ERR FORMAT"));return;}
  Op op=operation(opText);if(op==NONE||ms>((op==EXTEND||op==RETRACT)?1000UL:op==BELT?500UL:0UL)||((op==PING||op==STOP)?ms!=0:ms==0)){reply(id,F("FAIL"),F("RANGE"));return;}
  // ID 0 is reserved for local watchdog STOP and never starts motion.
  if(!id){if(op==STOP){halt(F("LOCAL_STOP"));reply(0,F("DONE"),F("BOTH_STOPPED"));}else reply(0,F("FAIL"),F("ID"));return;}
  for(uint8_t i=0;i<8;i++)if(records[i].op!=NONE&&records[i].id==id){
    if(records[i].op!=op||records[i].duration!=ms){reply(id,F("FAIL"),F("CONFLICT"));return;}
    reply(id,records[i].state==1?F("RUN"):records[i].state==2?F("DONE"):records[i].state==3?F("STOPPED"):F("FAIL"),F("CACHED"));return;
  }
  if(id<=maxId){reply(id,F("FAIL"),F("OLD_ID"));return;}maxId=id;
  while(slot==active)slot=(slot+1)%8;uint8_t index=slot;slot=(slot+1)%8;
  records[index].id=id;records[index].op=op;records[index].duration=ms;records[index].state=4;
  if(op==STOP){halt(F("OPERATOR"));records[index].state=2;reply(id,F("DONE"),F("BOTH_STOPPED"));return;}
  if(op==PING){records[index].state=2;reply(id,F("DONE"),F("PONG"));return;}
  if(active>=0){reply(id,F("FAIL"),F("BUSY"));return;}
  if((op==EXTEND||op==RETRACT)&&millis()-actStopped<COOLDOWN_MS){reply(id,F("FAIL"),F("COOLDOWN"));return;}
  outputsOff();active=index;started=millis();lastAlive=started;records[index].state=1;
  if(op==BELT){digitalWrite(DIR_PIN,HIGH);lastEdge=micros();}
  else{digitalWrite(ACT_IN1,op==EXTEND?HIGH:LOW);digitalWrite(ACT_IN2,op==EXTEND?LOW:HIGH);analogWrite(ACT_ENA,255);}
  reply(id,F("RUN"),F("STARTED"));
}
void setup(){
  digitalWrite(STEP_PIN,HIGH);pinMode(STEP_PIN,OUTPUT);digitalWrite(DIR_PIN,HIGH);pinMode(DIR_PIN,OUTPUT);
  digitalWrite(ACT_ENA,LOW);digitalWrite(ACT_IN1,LOW);digitalWrite(ACT_IN2,LOW);pinMode(ACT_ENA,OUTPUT);pinMode(ACT_IN1,OUTPUT);pinMode(ACT_IN2,OUTPUT);
  pinMode(IR1,INPUT_PULLUP);pinMode(IR2,INPUT_PULLUP);outputsOff();Serial.begin(115200);Serial.println(F("READY MOBOT_WIRELESS_TEST"));
}
void loop(){
  for(uint8_t n=0;n<16&&Serial.available();n++){
    char c=Serial.read();if(c=='\n'){if(!discard&&used){line[used]=0;command(line);}else if(discard)Serial.println(F("ERR LINE"));used=0;discard=false;cr=false;}
    else if(c=='\r'){if(cr)discard=true;cr=true;}
    else if(c<32||c>126||cr||used>=63)discard=true;else if(!discard)line[used++]=c;
  }
  if(active<0)return;
  if(millis()-lastAlive>LINK_LIMIT_MS){halt(F("LINK_LOST"));return;}
  Record& r=records[active];bool irStop=r.op==BELT&&(digitalRead(IR1)==LOW||digitalRead(IR2)==LOW);
  if(millis()-started>=r.duration||irStop){outputsOff();r.state=2;reply(r.id,F("DONE"),irStop?F("IR_STOP"):F("TIMER"));active=-1;return;}
  if(r.op==BELT&&micros()-lastEdge>=STEP_HALF_US){lastEdge=micros();pulse=!pulse;digitalWrite(STEP_PIN,pulse?LOW:HIGH);}
}
