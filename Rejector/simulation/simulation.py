"""Rejector system simulation (No-Star mode: every AS sends 1-byte ACNs).

Belt layout (positions in meters from the entry sensor):

    entry sensor -> Telodyne (c1) -> Keyence -> ELP (c2) -> exit sensor (xR) / rejector
        0.0            0.5             1.0        1.5            2.5

Each can carries ground-truth defects. Every camera counts cans on its own
trigger, and on a defect sends a reject request as raw bytes through a
simulated switch to the Rj-Pi. The run passes if the Rj-Pi rejects exactly the
defective cans.

Usage:
    python simulation.py [--cans 1000] [--seed 0] [--defect-rate 0.05] [-v]
    python -m unittest test_simulation -v
"""

import argparse
import heapq
import itertools
import random
import sys
from dataclasses import dataclass, field

from elp import ElpCamera
from keyence import KeyenceCamera
from rejector_pi import ACN_MODULO, RejectorPi
from telodyne import TelodyneCamera


class EventScheduler:
    """Discrete-event clock: callbacks run in time order, ties in schedule order."""

    def __init__(self):
        self.now = 0.0
        self._queue = []
        self._seq = itertools.count()

    def schedule(self, delay, callback, *args):
        heapq.heappush(self._queue, (self.now + delay, next(self._seq), callback, args))

    def run(self):
        while self._queue:
            self.now, _, callback, args = heapq.heappop(self._queue)
            callback(*args)


class Switch:
    """Simulated Ethernet switch that delivers raw bytes to a port."""

    def __init__(self, scheduler, rng, latency, jitter, drop_rate):
        self.scheduler = scheduler
        self.rng = rng
        self.latency = latency
        self.jitter = jitter
        self.drop_rate = drop_rate
        self._ports = {}
        self.packets_sent = 0
        self.packets_dropped = 0

    def register(self, port, handler):
        self._ports[port] = handler

    def send(self, port, data):
        if not isinstance(data, bytes):
            raise TypeError(f"switch only carries bytes, got {type(data).__name__}")
        self.packets_sent += 1
        if self.rng.random() < self.drop_rate:
            self.packets_dropped += 1
            return
        delay = self.latency + self.rng.uniform(0, self.jitter)
        self.scheduler.schedule(delay, self._ports[port], data)


@dataclass(frozen=True)
class Can:
    can_id: int  # true absolute can number, 1-based (ground truth only)
    defects: frozenset  # names of the cameras that should flag this can


@dataclass
class SimConfig:
    num_cans: int = 1000
    seed: int = 0

    belt_speed: float = 0.5  # m/s
    can_spacing: float = 0.1  # m, minimum gap between cans
    spacing_jitter: float = 0.5  # extra gap, as a fraction of can_spacing

    telodyne_pos: float = 0.5
    keyence_pos: float = 1.0
    elp_pos: float = 1.5
    exit_pos: float = 2.5

    # Seconds from a camera's trigger until its reject request is sent.
    telodyne_processing: float = 0.15
    keyence_processing: float = 0.05
    elp_processing: float = 0.20

    network_latency: float = 0.002
    network_jitter: float = 0.001
    drop_rate: float = 0.0

    defect_rate: float = 0.05  # per camera, per can
    # can_id -> camera names. When set, replaces random defects entirely.
    forced_defects: dict = None

    verbose: bool = False

    def validate(self):
        if not 0 < self.telodyne_pos < self.keyence_pos < self.elp_pos < self.exit_pos:
            raise ValueError(
                "cameras must sit in order between the entry and exit sensors"
            )
        max_on_belt = int(self.exit_pos / self.can_spacing) + 1
        if max_on_belt >= ACN_MODULO:
            raise ValueError(
                f"up to {max_on_belt} cans fit on the belt; a 1-byte ACN needs fewer than "
                f"{ACN_MODULO}"
            )


@dataclass
class SimResult:
    defective_ids: set
    rejected_ids: set
    acn_mismatches: list = field(default_factory=list)  # (can_id, expected, got, where)
    rejector: RejectorPi = None
    cameras: dict = None
    switch: Switch = None

    @property
    def false_rejects(self):
        return self.rejected_ids - self.defective_ids

    @property
    def missed_rejects(self):
        return self.defective_ids - self.rejected_ids

    @property
    def passed(self):
        return not (self.false_rejects or self.missed_rejects or self.acn_mismatches)


