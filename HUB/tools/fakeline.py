"""Builds Rj-Pi -> HUB events from the Rejector simulation (shared by the tools)."""

import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "Rejector", "simulation"))

from simulation import SimConfig, run_simulation  # noqa: E402

SIM_CAMERAS = ("telodyne", "keyence", "elp")
ALIASES = {"telodyne": "telodyne", "c1": "telodyne", "keyence": "keyence",
           "k": "keyence", "elp": "elp", "c2": "elp"}
BELT_SPEED = SimConfig.belt_speed


def camera(name):
    try:
        return ALIASES[name.strip().lower()]
    except KeyError:
        raise ValueError(f"unknown camera {name!r}; use telodyne/c1, keyence, elp/c2")


def parse_rates(text, default):
    """"telodyne=0.1,keyence=0.02" -> {"telodyne": 0.1, "keyence": 0.02, "elp": default}

    A bare number sets all three cameras: "0.9" -> {"telodyne": 0.9, ...}.
    """
    try:
        return dict.fromkeys(SIM_CAMERAS, float(text))
    except (TypeError, ValueError):
        pass
    rates = dict.fromkeys(SIM_CAMERAS, default)
    for part in filter(None, (p.strip() for p in (text or "").split(","))):
        name, _, value = part.partition("=")
        rates[camera(name)] = float(value)
    return rates


def parse_seeds(text):
    """"5:c1,20:keyence,41:c1+c2,60:" -> {5: {"telodyne"}, ..., 60: set()}

    The cameras listed are the ones that will *detect* the defect in the
    simulation; leave the list empty to simulate every camera missing it.
    """
    seeds = {}
    for part in filter(None, (p.strip() for p in (text or "").split(","))):
        position, _, cams = part.partition(":")
        seeds[int(position)] = {camera(c) for c in cams.split("+") if c.strip()}
    return seeds


def make_events(num_cans, rates, seeds=None, cans_per_second=3.0, seed=None,
                defect_rate=0.0, detect=None):
    """Simulated events for num_cans cans, each with "t" = seconds since start.

    Two ways a camera flags a can:
      * defect_rate: chance a can is really defective. Each camera then catches it
        with its own `detect` chance, so a real defect is usually flagged by all three.
      * rates: per-camera chance of flagging a good can on its own (false flags).
    seeds (validation) override both for the positions they list.
    """
    rng = random.Random(seed)
    detect = detect or dict.fromkeys(SIM_CAMERAS, 1.0)
    forced = {}
    for can_id in range(1, num_cans + 1):
        cams = set()
        if rng.random() < defect_rate:
            cams = {c for c in SIM_CAMERAS if rng.random() < detect.get(c, 0)}
        cams |= {c for c in SIM_CAMERAS if rng.random() < rates.get(c, 0)}
        if cams:
            forced[can_id] = cams
    for position, cams in (seeds or {}).items():
        if 1 <= position <= num_cans:
            forced[position] = cams
    spacing = BELT_SPEED / cans_per_second / 1.1  # jitter below adds ~10% on average
    result = run_simulation(SimConfig(
        num_cans=num_cans, seed=rng.randrange(2**32), can_spacing=spacing,
        spacing_jitter=0.2, forced_defects=forced))
    return result.hub_events
