"""Fake Rj-Pi: feeds simulated cans to the HUB over UDP, in real time.

The HUB can't tell this from the real Rj-Pi: it sends the same JSON datagrams
HubLink sends (Rejector/notes.md). Start the HUB first, press Start Run on the
operator screen, then run this. When every can has been sent it keeps sending
health events, like an idle Rj-Pi, until you press Ctrl-C.

Examples (from the HUB folder):
    python3 tools/replay.py                              # 2000 cans, 3 per second
    python3 tools/replay.py --cans 300 --rate 10 --speed 2
    python3 tools/replay.py --false-flags telodyne=0.12  # camera 1 over-flags
    python3 tools/replay.py --detect keyence=0.6         # Keyence misses real defects
    python3 tools/replay.py --cans 80 --seed-defects 5:c1,20:keyence,41:
    python3 tools/replay.py --drop-rate 0.01 --restart-at 400
"""

import argparse
import json
import os
import random
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fakeline import make_events, parse_rates, parse_seeds  # noqa: E402

HEALTH_INTERVAL = 5.0


class FakeRjPi:
    def __init__(self, addr, drop_rate, rng):
        self.addr = addr
        self.drop_rate = drop_rate
        self.rng = rng
        self.session = os.urandom(4).hex()
        self.seq = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.dropped = 0
        self.stats = dict(entry_acn=0, exit_acn=0, cans_on_belt=0, packets_received=0,
                          invalid_packets=0, late_packets=0, unexpected_exits=0,
                          overflows=0, rejections=0)

    def send(self, event):
        e, s = event["e"], self.stats
        if e == "entry":
            s["entry_acn"] = event["acn"]
        elif e == "exit":
            s["exit_acn"] = event["acn"]
            s["rejections"] += bool(event["rejected"])
        elif e in ("flag", "late", "invalid"):
            s["packets_received"] += 1
            if e != "flag":
                s[f"{e}_packets"] += 1
        s["cans_on_belt"] = s["entry_acn"] - s["exit_acn"]

        packet = dict(event, session=self.session, seq=self.seq)
        self.seq += 1
        if e != "hello" and self.rng.random() < self.drop_rate:
            self.dropped += 1
            return
        try:
            self.sock.sendto(json_bytes(packet), self.addr)
        except OSError as error:
            print(f"\nsend failed: {error}")

    def health(self, t):
        self.send(dict(self.stats, e="health", t=round(t, 4)))


def json_bytes(obj):
    return json.dumps(obj, separators=(",", ":")).encode()


def play(rjpi, events, speed, label):
    """Send events at their simulated times (divided by speed)."""
    start = time.monotonic()
    next_health = 0.0
    entered = rejected = 0
    shown = 0.0
    rjpi.send({"e": "hello", "t": 0.0})
    for event in events:
        while next_health <= event["t"]:
            wait_until(start + next_health / speed)
            rjpi.health(next_health)
            next_health += HEALTH_INTERVAL
        wait_until(start + event["t"] / speed)
        rjpi.send(event)
        if event["e"] == "entry":
            entered += 1
        elif event["e"] == "exit" and event["rejected"]:
            rejected += 1
        if time.monotonic() - shown >= 0.2:
            shown = time.monotonic()
            progress(label, rjpi, entered, rejected)
    progress(label, rjpi, entered, rejected)
    print()
    return start, next_health


def progress(label, rjpi, entered, rejected):
    end = "" if sys.stdout.isatty() else "\n"
    print(f"\r{label} session {rjpi.session}: {entered} cans entered, {rejected} rejected, "
          f"{rjpi.dropped} datagrams dropped ", end=end, flush=True)


def wait_until(deadline):
    delay = deadline - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples" + __doc__.split("Examples", 1)[1])
    parser.add_argument("--host", default="127.0.0.1", help="HUB address")
    parser.add_argument("--port", type=int, default=5001, help="HUB UDP port")
    parser.add_argument("--cans", type=int, default=2000)
    parser.add_argument("--rate", type=float, default=3.0, help="cans per second")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="time multiplier: 2 = twice as fast as real time")
    parser.add_argument("--defect-rate", type=float, default=None,
                        help="fraction of cans with a real defect (default 0.03; "
                             "0 when --seed-defects is given)")
    parser.add_argument("--detect", default="0.9",
                        help="chance each camera catches a real defect: one number for all "
                             "three, or per camera, e.g. keyence=0.6 (default 0.9)")
    parser.add_argument("--false-flags", "--defects", dest="false_flags", default=None,
                        help="chance a camera flags a good can on its own: one number, or "
                             "per camera, e.g. telodyne=0.1 (default 0.002; "
                             "0 when --seed-defects is given)")
    parser.add_argument("--seed-defects", default="",
                        help="known defects for a validation run: position:cameras "
                             "that detect it, e.g. 5:c1,20:keyence,41:c1+c2,60: "
                             "(empty = every camera misses it)")
    parser.add_argument("--drop-rate", type=float, default=0.0,
                        help="fraction of datagrams to lose on the way to the HUB")
    parser.add_argument("--restart-at", type=int, default=None,
                        help="simulate the Rj-Pi rebooting after this many cans")
    parser.add_argument("--seed", type=int, default=None, help="random seed")
    parser.add_argument("--exit-when-done", action="store_true",
                        help="stop instead of idling with health events")
    args = parser.parse_args(argv)

    seeds = parse_seeds(args.seed_defects)
    # A validation run should only flag the seeded cans unless asked otherwise.
    defect_rate = args.defect_rate if args.defect_rate is not None else (0.0 if seeds else 0.03)
    false_flags = args.false_flags if args.false_flags is not None else ("0" if seeds else "")
    rates = parse_rates(false_flags, 0.002)
    detect = parse_rates(args.detect, 0.9)
    rng = random.Random(args.seed)
    addr = (args.host, args.port)

    segments = [args.cans]
    if args.restart_at and 0 < args.restart_at < args.cans:
        segments = [args.restart_at, args.cans - args.restart_at]

    print(f"Fake Rj-Pi -> {addr[0]}:{addr[1]}  cans={args.cans} rate={args.rate}/s "
          f"speed={args.speed}x")
    print(f"  real defects: {defect_rate:.1%} of cans, caught by "
          + ", ".join(f"{c} {p:.0%}" for c, p in detect.items()))
    print("  false flags: " + ", ".join(f"{c} {p:.1%}" for c, p in rates.items()))
    try:
        for i, num_cans in enumerate(segments):
            events = make_events(num_cans, rates, seeds if i == 0 else None,
                                 cans_per_second=args.rate, seed=rng.randrange(2**32),
                                 defect_rate=defect_rate, detect=detect)
            if i < len(segments) - 1:
                # Power cut right after the last can of this segment enters:
                # cans still on the belt never report their exit.
                last_entry = max(j for j, e in enumerate(events) if e["e"] == "entry")
                events = events[:last_entry + 1]
            rjpi = FakeRjPi(addr, args.drop_rate, rng)
            start, next_health = play(rjpi, events, args.speed, f"[{i + 1}/{len(segments)}]")
            if i < len(segments) - 1:
                print("Rj-Pi rebooting (5 s)...")
                time.sleep(5 / args.speed)
        if args.exit_when_done:
            return 0
        print("All cans sent. Idling like a real Rj-Pi (health every 5 s). Ctrl-C to stop.")
        while True:
            wait_until(start + next_health / args.speed)
            rjpi.health(next_health)
            next_health += HEALTH_INTERVAL
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