def make_cans(config, rng):
    cans = []
    for can_id in range(1, config.num_cans + 1):
        if config.forced_defects is not None:
            defects = frozenset(config.forced_defects.get(can_id, ()))
        else:
            defects = frozenset(
                name
                for name in (TelodyneCamera.NAME, KeyenceCamera.NAME, ElpCamera.NAME)
                if rng.random() < config.defect_rate
            )
        cans.append(Can(can_id, defects))
    return cans


def run_simulation(config=None):
    config = config or SimConfig()
    config.validate()
    rng = random.Random(config.seed)
    scheduler = EventScheduler()

    def log(msg):
        if config.verbose:
            print(f"[{scheduler.now:9.3f}s] {msg}")

    switch = Switch(
        scheduler, rng, config.network_latency, config.network_jitter, config.drop_rate
    )
    rejector = RejectorPi(log=log)
    switch.register(RejectorPi.PORT, rejector.receive)

    cameras = {
        TelodyneCamera.NAME: (
            TelodyneCamera(
                scheduler, switch, RejectorPi.PORT, config.telodyne_processing
            ),
            config.telodyne_pos,
        ),
        KeyenceCamera.NAME: (
            KeyenceCamera(
                scheduler, switch, RejectorPi.PORT, config.keyence_processing
            ),
            config.keyence_pos,
        ),
        ElpCamera.NAME: (
            ElpCamera(scheduler, switch, RejectorPi.PORT, config.elp_processing),
            config.elp_pos,
        ),
    }

    cans = make_cans(config, rng)
    result = SimResult(
        defective_ids={can.can_id for can in cans if can.defects},
        rejected_ids=set(),
        rejector=rejector,
        cameras={name: camera for name, (camera, _) in cameras.items()},
        switch=switch,
    )

    def check_acn(can, got, where):
        expected = can.can_id % ACN_MODULO
        if got != expected:
            result.acn_mismatches.append((can.can_id, expected, got, where))

    def camera_trigger(camera, can):
        camera.on_can(can)
        check_acn(can, camera.acn, camera.NAME)

    def can_enters(can):
        check_acn(can, rejector.on_entry_sensor(), "entry")
        for camera, pos in cameras.values():
            scheduler.schedule(pos / config.belt_speed, camera_trigger, camera, can)
        scheduler.schedule(config.exit_pos / config.belt_speed, can_exits, can)

    def can_exits(can):
        acn, rejected = rejector.on_exit_sensor()
        check_acn(can, acn, "exit")
        if rejected:
            result.rejected_ids.add(can.can_id)

    entry_time = 0.0
    for can in cans:
        scheduler.schedule(entry_time, can_enters, can)
        gap = config.can_spacing * (1 + rng.uniform(0, config.spacing_jitter))
        entry_time += gap / config.belt_speed

    scheduler.run()
    return result


def print_summary(result):
    rejector = result.rejector
    print("=== Rejector Simulation (No-Star, 1-byte ACN) ===")
    print(f"Defective cans:      {len(result.defective_ids)}")
    print(f"Rejected cans:       {len(result.rejected_ids)}")
    print(
        f"False rejects:       {len(result.false_rejects)} {sorted(result.false_rejects)[:10]}"
    )
    print(
        f"Missed rejects:      {len(result.missed_rejects)} {sorted(result.missed_rejects)[:10]}"
    )
    print(f"ACN mismatches:      {len(result.acn_mismatches)}")
    for name, camera in result.cameras.items():
        print(f"  {name:<9} requests sent: {camera.requests_sent}")
    print(
        f"Switch sent/dropped: {result.switch.packets_sent}/{result.switch.packets_dropped}"
    )
    print(
        f"Rj-Pi received:      {rejector.packets_received} "
        f"(invalid {rejector.invalid_packets}, late {rejector.late_packets})"
    )
    print(f"Cans left on belt:   {rejector.cans_on_belt}")
    print("RESULT:", "PASS" if result.passed else "FAIL")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cans", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--defect-rate", type=float, default=0.05)
    parser.add_argument("--drop-rate", type=float, default=0.0)
    parser.add_argument(
        "--latency", type=float, default=0.002, help="network latency (s)"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    result = run_simulation(
        SimConfig(
            num_cans=args.cans,
            seed=args.seed,
            defect_rate=args.defect_rate,
            drop_rate=args.drop_rate,
            network_latency=args.latency,
            verbose=args.verbose,
        )
    )
    print_summary(result)
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
