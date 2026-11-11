#include <assert.h>
#include <stdio.h>
#include "../OrderPalletFeeder/FeederLogic.h"
using namespace feeder;

Controller ready() {
  Controller c({2000, 2000, 500, 30000});
  assert(c.confirmHome() == Result::OK);
  return c;
}

int main() {
  Controller locked({2000, 2000, 500, 30000});
  assert(locked.drive() == Drive::OFF);
  assert(locked.order(1, 0) == Result::BUSY);
  Controller invalid({0, 2000, 500, 30000});
  assert(invalid.confirmHome() == Result::WRONG_STATE);

  auto c = ready();
  assert(c.order(10, 100) == Result::OK);
  assert(c.drive() == Drive::PUSH);
  assert(c.order(10, 200) == Result::DUPLICATE);
  assert(c.order(11, 200) == Result::BUSY);
  assert(c.pallet(9, 200) == Result::BAD_ID);
  c.tick(2099);
  assert(c.state == State::PUSHING);
  c.tick(2100);
  assert(c.state == State::WAIT_CAMERA && c.drive() == Drive::OFF);
  c.tick(5000);
  assert(c.state == State::WAIT_CAMERA);  // No timed automatic return.
  assert(c.pallet(10, 5000) == Result::OK);
  assert(c.drive() == Drive::OFF);
  c.tick(5499);
  assert(c.state == State::REVERSAL_PAUSE);
  c.tick(5500);
  assert(c.drive() == Drive::RETURN);
  assert(c.pallet(10, 6000) == Result::DUPLICATE);
  c.tick(7499);
  assert(c.drive() == Drive::RETURN);  // Duplicate did not reset phase time.
  c.tick(7500);
  assert(c.state == State::RETURN_TIME_ELAPSED && c.drive() == Drive::OFF);
  assert(c.order(11, 8000) == Result::BUSY);  // Home must be confirmed.
  assert(c.confirmHome() == Result::OK);
  assert(c.order(10, 8000) == Result::DUPLICATE);
  assert(c.order(9, 8000) == Result::BAD_ID);
  assert(c.order(11, 8000) == Result::OK);

  auto early = ready();
  assert(early.order(1, 0) == Result::OK);
  assert(early.pallet(1, 100) == Result::OK);
  assert(early.drive() == Drive::OFF);
  early.tick(600);
  assert(early.drive() == Drive::RETURN);

  // STOP must cancel every active phase, including reversal pause.
  for (int phase = 0; phase < 4; ++phase) {
    auto s = ready();
    s.order(1, 0);
    if (phase == 1) s.tick(2000);
    if (phase >= 2) s.pallet(1, 100);
    if (phase == 3) s.tick(600);
    s.stop();
    s.tick(50000);
    assert(s.state == State::LOCKED && s.drive() == Drive::OFF);
    assert(s.pallet(1, 50001) == Result::WRONG_STATE);
    assert(s.order(2, 50001) == Result::BUSY);
  }

  auto timeout = ready();
  timeout.order(1, 0);
  timeout.tick(30000);
  assert(timeout.state == State::FAULT && timeout.drive() == Drive::OFF);
  assert(timeout.pallet(1, 30000) == Result::WRONG_STATE);
  assert(timeout.confirmHome() == Result::OK);
  assert(timeout.order(1, 30001) == Result::DUPLICATE);

  auto stale = ready();
  assert(stale.pallet(1, 0) == Result::BAD_ID);
  assert(stale.order(0, 0) == Result::BAD_ID);
  stale.order(UINT32_MAX, 0);
  assert(stale.order(UINT32_MAX, 1) == Result::DUPLICATE);
  assert(stale.order(1, 1) == Result::BAD_ID);

  auto wrap = ready();
  const uint32_t start = UINT32_MAX - 1000;
  wrap.order(1, start);
  wrap.tick(start + uint32_t(2000));
  assert(wrap.state == State::WAIT_CAMERA);
  wrap.pallet(1, start + uint32_t(2100));
  wrap.tick(start + uint32_t(2600));
  assert(wrap.state == State::RETURNING);
  wrap.tick(start + uint32_t(4600));
  assert(wrap.state == State::RETURN_TIME_ELAPSED);
  puts("Feeder state transitions passed (host simulation, no hardware).");
}
