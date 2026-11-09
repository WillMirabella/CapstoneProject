"""Rejector Raspberry Pi (Rj-Pi).

Receives reject requests from the Analysis Systems (AS) as raw bytes, builds a
RejectItem per Absolute Can Number (ACN), and fires the rejector when a flagged
can reaches the exit sensor (xR).

No-Star mode: every AS sends the ACN as a single byte, so the Rj-Pi and every
AS count ACNs modulo 256 (can 256 -> 0, can 257 -> 1, ...).

Packet formats
    Pi cameras (Telodyne, ELP): 2 bytes -> [SystemNumber, ACN]
    Keyence:                    1 byte  -> [ACN]

The Rj-Pi has two sensors:
    entry sensor - at the start of the belt, assigns the next ACN to a can
    exit sensor  - at the end of the belt (xR), identifies the can at the rejector
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
    def should_reject(self):
        return self.c1 or self.keyence or self.c2


class RejectorPi:
    PORT = 5000

    def __init__(self, log=None):
        self.log = log or (lambda msg: None)
        self.entry_acn = 0  # ACN of the last can past the entry sensor
        self.exit_acn = 0  # ACN of the last can past the exit sensor
        self.reject_items = {}  # ACN -> RejectItem, for cans still on the belt
        self.rejection_log = []  # RejectItems that fired the rejector

        self.packets_received = 0
        self.invalid_packets = 0  # malformed / unknown system number
        self.late_packets = 0  # ACN not on the belt (already exited)
        self.unexpected_exits = 0  # exit sensor fired with no can on the belt

    @property
    def cans_on_belt(self):
        return (self.entry_acn - self.exit_acn) % ACN_MODULO

    def is_on_belt(self, acn):
        offset = (acn - self.exit_acn) % ACN_MODULO
        return 0 < offset <= self.cans_on_belt

    # --- Sensors ---------------------------------------------------------

    def on_entry_sensor(self):
        """A can passed the entry sensor. Returns its (1-byte) ACN."""
        if self.cans_on_belt == ACN_MODULO - 1:
            # One more can would make on-belt ACNs ambiguous with a 1-byte ACN.
            raise OverflowError(
                f"{ACN_MODULO - 1} cans already on the belt; 1-byte ACN would wrap"
            )
        self.entry_acn = (self.entry_acn + 1) % ACN_MODULO
        return self.entry_acn

    def on_exit_sensor(self):
        """A can reached the exit sensor (xR). Returns (acn, rejected)."""
        if self.cans_on_belt == 0:
            self.unexpected_exits += 1
            self.log("WARNING: exit sensor fired with no can on the belt")
            return None, False

        self.exit_acn = (self.exit_acn + 1) % ACN_MODULO
        item = self.reject_items.pop(self.exit_acn, None)
        if item is not None and item.should_reject:
            self.fire_rejector(item)
            return self.exit_acn, True
        return self.exit_acn, False

    def fire_rejector(self, item):
        self.rejection_log.append(item)
        self.log(
            f"REJECT ACN {item.acn:3d} "
            f"(c1={item.c1}, keyence={item.keyence}, c2={item.c2})"
        )

    # --- Network ---------------------------------------------------------

    @staticmethod
    def parse_packet(data):
        """Returns (flag_name, acn), or None if the packet is invalid."""
        if len(data) == 1:
            return "keyence", data[0]
        if len(data) == 2:
            system_number, acn = data
            flag = SYSTEM_FLAGS.get(system_number)
            if flag is not None:
                return flag, acn
        return None

    def receive(self, data):
        """Handle one packet of raw bytes from the switch."""
        self.packets_received += 1
        parsed = self.parse_packet(data)
        if parsed is None:
            self.invalid_packets += 1
            self.log(f"WARNING: invalid packet {data!r}")
            return

        flag, acn = parsed
        if not self.is_on_belt(acn):
            # The can already passed xR (or never entered). Dropping it keeps a
            # stale item from rejecting the can that reuses this ACN 256 later.
            self.late_packets += 1
            self.log(f"WARNING: late packet from {flag} for ACN {acn}")
            return

        item = self.reject_items.setdefault(acn, RejectItem(acn))
        setattr(item, flag, True)
