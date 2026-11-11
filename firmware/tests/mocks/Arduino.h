#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <deque>
#include <string>

constexpr int LOW = 0, HIGH = 1, OUTPUT = 1;
class __FlashStringHelper;
using PGM_P = const char*;
#define F(s) reinterpret_cast<const __FlashStringHelper*>(s)
inline size_t strlen_P(PGM_P s) { return strlen(s); }
static unsigned long fakeNow = 0;
static int fakeDigital[20] = {}, fakePwm[20] = {};
inline unsigned long millis() { return fakeNow; }
inline void pinMode(uint8_t, int) {}
inline void digitalWrite(uint8_t pin, int v) { fakeDigital[pin] = v; }
inline void analogWrite(uint8_t pin, int v) { fakePwm[pin] = v; }
struct FakeSerial {
  std::deque<char> rx;
  std::string log;
  void begin(unsigned long) {}
  int availableForWrite() { return 64; }
  int available() { return static_cast<int>(rx.size()); }
  int read() { char c = rx.front(); rx.pop_front(); return c; }
  void println(const __FlashStringHelper* s) {
    log += reinterpret_cast<const char*>(s);
    log += '\n';
  }
  void feed(const char* s) { for (; *s; ++s) rx.push_back(*s); }
};
static FakeSerial Serial;
