"""Connect the LINE adapter to the USB serial port and poll its v1 protocol."""
import logging


LOG = logging.getLogger("mobot.line")


def run_serial(adapter):
    import serial

    waiting_for_usb = False
    while not adapter.stop.wait(.02):
        if not adapter.configured():
            adapter.stop.wait(1)
            continue
        try:
            if adapter.serial is None:
                # SYNC arms the UNO watchdog. Wait for the authenticated server
                # before opening the serial link, even if the Pi booted first.
                if adapter.fault or not adapter.controller_recent():
                    adapter.stop.wait(1)
                    continue
                try:
                    adapter.serial = serial.Serial(
                        adapter.config["serial_port"], 115200, timeout=0,
                        write_timeout=1, exclusive=True,
                    )
                except OSError:
                    # An absent USB port cannot have received a command.
                    if not waiting_for_usb:
                        LOG.warning("LINE USB serial port is unavailable; waiting for the UNO.")
                    waiting_for_usb = True
                    adapter.stop.wait(1)
                    continue
                waiting_for_usb = False
                adapter.buffer.clear()
                adapter.discard = False
                adapter.synced = False
                adapter.write(f"V1 SYNC {adapter.sid}")
                opened = adapter.clock()

            adapter.pump()
            if not adapter.synced and adapter.clock() - opened > adapter.config["serial_response_timeout_seconds"]:
                raise OSError("UNO v1 negotiation failed")
        except Exception:
            if adapter.stop.is_set():
                return
            LOG.warning("LINE link fault; verify the UNO firmware and reconcile before operation.")
            adapter.uncertain("Serial disconnected or negotiation failed; inspect outputs before recovery.")
            if adapter.serial:
                try:
                    adapter.serial.close()
                except OSError:
                    pass
            adapter.serial = None
            adapter.stop.wait(1)
