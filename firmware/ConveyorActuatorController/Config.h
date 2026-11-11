#pragma once

// Existing conveyor wiring. Do not change the driver DIP switches here.
constexpr uint8_t CONVEYOR_STEP_PIN = 5;
constexpr uint8_t CONVEYOR_DIR_PIN = 6;
constexpr bool CONVEYOR_INVERT_DIRECTION = true;  // F -> DIR LOW
constexpr float CONVEYOR_MAX_SPEED = 1000.0f;    // pulses/second
constexpr float CONVEYOR_ACCELERATION = 250.0f; // pulses/second^2
constexpr unsigned long CONVEYOR_TIMEOUT_MS = 10000UL;

// Dry NC contact: D2 -> NC contact -> GND.
// Closed/LOW = healthy. Open/HIGH = stop or disconnected wire.
// This input is monitoring only; hardware must independently remove drive power.
constexpr uint8_t ESTOP_PIN = 2;
// Separate momentary NO reset button: D3 -> button -> GND.
constexpr uint8_t RESET_PIN = 3;
constexpr unsigned long ESTOP_HEALTHY_MS = 500UL;
constexpr unsigned long RESET_DEBOUNCE_MS = 30UL;

// Two-wire 12V/100mm actuator + L298N channel A (ENA jumper removed).
// Keep disabled until physical E-stop/reset and external NC limits are verified.
// Use the separate ActuatorJogTest sketch for initial unloaded 2-second tests.
constexpr bool ACTUATOR_HARDWARE_VERIFIED = false;
constexpr uint8_t ACTUATOR_ENA_PIN = 9;
constexpr uint8_t ACTUATOR_IN1_PIN = 7;
constexpr uint8_t ACTUATOR_IN2_PIN = 8;
constexpr uint8_t ACTUATOR_EXTEND_LIMIT_PIN = A0;
constexpr uint8_t ACTUATOR_RETRACT_LIMIT_PIN = A1;
// Candidate NC limits to GND: open/HIGH blocks travel toward that end.
// An open wire cannot be distinguished from a reached limit by this circuit.
constexpr uint8_t ACTUATOR_PWM = 255;
constexpr unsigned long ACTUATOR_TIMEOUT_MS = 3000UL;

// The 3-second actuator timeout is only an initial commissioning ceiling.
// It is NOT calibrated travel distance. Determine it from actual stroke/speed.
constexpr unsigned long SERIAL_BAUD = 9600UL;
constexpr unsigned long RX_PARTIAL_TIMEOUT_MS = 500UL;
