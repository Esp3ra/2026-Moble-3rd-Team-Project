#pragma once
// Copy measured values here and review actual wiring before enabling outputs.
// Pins below are the existing UNO sketch's wiring, not a newly approved diagram.
constexpr bool WIRING_VERIFIED = true; // Confirmed by user 2026-11-21.
constexpr bool TIMING_PROFILE_APPROVED = false;
constexpr unsigned long PUSH_MS = 0;
constexpr unsigned long RETRACT_MS = 0;
constexpr unsigned long REVERSAL_MS = 0;
constexpr unsigned long IR1_DELAY_MS = 0;
constexpr unsigned long IR2_DELAY_MS = 0;
constexpr unsigned long TRAVEL_LIMIT_MS = 0;
constexpr unsigned long HEARTBEAT_LIMIT_MS = 0;
constexpr unsigned long SENSOR_FILTER_MS = 0;
constexpr unsigned long STEP_HALF_PERIOD_US = 0;
constexpr uint8_t STEP_PIN=5, DIR_PIN=6, ACT_ENA=9, ACT_IN1=7, ACT_IN2=8;
constexpr uint8_t IR1_PIN=4, IR2_PIN=2;
constexpr uint8_t IR_DETECTED=LOW;
