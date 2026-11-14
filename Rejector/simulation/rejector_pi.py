"""Rejector Raspberry Pi (Rj-Pi).

Receives reject requests from the Analysis Systems (AS) as raw bytes, builds a
RejectItem per can, and fires the rejector when a flagged can reaches the exit
sensor (xR).

Can numbering
    ACN  - Absolute Can Number. The Rj-Pi's own count of every can past the
           entry sensor since it started: 1, 2, 3, ... It never wraps.
    RACN - Relative Can Number, ACN % 256. This is the 1-byte number the cameras
           count and send (can 256 -> 0, can 257 -> 1, ...).

    The Rj-Pi turns each RACN it receives back into the ACN of the matching can
    on the belt (see racn_to_acn). This works while fewer than 256 cans are on
    the belt between the entry and exit sensors.

Packet formats (camera -> Rj-Pi, UDP port 5000)
    Pi cameras (Telodyne, ELP): 2 bytes -> [SystemNumber, RACN]
    Keyence:                    1 byte  -> [RACN]

The Rj-Pi has two sensors:
    entry sensor - at the start of the belt, assigns the next ACN to a can
    exit sensor  - at the end of the belt (xR), identifies the can at the rejector

HUB events
    Pass emit=<callable> to RejectorPi to receive one dict per event. rj_main.py
    sends these to the HUB; the simulation collects them. See notes.md.
"""

from dataclasses import dataclass

ACN_MODULO = 256

TELODYNE_SYSTEM_NUMBER = 1
ELP_SYSTEM_NUMBER = 2

# Maps a 2-byte packet's SystemNumber to the RejectItem flag it sets.
SYSTEM_FLAGS = {
    TELODYNE_SYSTEM_NUMBER: "c1",
    ELP_SYSTEM_NUMBER: "c2",
}


@dataclass
class RejectItem:
    acn: int
    c1: bool = False
    keyence: bool = False
    c2: bool = False

    @property
    def racn(self):
        return self.acn % ACN_MODULO

    @property
    def should_reject(self):
        return self.c1 or self.keyence or self.c2


class RejectorPi:
    PORT = 5000

    def __init__(self, log=None, emit=None):
        self.log = log or (lambda msg: None)
        self.emit = emit or (lambda event: None)
        self.entry_acn = 0  # ACN of the last can past the entry sensor
        self.exit_acn = 0  # ACN of the last can past the exit sensor
        self.reject_items = {}  # ACN -> RejectItem, for cans still on the belt
        self.rejection_log = []  # RejectItems that fired the rejector

        self.packets_received = 0
        self.invalid_packets = 0  # malformed / unknown system number
        self.late_packets = 0  # RACN not on the belt (already exited)
        self.unexpected_exits = 0  # exit sensor fired with no can on the belt
        self.overflows = 0  # entries refused because the belt was full

    @property
    def cans_on_belt(self):
        return self.entry_acn - self.exit_acn

    def racn_to_acn(self, racn):
        """ACN of the on-belt can whose RACN is racn, or None if there is none."""
        acn = self.exit_acn + (racn - self.exit_acn) % ACN_MODULO
        if self.exit_acn < acn <= self.entry_acn:
            return acn
        return None

    def stats(self):
        return {
            "entry_acn": self.entry_acn,
            "exit_acn": self.exit_acn,
            "cans_on_belt": self.cans_on_belt,
            "packets_received": self.packets_received,
            "invalid_packets": self.invalid_packets,
            "late_packets": self.late_packets,
            "unexpected_exits": self.unexpected_exits,
            "overflows": self.overflows,
            "rejections": len(self.rejection_log),
        }

    # --- Sensors ---------------------------------------------------------

    def on_entry_sensor(self):
        """A can passed the entry sensor. Returns its ACN."""
        if self.cans_on_belt >= ACN_MODULO - 1:
            # One more can would give two on-belt cans the same RACN.
            self.overflows += 1
            self.emit({"e": "overflow"})
            raise OverflowError(
                f"{ACN_MODULO - 1} cans already on the belt; 1-byte RACN would wrap"
            )
        self.entry_acn += 1
        self.emit({"e": "entry", "acn": self.entry_acn})
        return self.entry_acn

    def on_exit_sensor(self):
        """A can reached the exit sensor (xR). Returns (acn, rejected)."""
        if self.cans_on_belt == 0:
            self.unexpected_exits += 1
            self.log("WARNING: exit sensor fired with no can on the belt")
            self.emit({"e": "unexpected_exit"})
            return None, False

        self.exit_acn += 1
        item = self.reject_items.pop(self.exit_acn, None) or RejectItem(self.exit_acn)
        rejected = item.should_reject
        if rejected:
            self.fire_rejector(item)
        self.emit({
            "e": "exit",
            "acn": item.acn,
            "rejected": rejected,
            "c1": item.c1,
            "keyence": item.keyence,
            "c2": item.c2,
        })
        return self.exit_acn, rejected

    def fire_rejector(self, item):
        self.rejection_log.append(item)
        self.log(
            f"REJECT ACN {item.acn} (RACN {item.racn:3d}, "
            f"c1={item.c1}, keyence={item.keyence}, c2={item.c2})"
        )

    # --- Network ---------------------------------------------------------

    @staticmethod
    def parse_packet(data):
        """Returns (flag_name, racn), or None if the packet is invalid."""
        if len(data) == 1:
            return "keyence", data[0]
        if len(data) == 2:
            system_number, racn = data
            flag = SYSTEM_FLAGS.get(system_number)
            if flag is not None:
                return flag, racn
        return None

    def receive(self, data):
        """Handle one packet of raw bytes from the switch."""
        self.packets_received += 1
        parsed = self.parse_packet(data)
        if parsed is None:
            self.invalid_packets += 1
            self.log(f"WARNING: invalid packet {data!r}")
            self.emit({"e": "invalid", "data": bytes(data).hex()})
            return

        flag, racn = parsed
        acn = self.racn_to_acn(racn)
        if acn is None:
            # The can already passed xR (or never entered). Dropping it keeps a
            # stale item from rejecting the can that reuses this RACN 256 later.
            self.late_packets += 1
            self.log(f"WARNING: late packet from {flag} for RACN {racn}")
            self.emit({"e": "late", "src": flag, "racn": racn})
            return

        item = self.reject_items.setdefault(acn, RejectItem(acn))
        setattr(item, flag, True)
        self.emit({"e": "flag", "src": flag, "acn": acn})
