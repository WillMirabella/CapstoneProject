"""Tracker tests, driven by events from the Rejector simulation.

Run from the HUB folder:  python3 -m unittest discover tests -v
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "Rejector", "simulation"))

from hub import db, reports  # noqa: E402
from hub.tracker import RunError, Tracker  # noqa: E402
from simulation import SimConfig, run_simulation  # noqa: E402

SIM_TO_HUB = {"telodyne": "c1", "keyence": "keyence", "elp": "c2"}


class FakeClock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        self.now += 0.01
        return self.now


def stamp(events, session="aaaa0001"):
    """Add the session/seq fields HubLink adds on the real Rj-Pi."""
    return [dict(e, session=session, seq=i) for i, e in enumerate(events)]


class TrackerTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect()
        self.clock = FakeClock()
        self.tracker = Tracker(self.conn, clock=self.clock)
        ship = self.tracker.add_shipment("Northline Can Co.", arrival_date="2026-11-12",
                                         po_number="PO-1", bill_of_lading="BOL-9")
        self.line_a = self.tracker.add_shipment_line(ship, "12OZ-STD", lot_code="L1")
        self.line_b = self.tracker.add_shipment_line(ship, "16OZ-TALL", lot_code="L2")

    def tearDown(self):
        self.conn.close()

    def replay(self, events, after_entry=None, drop=()):
        """Feed events; after_entry[acn]() runs right after that can enters."""
        after_entry = after_entry or {}
        for i, event in enumerate(events):
            if i in drop:
                continue
            self.tracker.handle(event)
            if event["e"] == "entry" and event["acn"] in after_entry:
                after_entry[event["acn"]]()

    def cans(self, run_id):
        return self.conn.execute(
            "SELECT * FROM can WHERE run_id = ? ORDER BY position", (run_id,)).fetchall()

    def run_status(self, run_id):
        return self.conn.execute(
            "SELECT status FROM run WHERE id = ?", (run_id,)).fetchone()["status"]


class ProductionRunTests(TrackerTestCase):
    def test_single_run_matches_simulation(self):
        sim = run_simulation(SimConfig(num_cans=500, seed=3))
        run = self.tracker.start_production_run(self.line_a, operator="Sam")
        self.replay(stamp(sim.hub_events), after_entry={500: self.tracker.stop_run})

        summary = reports.run_summary(self.conn, run)
        self.assertEqual(self.run_status(run), "complete")
        self.assertEqual(summary["entered"], 500)
        self.assertEqual(summary["exited"], 500)
        self.assertEqual(summary["on_belt"], 0)
        self.assertEqual(summary["rejected"], len(sim.rejected_ids))
        self.assertEqual(summary["passed"], 500 - len(sim.rejected_ids))
        self.assertEqual(summary["run"]["sku"], "12OZ-STD")
        rejected = {c["acn"] for c in self.cans(run) if c["rejected"]}
        self.assertEqual(rejected, sim.rejected_ids)
        self.assertEqual([c["position"] for c in self.cans(run)], list(range(1, 501)))

    def test_back_to_back_runs_overlap_on_belt(self):
        sim = run_simulation(SimConfig(num_cans=400, seed=5))
        runs = {}

        def switch_runs():
            self.tracker.stop_run()
            runs["b"] = self.tracker.start_production_run(self.line_b)

        runs["a"] = self.tracker.start_production_run(self.line_a)
        # Run A is still finishing (cans on the belt) when run B starts.
        self.replay(stamp(sim.hub_events), after_entry={
            150: switch_runs, 400: self.tracker.stop_run})

        a, b = self.cans(runs["a"]), self.cans(runs["b"])
        self.assertEqual([c["acn"] for c in a], list(range(1, 151)))
        self.assertEqual([c["acn"] for c in b], list(range(151, 401)))
        self.assertEqual([c["position"] for c in b], list(range(1, 251)))
        self.assertEqual({c["acn"] for c in a if c["rejected"]},
                         {i for i in sim.rejected_ids if i <= 150})
        self.assertEqual(self.run_status(runs["a"]), "complete")
        self.assertEqual(self.run_status(runs["b"]), "complete")
        # Run A's cans that exited after run B started still count for run A.
        summary_a = reports.run_summary(self.conn, runs["a"])
        self.assertEqual(summary_a["passed"], 150 - len({i for i in sim.rejected_ids if i <= 150}))

    def test_run_stays_finishing_until_last_can_exits(self):
        sim = run_simulation(SimConfig(num_cans=50))
        events = stamp(sim.hub_events)
        run = self.tracker.start_production_run(self.line_a)
        last_exit = max(i for i, e in enumerate(events) if e["e"] == "exit")
        self.replay(events[:last_exit], after_entry={50: self.tracker.stop_run})
        self.assertEqual(self.run_status(run), "finishing")
        summary = reports.run_summary(self.conn, run)
        self.assertEqual(summary["on_belt"], 1)
        before = summary["passed"] + summary["rejected"]
        self.tracker.handle(events[last_exit])
        self.assertEqual(self.run_status(run), "complete")
        # The last can still counts toward the run after Stop was pressed.
        summary = reports.run_summary(self.conn, run)
        self.assertEqual(summary["passed"] + summary["rejected"], before + 1)
        self.assertEqual(summary["passed"] + summary["rejected"], 50)

    def test_cans_with_no_active_run_are_unassigned(self):
        sim = run_simulation(SimConfig(num_cans=20))
        self.replay(stamp(sim.hub_events))
        count = self.conn.execute(
            "SELECT count(*) FROM can WHERE run_id IS NULL").fetchone()[0]
        self.assertEqual(count, 20)

    def test_one_active_run_at_a_time(self):
        self.tracker.start_production_run(self.line_a)
        with self.assertRaises(RunError):
            self.tracker.start_production_run(self.line_b)

    def test_stop_with_no_run(self):
        with self.assertRaises(RunError):
            self.tracker.stop_run()

    def test_empty_run_completes_on_stop(self):
        run = self.tracker.start_production_run(self.line_a)
        self.tracker.stop_run()
        self.assertEqual(self.run_status(run), "complete")

    def test_production_run_needs_real_line(self):
        with self.assertRaises(RunError):
            self.tracker.start_production_run(999)


class LostDataTests(TrackerTestCase):
    def test_lost_entry_is_recovered_in_place(self):
        sim = run_simulation(SimConfig(num_cans=100, forced_defects={50: {"keyence"}}))
        events = stamp(sim.hub_events)
        drop = {i for i, e in enumerate(events) if e["e"] == "entry" and e["acn"] == 50}
        run = self.tracker.start_production_run(self.line_a)
        self.replay(events, drop=drop, after_entry={100: self.tracker.stop_run})

        cans = self.cans(run)
        self.assertEqual([c["acn"] for c in cans], list(range(1, 101)))
        self.assertEqual([c["position"] for c in cans], list(range(1, 101)))
        can50 = cans[49]
        self.assertEqual((can50["recovered"], can50["rejected"], can50["keyence"]),
                         (1, 1, 1))
        self.assertIsNone(can50["entered_at"])
        session = self.conn.execute("SELECT missed FROM rj_session").fetchone()
        self.assertEqual(session["missed"], 1)

    def test_lost_entry_of_last_can_recovered_from_its_exit(self):
        sim = run_simulation(SimConfig(num_cans=30))
        events = stamp(sim.hub_events)
        drop = {i for i, e in enumerate(events) if e["e"] == "entry" and e["acn"] == 30}
        run = self.tracker.start_production_run(self.line_a)
        self.replay(events, drop=drop, after_entry={29: self.tracker.stop_run})
        # Stop was pressed after can 29 entered, so the recovered can 30 has no
        # active run to join; it is kept, but unassigned.
        self.assertEqual(len(self.cans(run)), 29)
        orphan = self.conn.execute("SELECT * FROM can WHERE acn = 30").fetchone()
        self.assertIsNone(orphan["run_id"])
        self.assertEqual(orphan["recovered"], 1)
        self.assertEqual(self.run_status(run), "complete")

    def test_lost_exit_expires_to_incomplete_then_recovers(self):
        sim = run_simulation(SimConfig(num_cans=40))
        events = stamp(sim.hub_events)
        drop = {i for i, e in enumerate(events) if e["e"] == "exit" and e["acn"] == 40}
        run = self.tracker.start_production_run(self.line_a)
        self.replay(events, drop=drop, after_entry={40: self.tracker.stop_run})
        self.assertEqual(self.run_status(run), "finishing")
        self.clock.now += 120
        self.tracker.expire_stale_runs(timeout=60)
        self.assertEqual(self.run_status(run), "incomplete")
        # The exit turns up late after all (e.g. replayed from a log).
        self.tracker.handle(events[min(drop)])
        self.assertEqual(self.run_status(run), "complete")

    def test_rjpi_restart_marks_on_belt_cans_lost(self):
        first = stamp(run_simulation(SimConfig(num_cans=60)).hub_events, "sess-one")
        second = stamp(run_simulation(SimConfig(num_cans=40)).hub_events, "sess-two")
        cut = next(i for i, e in enumerate(first) if e["e"] == "exit" and e["acn"] == 30)
        run = self.tracker.start_production_run(self.line_a)
        self.replay(first[:cut])  # power cut: cans 30+ on the belt never exit
        entered_before_cut = sum(e["e"] == "entry" for e in first[:cut])
        self.replay(second, after_entry={40: self.tracker.stop_run})

        summary = reports.run_summary(self.conn, run)
        self.assertEqual(summary["entered"], entered_before_cut + 40)
        self.assertEqual(summary["lost"], entered_before_cut - 29)
        self.assertEqual(summary["on_belt"], 0)
        self.assertEqual(self.run_status(run), "incomplete")
        # ACNs restart in the new session without clashing with the old ones.
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM can WHERE acn = 1").fetchone()[0], 2)

    def test_bad_and_duplicate_datagrams(self):
        run = self.tracker.start_production_run(self.line_a)
        self.tracker.handle_datagram(b"not json")
        self.tracker.handle_datagram(b"[1, 2]")
        self.tracker.handle_datagram(b'{"e": "entry", "acn": 1}')  # no session
        entry = json.dumps({"e": "entry", "acn": 1, "session": "s", "seq": 0}).encode()
        self.tracker.handle_datagram(entry)
        self.tracker.handle_datagram(entry)  # duplicate
        self.assertEqual(len(self.cans(run)), 1)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM event_log").fetchone()[0], 5)
        self.assertEqual(self.conn.execute(
            "SELECT missed FROM rj_session").fetchone()[0], 0)

    def test_health_is_stored(self):
        self.tracker.handle({"e": "health", "session": "s", "seq": 0, "t": 5.0,
                             "cans_on_belt": 3, "late_packets": 2})
        health = json.loads(self.conn.execute(
            "SELECT health FROM rj_session").fetchone()[0])
        self.assertEqual(health["late_packets"], 2)


class ValidationRunTests(TrackerTestCase):
    SEEDS = {
        5: {"expected": ["c1"], "defect": "side dent"},
        20: {"expected": ["keyence"], "defect": "bottom scratch"},
        41: {"expected": ["c2", "c1"], "defect": "flange damage"},
        60: {"expected": [], "defect": "any"},
    }

    def run_validation(self, forced, planned=80, actual=80):
        sim = run_simulation(SimConfig(num_cans=actual, forced_defects={
            pos: {name for name, hub in SIM_TO_HUB.items() if hub in cams}
            for pos, cams in forced.items()}))
        run = self.tracker.start_validation_run(self.SEEDS, planned_cans=planned)
        self.replay(stamp(sim.hub_events), after_entry={actual: self.tracker.stop_run})
        return reports.validation_report(self.conn, run)

    def test_scores_each_camera(self):
        report = self.run_validation({
            5: {"c1"},             # caught by the expected camera
            20: set(),             # Keyence missed it
            41: {"c1"},            # c1 caught it, c2 missed it
            60: {"c2"},            # any camera counts
            70: {"keyence"},       # flag on an ordinary can
        })
        self.assertEqual(report["status"], "complete")
        results = {s["position"]: s["result"] for s in report["seeds"]}
        self.assertEqual(results, {5: "caught", 20: "missed", 41: "caught", 60: "caught"})
        self.assertEqual(report["caught"], 3)
        self.assertEqual(report["false_rejects"], 1)
        cams = report["cameras"]
        self.assertEqual((cams["c1"]["expected"], cams["c1"]["detected"]), (2, 2))
        self.assertEqual((cams["keyence"]["expected"], cams["keyence"]["detected"]), (1, 0))
        self.assertEqual((cams["c2"]["expected"], cams["c2"]["detected"]), (1, 0))
        self.assertEqual(cams["keyence"]["false_flags"], 1)
        self.assertEqual(cams["c2"]["extra"], 0)  # pos 60 expects "any camera"
        self.assertEqual(report["warnings"], [])

    def test_count_mismatch_warns_and_marks_unrun_seeds(self):
        report = self.run_validation({5: {"c1"}}, planned=80, actual=50)
        self.assertEqual({s["position"]: s["result"] for s in report["seeds"]},
                         {5: "caught", 20: "missed", 41: "missed", 60: "not run"})
        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn("50 cans entered but 80 were planned", report["warnings"][0])

    def test_seeds_are_relative_to_run_start(self):
        # 100 production cans first, so the validation run's ACNs start at 101.
        sim = run_simulation(SimConfig(num_cans=180, forced_defects={105: {"telodyne"}}))
        self.tracker.start_production_run(self.line_a)

        def start_validation():
            self.tracker.stop_run()
            self.tracker.start_validation_run({5: {"expected": ["c1"]}}, planned_cans=80)

        self.replay(stamp(sim.hub_events), after_entry={
            100: start_validation, 180: self.tracker.stop_run})
        run = self.tracker.conn.execute(
            "SELECT id FROM run WHERE kind = 'validation'").fetchone()["id"]
        report = reports.validation_report(self.conn, run)
        self.assertEqual(report["seeds"][0]["acn"], 105)
        self.assertEqual(report["seeds"][0]["result"], "caught")

    def test_invalid_seeds(self):
        with self.assertRaises(RunError):
            self.tracker.start_validation_run({}, planned_cans=10)
        with self.assertRaises(RunError):
            self.tracker.start_validation_run({11: {}}, planned_cans=10)
        with self.assertRaises(RunError):
            self.tracker.start_validation_run({1: {"expected": ["cam9"]}}, planned_cans=10)
        with self.assertRaises(RunError):
            self.tracker.start_validation_run({1: {}}, planned_cans=0)
        self.assertIsNone(self.tracker.active_run())


if __name__ == "__main__":
    unittest.main()
