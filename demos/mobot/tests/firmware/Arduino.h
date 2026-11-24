#pragma once
#include <cstdint>
#include <string>
#include <sstream>
#include <cstring>
struct __FlashStringHelper {};
#define F(s) reinterpret_cast<const __FlashStringHelper*>(s)
constexpr uint8_t HIGH=1,LOW=0,OUTPUT=1,INPUT_PULLUP=2;
extern unsigned long fakeMillis;
extern int inPins[32],outPins[32];
inline unsigned long millis(){return fakeMillis;}
inline unsigned long micros(){return fakeMillis*1000UL;}
inline void pinMode(uint8_t,int){}
inline int digitalRead(uint8_t pin){return inPins[pin];}
inline void digitalWrite(uint8_t pin,int value){outPins[pin]=value;}
inline void analogWrite(uint8_t pin,int value){outPins[pin]=value;}
struct SerialStub {
 std::string output;
 void begin(unsigned long){}
 int available(){return 0;}
 char read(){return 0;}
 void print(const __FlashStringHelper* v){output+=reinterpret_cast<const char*>(v);}
 void print(const char* v){output+=v;}
 void print(char v){output+=v;}
 void print(unsigned long v){output+=std::to_string(v);}
 void print(int v){output+=std::to_string(v);}
 void println(const __FlashStringHelper* v){print(v);output+='\n';}
 void println(unsigned long v){print(v);output+='\n';}
};
extern SerialStub Serial;
