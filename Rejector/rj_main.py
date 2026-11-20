"""Rj-Pi main program: connects the hardware and network to RejectorPi.

    USB entry sensor -> on_entry()    (PCsensor trigger, read as keyboard events)
    GPIO exit sensor -> on_exit()     -> relay pulse if the can was flagged
    UDP port 5000    -> listen()      -> reject requests from the cameras
    every event      -> HubLink       -> UDP JSON to the HUB (fire-and-forget)

Keep this file in the same folder as rejector_pi.py and run: python3 rj_main.py
Stop rj_listener.py first, because both programs use port 5000.
"""

import fcntl
import glob
import json
import os
import socket
import struct
import sys
import threading
import time

from gpiozero import DigitalInputDevice, DigitalOutputDevice

from rejector_pi import RejectorPi

# BCM GPIO numbers, not physical pin positions on the header.
EXIT_SENSOR_PIN = 17  # GPIO17, physical pin 11
REJECT_RELAY_PIN = 27  # GPIO27, physical pin 13

# USB entry sensor (PCsensor trigger, shows up as a keyboard).
ENTRY_SENSOR_GLOB = "/dev/input/by-id/*PCsensor*event-kbd"

# TODO: confirm against the actual exit sensor and relay module.
EXIT_SENSOR_PULL_UP = False  # False: signal goes high at a can. True: goes low.
RELAY_ACTIVE_HIGH = True  # Many relay modules are active-low; set False for those.

# TODO: tune on the real belt.
ENTRY_SENSOR_BOUNCE_SECONDS = 0.05  # ignore beam flicker shorter than this
EXIT_SENSOR_BOUNCE_SECONDS = 0.01  # ignore repeat signals closer together than this
REJECT_DELAY_SECONDS = 0.0  # wait between the exit sensor and the relay firing
RELAY_PULSE_SECONDS = 0.1  # how long the relay stays on

# Linux input_event layout: time_sec, time_usec, type, code, value
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 1
KEY_DOWN = 1  # beam blocked
KEY_UP = 0  # beam cleared (2 = auto-repeat while blocked, ignored)
EVIOCGRAB = 0x40044590  # take the device so keystrokes don't reach the console

# HUB that records runs for the dashboard. If the HUB is off or unreachable,
# events are dropped and rejecting carries on, so leaving this set is safe.
# None just stops sending entirely (e.g. on a network with no HUB at all).
HUB_ADDR = ("192.168.50.50", 5001)  # TODO: set to the HUB's IP address
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
            packet = dict(
                event,
                session=self.session,
                seq=self.seq,
                t=round(time.monotonic() - self.started, 4),
            )
            self.seq += 1
        try:
            self.sock.sendto(
                json.dumps(packet, separators=(",", ":")).encode(), self.addr
            )
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


def find_entry_sensor():
    matches = glob.glob(ENTRY_SENSOR_GLOB)
    if not matches:
        sys.exit(f"USB entry sensor not found ({ENTRY_SENSOR_GLOB}). Is it plugged in?")
    return matches[0]


def watch_usb_entry_sensor(path):
    """Read the USB entry sensor and call on_entry() once per can."""
    last_release = 0.0
    with open(path, "rb") as f:
        fcntl.ioctl(f, EVIOCGRAB, 1)
        while True:
            _, _, etype, _, value = struct.unpack(EVENT_FORMAT, f.read(EVENT_SIZE))
            if etype != EV_KEY:
                continue
            if value == KEY_DOWN:
                if time.monotonic() - last_release >= ENTRY_SENSOR_BOUNCE_SECONDS:
                    on_entry()
            elif value == KEY_UP:
                last_release = time.monotonic()


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

    entry_path = find_entry_sensor()
    print("Entry sensor:", entry_path)

    relay = DigitalOutputDevice(
        REJECT_RELAY_PIN, active_high=RELAY_ACTIVE_HIGH, initial_value=False
    )
    exit_sensor = DigitalInputDevice(
        EXIT_SENSOR_PIN,
        pull_up=EXIT_SENSOR_PULL_UP,
        bounce_time=EXIT_SENSOR_BOUNCE_SECONDS,
    )
    exit_sensor.when_activated = on_exit

    threading.Thread(
        target=watch_usb_entry_sensor, args=(entry_path,), daemon=True
    ).start()
    threading.Thread(target=send_health, daemon=True).start()
    listen()


if __name__ == "__main__":
    main()
