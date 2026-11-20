#pragma once

#include <cstdint>
#include <string>

constexpr uint8_t LOW = 0, HIGH = 1, OUTPUT = 1, INPUT_PULLUP = 2;

class __FlashStringHelper;
#define F(value) (reinterpret_cast<const __FlashStringHelper*>(value))

unsigned long millis();
unsigned long micros();
void pinMode(uint8_t, uint8_t);
int digitalRead(uint8_t pin);
void digitalWrite(uint8_t pin, uint8_t value);
void analogWrite(uint8_t pin, uint8_t value);

struct SerialStub {
  std::string input;
  std::string output;

  void begin(unsigned long) {}
  bool available() const { return !input.empty(); }
  char read() {
    const char byte = input.front();
    input.erase(0, 1);
    return byte;
  }
  void print(const char* value) { output += value; }
  void print(const __FlashStringHelper* value) { print(reinterpret_cast<const char*>(value)); }
  void print(unsigned long value) { output += std::to_string(value); }
  void print(char value) { output += value; }

  template <typename T>
  void println(T value) {
    print(value);
    print('\n');
  }
};

extern SerialStub Serial;
