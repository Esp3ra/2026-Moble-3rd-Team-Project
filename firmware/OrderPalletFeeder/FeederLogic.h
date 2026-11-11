#pragma once
#include <stdint.h>

// Hardware-independent state machine. All elapsed-time arithmetic is uint32_t.
namespace feeder {
enum class State : uint8_t {
  LOCKED, IDLE, PUSHING, WAIT_CAMERA, REVERSAL_PAUSE,
  RETURNING, RETURN_TIME_ELAPSED, FAULT
};
enum class Drive : uint8_t { OFF, PUSH, RETURN };
enum class Result : uint8_t { OK, DUPLICATE, BUSY, BAD_ID, WRONG_STATE };
struct Settings {
  uint32_t pushMs, returnMs, reversalPauseMs, cameraTimeoutMs;
};

class Controller {
 public:
  explicit Controller(Settings settings) : cfg(settings) {}
  State state = State::LOCKED;
  uint32_t job = 0;
  uint32_t highestAccepted = 0;

  bool validConfig() const {
    return cfg.pushMs > 0 && cfg.returnMs > 0 && cfg.reversalPauseMs > 0 &&
           cfg.cameraTimeoutMs > cfg.pushMs &&
           cfg.cameraTimeoutMs < 0x80000000UL && cfg.returnMs < 0x80000000UL;
  }

  // A separate explicit acknowledgement of manually verified home position.
  // It never drives the actuator and never clears the duplicate watermark.
  Result confirmHome() {
    if (state != State::LOCKED && state != State::RETURN_TIME_ELAPSED &&
        state != State::FAULT) return Result::WRONG_STATE;
    if (!validConfig()) return Result::WRONG_STATE;
    state = State::IDLE;
    return Result::OK;
  }

  Result order(uint32_t id, uint32_t now) {
    if (id == 0 || id < highestAccepted) return Result::BAD_ID;
    if (highestAccepted != 0 && id == highestAccepted) return Result::DUPLICATE;
    if (state != State::IDLE) return Result::BUSY;
    job = highestAccepted = id;
    orderedAt = phaseAt = now;
    state = State::PUSHING;
    return Result::OK;
  }

  Result pallet(uint32_t id, uint32_t now) {
    tick(now);  // An event arriving at/after the deadline cannot revive a fault.
    if (id == 0 || id != job) return Result::BAD_ID;
    if (state == State::REVERSAL_PAUSE || state == State::RETURNING ||
        state == State::RETURN_TIME_ELAPSED) return Result::DUPLICATE;
    if (state != State::PUSHING && state != State::WAIT_CAMERA)
      return Result::WRONG_STATE;
    // Camera detection can stop the push early. Reverse only after dead time.
    state = State::REVERSAL_PAUSE;
    phaseAt = now;
    return Result::OK;
  }

  void stop() { state = State::LOCKED; }

  void tick(uint32_t now) {
    if ((state == State::PUSHING || state == State::WAIT_CAMERA) &&
        static_cast<uint32_t>(now - orderedAt) >= cfg.cameraTimeoutMs) {
      state = State::FAULT;
      return;
    }
    if (state == State::PUSHING &&
        static_cast<uint32_t>(now - phaseAt) >= cfg.pushMs) {
      state = State::WAIT_CAMERA;  // Stop pushing; never auto-return on time alone.
    } else if (state == State::REVERSAL_PAUSE &&
               static_cast<uint32_t>(now - phaseAt) >= cfg.reversalPauseMs) {
      state = State::RETURNING;
      phaseAt = now;
    } else if (state == State::RETURNING &&
               static_cast<uint32_t>(now - phaseAt) >= cfg.returnMs) {
      // Timed output completion is not evidence of physical home position.
      state = State::RETURN_TIME_ELAPSED;
    }
  }

  Drive drive() const {
    if (state == State::PUSHING) return Drive::PUSH;
    if (state == State::RETURNING) return Drive::RETURN;
    return Drive::OFF;
  }

 private:
  Settings cfg;
  uint32_t orderedAt = 0, phaseAt = 0;
};
}  // namespace feeder
