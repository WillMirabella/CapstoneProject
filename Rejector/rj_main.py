"""Rj-Pi main program: connects the hardware and network to RejectorPi.

    USB entry sensor -> on_entry()    (not written yet, see watch_usb_entry_sensor)
    GPIO exit sensor -> on_exit()     -> relay pulse if the can was flagged
    UDP port 5000    -> listen()      -> reject requests from the cameras
    every event      -> HubLink       -> UDP JSON to the HUB (fire-and-forget)

Keep this file in the same folder as rejector_pi.py and run: python3 rj_main.py
Stop rj_listener.py first, because both programs use port 5000.
"""

import json
import os
import socket
import sys
import threading
import time

from gpiozero import DigitalInputDevice, DigitalOutputDevice

from rejector_pi import RejectorPi

# BCM GPIO numbers, not physical pin positions on the header.
EXIT_SENSOR_PIN = 17  # GPIO17, physical pin 11
REJECT_RELAY_PIN = 27  # GPIO27, physical pin 13

# TODO: confirm against the actual exit sensor and relay module.
EXIT_SENSOR_PULL_UP = False  # False: signal goes high at a can. True: goes low.
RELAY_ACTIVE_HIGH = True  # Many relay modules are active-low; set False for those.

# TODO: tune on the real belt.
EXIT_SENSOR_BOUNCE_SECONDS = 0.01  # ignore repeat signals closer together than this
REJECT_DELAY_SECONDS = 0.0  # wait between the exit sensor and the relay firing
RELAY_PULSE_SECONDS = 0.1  # how long the relay stays on

# HUB that records runs for the dashboard. Set to None to run without a HUB.
HUB_ADDR = ("192.168.1.50", 5001)  # TODO: set to the HUB's IP address
HEALTH_INTERVAL_SECONDS = 5.0


class HubLink:
    """Sends RejectorPi events to the HUB as small JSON UDP datagrams.

    Never blocks and never raises: if the HUB is down, rejecting carries on.
    Each datagram carries:
        session - random id chosen at startup (ACNs restart at 1 on every boot)
        seq     - 0, 1, 2, ... per session, so the HUB can spot lost datagrams
        t       - seconds since startup (the Pi may have no real-time clock)
    """

    def __init__(self, addr):
        self.addr = addr
        self.session = os.urandom(4).hex()
        self.seq = 0
        self.started = time.monotonic()
        self.lock = threading.Lock()  # emit is called from several threads
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)

    def emit(self, event):
        if self.addr is None:
            return
        with self.lock:
            packet = dict(event, session=self.session, seq=self.seq,
                          t=round(time.monotonic() - self.started, 4))
            self.seq += 1
        try:
            self.sock.sendto(json.dumps(packet, separators=(",", ":")).encode(), self.addr)
        except OSError:
            pass  # HUB unreachable or send buffer full; never stall the rejector


hub = HubLink(HUB_ADDR)
rejector = RejectorPi(log=print, emit=hub.emit)
lock = threading.Lock()  # the sensors and network all update rejector
relay = None


def on_entry():
    """Call exactly once for each can passing the entry sensor."""
    with lock:
        try:
            acn = rejector.on_entry_sensor()
        except OverflowError as error:
            print(f"[ERROR] {error}")
            return
    print(f"[ENTRY] acn={acn}")


def on_exit():
    with lock:
        acn, rejected = rejector.on_exit_sensor()
    print(f"[EXIT] acn={acn} rejected={rejected}")
    if rejected:
        threading.Timer(REJECT_DELAY_SECONDS, pulse_relay).start()


def pulse_relay():
    relay.blink(on_time=RELAY_PULSE_SECONDS, off_time=0, n=1)


def watch_usb_entry_sensor():
    """TODO: read the USB entry sensor and call on_entry() once per can.

    Count changes, not readings: call on_entry() only when the sensor goes from
    clear to blocked, never again while it stays blocked.
    """


def send_health():
    hub.emit({"e": "hello"})
    while True:
        time.sleep(HEALTH_INTERVAL_SECONDS)
        with lock:
            stats = rejector.stats()
        hub.emit(dict(stats, e="health"))


def listen():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", RejectorPi.PORT))
    print(f"Listening for reject requests on port {RejectorPi.PORT}")

    while True:
        data, sender = sock.recvfrom(16)
        parsed = RejectorPi.parse_packet(data)
        if parsed is not None:
            flag, racn = parsed
            print(f"[REJECT RECEIVED] from {sender[0]}: flag={flag} racn={racn}")
        with lock:
            rejector.receive(data)


def main():
    global relay
    if EXIT_SENSOR_PIN is None or REJECT_RELAY_PIN is None:
        sys.exit("Set EXIT_SENSOR_PIN and REJECT_RELAY_PIN at the top of rj_main.py")

    relay = DigitalOutputDevice(
        REJECT_RELAY_PIN, active_high=RELAY_ACTIVE_HIGH, initial_value=False
    )
    exit_sensor = DigitalInputDevice(
        EXIT_SENSOR_PIN,
        pull_up=EXIT_SENSOR_PULL_UP,
        bounce_time=EXIT_SENSOR_BOUNCE_SECONDS,
    )
    exit_sensor.when_activated = on_exit

    threading.Thread(target=watch_usb_entry_sensor, daemon=True).start()
    threading.Thread(target=send_health, daemon=True).start()
    listen()


if __name__ == "__main__":
    main()
