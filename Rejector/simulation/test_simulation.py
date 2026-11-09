"""Tests for the rejector simulation. Run from this directory:

    python -m unittest test_simulation -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rejector_pi
from elp import ElpCamera
from keyence import KeyenceCamera
from rejector_pi import RejectItem, RejectorPi
from simulation import Can, EventScheduler, SimConfig, Switch, run_simulation
from telodyne import TelodyneCamera

ALL_CAMERAS = {TelodyneCamera.NAME, KeyenceCamera.NAME, ElpCamera.NAME}


class FakeSwitch:
    def __init__(self):
        self.sent = []

    def send(self, port, data):
        self.sent.append((port, data))


class PacketFormatTests(unittest.TestCase):
    def send_one(self, camera_cls):
        scheduler = EventScheduler()
        switch = FakeSwitch()
        camera = camera_cls(scheduler, switch, RejectorPi.PORT, processing_time=0.1)
        camera.on_can(Can(1, frozenset({camera_cls.NAME})))
        scheduler.run()
        return switch.sent

    def test_telodyne_sends_system_number_and_acn(self):
        self.assertEqual(self.send_one(TelodyneCamera), [(RejectorPi.PORT, b"\x01\x01")])

    def test_elp_sends_system_number_and_acn(self):
        self.assertEqual(self.send_one(ElpCamera), [(RejectorPi.PORT, b"\x02\x01")])

    def test_keyence_sends_single_byte(self):
        self.assertEqual(self.send_one(KeyenceCamera), [(RejectorPi.PORT, b"\x01")])

    def test_camera_system_numbers_match_rejector(self):
        self.assertEqual(TelodyneCamera.SYSTEM_NUMBER, rejector_pi.TELODYNE_SYSTEM_NUMBER)
        self.assertEqual(ElpCamera.SYSTEM_NUMBER, rejector_pi.ELP_SYSTEM_NUMBER)

    def test_good_can_sends_nothing(self):
        scheduler = EventScheduler()
        switch = FakeSwitch()
        camera = TelodyneCamera(scheduler, switch, RejectorPi.PORT, 0.1)
        camera.on_can(Can(1, frozenset()))
        scheduler.run()
        self.assertEqual(switch.sent, [])

    def test_camera_acn_wraps_at_256(self):
        camera = KeyenceCamera(EventScheduler(), FakeSwitch(), RejectorPi.PORT, 0.1)
        acns = []
        for can_id in range(1, 260):
            camera.on_can(Can(can_id, frozenset()))
            acns.append(camera.acn)
        self.assertEqual(acns[254:259], [255, 0, 1, 2, 3])

    def test_switch_rejects_non_bytes(self):
        import random
        switch = Switch(EventScheduler(), random.Random(0), 0.0, 0.0, 0.0)
        with self.assertRaises(TypeError):
            switch.send(RejectorPi.PORT, {1: 5})


class RejectorPiTests(unittest.TestCase):
    def test_parse_packets(self):
        self.assertEqual(RejectorPi.parse_packet(b"\x07"), ("keyence", 7))
        self.assertEqual(RejectorPi.parse_packet(b"\x01\x07"), ("c1", 7))
        self.assertEqual(RejectorPi.parse_packet(b"\x02\x07"), ("c2", 7))
        self.assertIsNone(RejectorPi.parse_packet(b""))
        self.assertIsNone(RejectorPi.parse_packet(b"\x09\x07"))  # unknown system
        self.assertIsNone(RejectorPi.parse_packet(b"\x01\x02\x03"))

    def test_reject_item(self):
        self.assertFalse(RejectItem(5).should_reject)
        self.assertTrue(RejectItem(5, keyence=True).should_reject)

    def test_entry_and_exit_acns_wrap_together(self):
        rj = RejectorPi()
        entries, exits = [], []
        for _ in range(600):
            entries.append(rj.on_entry_sensor())
            exits.append(rj.on_exit_sensor()[0])
        self.assertEqual(entries, exits)
        self.assertEqual(entries[:3], [1, 2, 3])
        self.assertEqual(entries[254:258], [255, 0, 1, 2])

    def test_rejects_flagged_can_only(self):
        rj = RejectorPi()
        for _ in range(3):
            rj.on_entry_sensor()
        rj.receive(b"\x02")  # Keyence flags ACN 2
        self.assertEqual([rj.on_exit_sensor() for _ in range(3)],
                         [(1, False), (2, True), (3, False)])

    def test_multiple_flags_one_rejection(self):
        rj = RejectorPi()
        rj.on_entry_sensor()
        for packet in (b"\x01\x01", b"\x01", b"\x02\x01"):
            rj.receive(packet)
        self.assertEqual(rj.on_exit_sensor(), (1, True))
        self.assertEqual(rj.rejection_log, [RejectItem(1, c1=True, keyence=True, c2=True)])

    def test_late_packet_is_dropped_not_stored(self):
        rj = RejectorPi()
        rj.on_entry_sensor()
        rj.on_exit_sensor()
        rj.receive(b"\x01\x01")  # ACN 1 already exited
        self.assertEqual(rj.late_packets, 1)
        self.assertEqual(rj.reject_items, {})

    def test_invalid_packet_counted(self):
        rj = RejectorPi()
        rj.on_entry_sensor()
        rj.receive(b"\x05\x01")
        self.assertEqual(rj.invalid_packets, 1)
        self.assertEqual(rj.on_exit_sensor(), (1, False))

    def test_unexpected_exit_does_not_advance_acn(self):
        rj = RejectorPi()
        self.assertEqual(rj.on_exit_sensor(), (None, False))
        self.assertEqual(rj.unexpected_exits, 1)
        self.assertEqual(rj.on_entry_sensor(), 1)
        self.assertEqual(rj.on_exit_sensor(), (1, False))

    def test_belt_overflow_raises(self):
        rj = RejectorPi()
        for _ in range(255):
            rj.on_entry_sensor()
        with self.assertRaises(OverflowError):
            rj.on_entry_sensor()


class SimulationTests(unittest.TestCase):
    def assertPassed(self, result):
        self.assertEqual(result.false_rejects, set())
        self.assertEqual(result.missed_rejects, set())
        self.assertEqual(result.acn_mismatches, [])
        self.assertEqual(result.rejector.cans_on_belt, 0)
        self.assertEqual(result.rejector.reject_items, {})

    def test_default_run_passes(self):
        result = run_simulation(SimConfig())
        self.assertGreater(len(result.defective_ids), 0)
        self.assertPassed(result)

    def test_many_seeds_pass(self):
        for seed in range(10):
            with self.subTest(seed=seed):
                self.assertPassed(run_simulation(SimConfig(num_cans=2000, seed=seed)))

    def test_no_defects(self):
        result = run_simulation(SimConfig(defect_rate=0.0))
        self.assertEqual(result.rejected_ids, set())
        self.assertEqual(result.switch.packets_sent, 0)
        self.assertPassed(result)

    def test_every_can_defective(self):
        result = run_simulation(SimConfig(num_cans=700, defect_rate=1.0))
        self.assertEqual(result.rejected_ids, set(range(1, 701)))
        self.assertPassed(result)

    def test_defects_around_acn_wraparound(self):
        boundary = {255, 256, 257, 511, 512, 513, 768}
        result = run_simulation(SimConfig(
            num_cans=800, forced_defects={i: {KeyenceCamera.NAME} for i in boundary},
        ))
        self.assertEqual(result.rejected_ids, boundary)
        self.assertPassed(result)

    def test_each_camera_alone_triggers_rejection(self):
        for name in ALL_CAMERAS:
            with self.subTest(camera=name):
                result = run_simulation(SimConfig(num_cans=50, forced_defects={10: {name}}))
                self.assertEqual(result.rejected_ids, {10})

    def test_all_cameras_flag_same_can(self):
        result = run_simulation(SimConfig(num_cans=50, forced_defects={10: ALL_CAMERAS}))
        self.assertEqual(result.rejected_ids, {10})
        self.assertEqual(result.rejector.rejection_log,
                         [RejectItem(10, c1=True, keyence=True, c2=True)])

    def test_densely_packed_belt(self):
        # 0.0101 m spacing -> 248 cans on the belt, just under the 1-byte limit.
        self.assertPassed(run_simulation(SimConfig(
            num_cans=1500, can_spacing=0.0101, spacing_jitter=0.0,
        )))

    def test_belt_too_dense_for_one_byte_acn(self):
        with self.assertRaises(ValueError):
            run_simulation(SimConfig(can_spacing=0.005))

    def test_slow_camera_misses_but_never_false_rejects(self):
        # ELP sits 2 s from the exit sensor; 3 s processing is always too late.
        result = run_simulation(SimConfig(
            num_cans=600, elp_processing=3.0,
            forced_defects={5: {ElpCamera.NAME}, 6: {KeyenceCamera.NAME},
                            300: {ElpCamera.NAME}, 301: {ElpCamera.NAME, TelodyneCamera.NAME}},
        ))
        self.assertEqual(result.rejector.late_packets, 3)
        self.assertEqual(result.rejected_ids, {6, 301})
        self.assertEqual(result.missed_rejects, {5, 300})
        self.assertEqual(result.rejector.reject_items, {})

    def test_dropped_packets_are_missed_not_false_rejects(self):
        result = run_simulation(SimConfig(drop_rate=1.0))
        self.assertEqual(result.rejected_ids, set())
        self.assertEqual(result.missed_rejects, result.defective_ids)

    def test_deterministic_with_seed(self):
        a = run_simulation(SimConfig(seed=42))
        b = run_simulation(SimConfig(seed=42))
        self.assertEqual(a.rejected_ids, b.rejected_ids)
        self.assertEqual(a.defective_ids, b.defective_ids)


if __name__ == "__main__":
    unittest.main()
