"""Telodyne Analysis System (c1), run by a Raspberry Pi.

Counts cans as they pass its trigger to track RACN (1 byte, wraps at 256) and
sends a 2-byte reject request [SystemNumber, RACN] when it finds a defect.
"""

ACN_MODULO = 256


class TelodyneCamera:
    NAME = "telodyne"
    SYSTEM_NUMBER = 1

    def __init__(self, scheduler, switch, rejector_port, processing_time):
        self.scheduler = scheduler
        self.switch = switch
        self.rejector_port = rejector_port
        self.processing_time = processing_time
        self.racn = 0
        self.requests_sent = 0

    def on_can(self, can):
        """Camera trigger: a can is in front of the camera."""
        self.racn = (self.racn + 1) % ACN_MODULO
        if self.inspect(can):
            self.scheduler.schedule(self.processing_time, self.send_reject, self.racn)

    def inspect(self, can):
        return self.NAME in can.defects

    def build_packet(self, racn):
        return bytes([self.SYSTEM_NUMBER, racn])

    def send_reject(self, racn):
        self.requests_sent += 1
        self.switch.send(self.rejector_port, self.build_packet(racn))
