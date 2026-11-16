"""Fill a HUB database with made-up shipments and a history of past runs.

    python3 tools/demo_data.py --db hub.db              # shipments + 30 days of runs
    python3 tools/demo_data.py --db hub.db --days 0     # shipments only

Run it while the HUB server is stopped. The suppliers and SKUs are fictional.
The history has two stories for the dashboard to show:
  - one shipment from Harbor Container arrived side-damaged: Telodyne and ELP
    reject far more of it than usual
  - the Keyence slowly starts over-flagging over the last ten days
"""

import argparse
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from fakeline import make_events  # noqa: E402
from hub import db  # noqa: E402
from hub.tracker import Tracker  # noqa: E402

DAY = 86400.0
SUPPLIERS = ["Northline Can Co.", "Summit Metal Packaging", "Harbor Container"]
SKUS = ["12OZ-STD-202", "16OZ-TALL-202", "12OZ-SLEEK-202"]
OPERATORS = ["A. Rivera", "J. Chen", "M. Okafor"]
BASE_RATE = 0.012


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def date(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def build(conn, days, rng):
    today = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    clock = Clock(today - (days + 7) * DAY)
    tracker = Tracker(conn, clock=clock)

    # A shipment every few days, with one or two SKUs each.
    lines, bad_line = [], None
    arrival = today - (days + 6) * DAY
    n = 0
    while arrival <= today:
        n += 1
        supplier = SUPPLIERS[n % len(SUPPLIERS)]
        clock.now = arrival + 6 * 3600
        ship = tracker.add_shipment(
            supplier, arrival_date=date(arrival), po_number=f"PO-{24000 + n}",
            bill_of_lading=f"BOL-{rng.randrange(10**6):06d}",
            carrier=rng.choice(["Midstate Freight", "Lakeshore Logistics"]),
            tracking_number=f"TRK{rng.randrange(10**9):09d}")
        for sku in rng.sample(SKUS, rng.choice((1, 2))):
            line = tracker.add_shipment_line(
                ship, sku, lot_code=f"L{rng.randrange(10**5):05d}",
                quantity=rng.choice((20000, 40000, 60000)))
            lines.append((arrival, line))
            if supplier == "Harbor Container" and bad_line is None and \
                    arrival > today - days * 0.6 * DAY:
                bad_line = line
        arrival += rng.choice((2, 3, 4)) * DAY

    for day in range(days, 0, -1):
        start_of_day = today - day * DAY
        t = start_of_day + 7 * 3600
        session = os.urandom(4).hex()  # the Rj-Pi is powered up each morning
        seq = 0
        acn_base = 0  # ACNs keep counting across runs until the Rj-Pi restarts
        available = [line for arrived, line in lines if arrived <= start_of_day]
        if not available:
            continue
        drift = max(0.0, (10 - day) / 10) * 0.05  # Keyence over-flagging
        runs = rng.choice((1, 2, 3))
        if day % 7 == 3:
            runs += 1  # weekly validation run
        for r in range(runs):
            clock.now = t
            validation = day % 7 == 3 and r == 0
            if validation:
                planned = 80
                positions = sorted(rng.sample(range(1, planned + 1), 6))
                kinds = [("c1", "side dent"), ("keyence", "bottom scratch"),
                         ("c2", "flange damage")]
                seeds, sim_seeds = {}, {}
                for i, pos in enumerate(positions):
                    cam, defect = kinds[i % 3]
                    seeds[pos] = {"expected": [cam], "defect": defect}
                    sim_cam = {"c1": "telodyne", "keyence": "keyence", "c2": "elp"}[cam]
                    # Keyence misses about one seeded can in five.
                    caught = not (cam == "keyence" and rng.random() < 0.2)
                    sim_seeds[pos] = {sim_cam} if caught else set()
                tracker.start_validation_run(seeds, planned_cans=planned,
                                             operator=rng.choice(OPERATORS))
                events = make_events(planned, dict.fromkeys(
                    ("telodyne", "keyence", "elp"), 0.0), sim_seeds,
                    cans_per_second=3, seed=rng.randrange(2**32))
            else:
                line = rng.choice(available[-4:])
                rates = {"telodyne": BASE_RATE, "keyence": BASE_RATE + drift,
                         "elp": BASE_RATE}
                if line == bad_line:
                    rates["telodyne"] = rates["elp"] = 0.11
                tracker.start_production_run(line, operator=rng.choice(OPERATORS))
                events = make_events(rng.randrange(400, 1600), rates,
                                     cans_per_second=3, seed=rng.randrange(2**32))
            last_entry = max(i for i, e in enumerate(events) if e["e"] == "entry")
            for i, event in enumerate(events):
                clock.now = t + event["t"]
                event = dict(event, session=session, seq=seq)
                if "acn" in event:
                    event["acn"] += acn_base
                tracker.handle(event)
                seq += 1
                if i == last_entry:
                    tracker.stop_run()
            acn_base += sum(e["e"] == "entry" for e in events)
            t = clock.now + rng.randrange(10, 40) * 60
        print(f"\r{date(start_of_day)}: {runs} run(s)", end="", flush=True)
    print()


def main():
    parser = argparse.ArgumentParser(description="Fill a HUB database with demo data")
    parser.add_argument("--db", default="hub.db")
    parser.add_argument("--days", type=int, default=30, help="days of past runs")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if os.path.exists(args.db):
        sys.exit(f"{args.db} already exists; delete it or pick another --db")

    # Build in memory (fast), then write the finished database to disk once.
    mem = db.connect(":memory:")
    build(mem, args.days, random.Random(args.seed))
    out = db.connect(args.db)
    mem.backup(out)
    out.close()
    counts = mem.execute(
        "SELECT (SELECT count(*) FROM shipment), (SELECT count(*) FROM run),"
        " (SELECT count(*) FROM can)").fetchone()
    print(f"Wrote {args.db}: {counts[0]} shipments, {counts[1]} runs, {counts[2]} cans")


if __name__ == "__main__":
    main()
