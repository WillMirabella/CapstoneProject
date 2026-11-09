"""Keyence Analysis System.

Counts cans as they pass its trigger to track ACN (1 byte, wraps at 256) and
sends a 1-byte reject request [ACN] when it finds a defect. The Rj-Pi knows
the packet is from the Keyence because it is the only 1-byte packet.
"""

ACN_MODULO = 256


class KeyenceCamera:
    NAME = "keyence"

    def __init__(self, scheduler, switch, rejector_port, processing_time):
        self.scheduler = scheduler
        self.switch = switch
        self.rejector_port = rejector_port
        self.processing_time = processing_time
        self.acn = 0
        self.requests_sent = 0

    def on_can(self, can):
        """Camera trigger: a can is in front of the camera."""
        self.acn = (self.acn + 1) % ACN_MODULO
        if self.inspect(can):
            self.scheduler.schedule(self.processing_time, self.send_reject, self.acn)

    def inspect(self, can):
        return self.NAME in can.defects

    def build_packet(self, acn):
        return bytes([acn])

    def send_reject(self, acn):
        self.requests_sent += 1
        self.switch.send(self.rejector_port, self.build_packet(acn))
