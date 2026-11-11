#pragma once

// Start with serial simulation only. Set false after wiring, direction,
// timing, stroke clearance and independent stop arrangements are verified.
constexpr bool DRY_RUN = true;
constexpr uint8_t ENA_PIN = 9, IN1_PIN = 7, IN2_PIN = 8;
constexpr uint8_t MOTOR_PWM = 255;
// J direction from ActuatorJogTest is IN1 HIGH / IN2 LOW.
// Physical pushing direction has not yet been confirmed.
constexpr bool PUSH_IS_J = true;

// Provisional simulation values, NOT measured/calibrated pallet-feed times.
constexpr uint32_t PUSH_MAX_MS = 2000UL;
constexpr uint32_t RETURN_MS = 2000UL;
constexpr uint32_t REVERSAL_PAUSE_MS = 500UL;
// Deadline starts when ORDER is accepted, not when pushing finishes.
constexpr uint32_t CAMERA_TIMEOUT_MS = 30000UL;
constexpr uint32_t SERIAL_BAUD = 115200UL;
