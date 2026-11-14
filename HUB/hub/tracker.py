"""Turns Rj-Pi events into cans, runs and shipments in the database.

The Rj-Pi stays the real-time authority: it decides what gets rejected. The HUB
only records what happened and labels each can with the run it belongs to.

How cans are assigned to runs
    entry event   -> new can in the active run (or no run if none is active),
                     at the next position in that run (1, 2, 3, ...)
    Stop Run      -> the run stops taking new cans and is "finishing" until
                     every one of its cans has exited, then "complete"
    exit event    -> fills in the can's camera flags and rejected result

    A missing entry (lost datagram) is recovered from the ACN numbering: the
    missing cans go to the active run, marked recovered=1.
    If the Rj-Pi restarts (new session), cans of the old session still on the
    belt can never exit; they are marked lost=1 and their run is "incomplete".

Event format: see Rejector/notes.md, "Rj-Pi -> HUB events".
"""

import json
import threading
import time
from contextlib import contextmanager

from .db import CAMERAS


class RunError(Exception):
    """An operator action that isn't allowed right now (shown on screen)."""


class Tracker:
    def __init__(self, conn, clock=time.time):
        self.conn = conn
        self.clock = clock
        self.lock = threading.RLock()

    @contextmanager
    def _tx(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def _one(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()

    # --- Shipments ---------------------------------------------------------

    def add_shipment(self, supplier, arrival_date=None, po_number=None,
                     bill_of_lading=None, carrier=None, tracking_number=None,
                     notes=None):
        if not supplier or not supplier.strip():
            raise RunError("A shipment needs a supplier")
        with self._tx():
            return self.conn.execute(
                "INSERT INTO shipment (supplier, arrival_date, po_number, bill_of_lading,"
                " carrier, tracking_number, notes, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (supplier.strip(), arrival_date, po_number, bill_of_lading, carrier,
                 tracking_number, notes, self.clock()),
            ).lastrowid

    def add_shipment_line(self, shipment_id, sku, lot_code=None, quantity=None,
                          notes=None):
        if not sku or not sku.strip():
            raise RunError("A shipment line needs a SKU")
        with self._tx():
            if not self._one("SELECT 1 FROM shipment WHERE id = ?", shipment_id):
                raise RunError(f"No shipment {shipment_id}")
            return self.conn.execute(
                "INSERT INTO shipment_line (shipment_id, sku, lot_code, quantity, notes)"
                " VALUES (?, ?, ?, ?, ?)",
                (shipment_id, sku.strip(), lot_code, quantity, notes),
            ).lastrowid

    # --- Runs (operator actions) ---------------------------------------------

    def active_run(self):
        return self._one("SELECT * FROM run WHERE status = 'active'")

    def start_production_run(self, shipment_line_id, operator=None, notes=None):
        with self._tx():
            if not self._one("SELECT 1 FROM shipment_line WHERE id = ?", shipment_line_id):
                raise RunError(f"No shipment line {shipment_line_id}")
            return self._start_run("production", shipment_line_id, operator, notes, None)

    def start_validation_run(self, seeds, planned_cans, operator=None, notes=None,
                             shipment_line_id=None):
        """seeds: {position: {"expected": ["c1", ...], "defect": "side dent"}}

        position 1 is the first can the operator puts on the belt. "expected"
        lists the cameras that should catch that defect; leave it empty if any
        camera catching it counts.
        """
        if not seeds:
            raise RunError("A validation run needs at least one seeded can")
        if not planned_cans or planned_cans < 1:
            raise RunError("A validation run needs the number of cans to run")
        rows = []
        for position, seed in seeds.items():
            position = int(position)
            seed = seed or {}
            expected = list(seed.get("expected") or [])
            unknown = set(expected) - set(CAMERAS)
            if unknown:
                raise RunError(f"Unknown camera(s) {sorted(unknown)}")
            if not 1 <= position <= planned_cans:
                raise RunError(f"Seeded position {position} is outside 1-{planned_cans}")
            rows.append((position, ",".join(c for c in CAMERAS if c in expected),
                         seed.get("defect")))
        with self._tx():
            run_id = self._start_run("validation", shipment_line_id, operator, notes,
                                     planned_cans)
            self.conn.executemany(
                "INSERT INTO seeded_can (run_id, position, expected, defect)"
                " VALUES (?, ?, ?, ?)",
                [(run_id, *row) for row in rows],
            )
            return run_id

    def _start_run(self, kind, shipment_line_id, operator, notes, planned_cans):
        if self.active_run() is not None:
            raise RunError("Stop the current run before starting a new one")
        return self.conn.execute(
            "INSERT INTO run (kind, shipment_line_id, operator, notes, planned_cans,"
            " status, started_at) VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (kind, shipment_line_id, operator, notes, planned_cans, self.clock()),
        ).lastrowid

    def stop_run(self):
        """The last can of the active run is on the belt. Returns the run id."""
        with self._tx():
            run = self.active_run()
            if run is None:
                raise RunError("No run is active")
            self.conn.execute(
                "UPDATE run SET status = 'finishing', stopped_at = ? WHERE id = ?",
                (self.clock(), run["id"]),
            )
            self._check_finished(run["id"])
            return run["id"]

    def expire_stale_runs(self, timeout=60.0):
        """Mark finishing runs incomplete if their cans stopped exiting.

        Call this every few seconds. A run is stale when nothing in it has
        exited for `timeout` seconds since it was stopped.
        """
        now = self.clock()
        with self._tx():
            for run in self.conn.execute(
                "SELECT r.id, max(r.stopped_at, coalesce(max(c.exited_at), 0)) AS last"
                " FROM run r LEFT JOIN can c ON c.run_id = r.id"
                " WHERE r.status = 'finishing' GROUP BY r.id"
            ).fetchall():
                if now - run["last"] >= timeout:
                    self.conn.execute(
                        "UPDATE run SET status = 'incomplete', finished_at = ?"
                        " WHERE id = ?", (now, run["id"]),
                    )

    def _check_finished(self, run_id):
        run = self._one("SELECT status FROM run WHERE id = ?", run_id)
        if run is None or run["status"] not in ("finishing", "incomplete"):
            return
        counts = self._one(
            "SELECT count(*) FILTER (WHERE exited_at IS NULL AND lost = 0) AS waiting,"
            "       count(*) FILTER (WHERE lost = 1) AS lost"
            " FROM can WHERE run_id = ?", run_id,
        )
        if counts["waiting"] == 0:
            status = "incomplete" if counts["lost"] else "complete"
            self.conn.execute(
                "UPDATE run SET status = ?, finished_at = coalesce(finished_at, ?)"
                " WHERE id = ?", (status, self.clock(), run_id),
            )

    # --- Rj-Pi events ----------------------------------------------------------

    def handle_datagram(self, data):
        """Raw UDP payload from the Rj-Pi. Bad JSON is logged, never raised."""
        try:
            event = json.loads(data)
            if not isinstance(event, dict):
                raise ValueError("not an object")
        except ValueError:
            with self._tx():
                self._log(None, data.decode("utf-8", "replace") if isinstance(
                    data, bytes) else str(data))
            return
        self.handle(event)

    def handle(self, event):
        now = self.clock()
        with self._tx():
            self._log(event, json.dumps(event, separators=(",", ":")))
            session, e = event.get("session"), event.get("e")
            if not isinstance(session, str) or not isinstance(e, str):
                return
            self._track_session(session, event.get("seq"), now, event)
            handler = getattr(self, f"_on_{e}", None)
            if handler is not None:
                handler(event, session, now)

    def _log(self, event, raw):
        event = event or {}
        self.conn.execute(
            "INSERT INTO event_log (received_at, session, seq, e, raw)"
            " VALUES (?, ?, ?, ?, ?)",
            (self.clock(), event.get("session"), event.get("seq"), event.get("e"), raw),
        )

    def _track_session(self, session, seq, now, event):
        seq = seq if isinstance(seq, int) else None
        row = self._one("SELECT * FROM rj_session WHERE session = ?", session)
        if row is None:
            self.conn.execute(
                "INSERT INTO rj_session (session, first_seen, last_seen, last_seq, missed)"
                " VALUES (?, ?, ?, ?, ?)",
                (session, now, now, seq if seq is not None else -1,
                 seq if seq is not None else 0),
            )
            self._lose_cans_from_other_sessions(session)
        else:
            missed, last_seq = row["missed"], row["last_seq"]
            if seq is not None and seq > last_seq:
                missed += seq - last_seq - 1
                last_seq = seq
            self.conn.execute(
                "UPDATE rj_session SET last_seen = ?, last_seq = ?, missed = ?"
                " WHERE session = ?", (now, last_seq, missed, session),
            )

    def _lose_cans_from_other_sessions(self, session):
        """The Rj-Pi restarted, so cans it was tracking will never exit."""
        runs = [r["run_id"] for r in self.conn.execute(
            "SELECT DISTINCT run_id FROM can WHERE session != ? AND exited_at IS NULL"
            " AND lost = 0 AND run_id IS NOT NULL", (session,))]
        self.conn.execute(
            "UPDATE can SET lost = 1 WHERE session != ? AND exited_at IS NULL", (session,))
        for run_id in runs:
            self._check_finished(run_id)

    # Each handler runs inside the transaction opened by handle().

    def _on_entry(self, event, session, now):
        acn = event.get("acn")
        if not isinstance(acn, int):
            return
        self._fill_missing_entries(session, acn - 1)
        if self._can(session, acn) is None:
            run = self.active_run()
            self._add_can(session, acn, run["id"] if run else None,
                          entered_at=now, recovered=False)

    def _on_flag(self, event, session, now):
        acn, src = event.get("acn"), event.get("src")
        if isinstance(acn, int) and src in CAMERAS:
            self._ensure_can(session, acn)
            self.conn.execute(
                f"UPDATE can SET {src} = 1 WHERE session = ? AND acn = ?", (session, acn))

    def _on_exit(self, event, session, now):
        acn = event.get("acn")
        if not isinstance(acn, int):
            return
        can = self._ensure_can(session, acn)
        flags = {c: 1 if event.get(c) else 0 for c in CAMERAS}
        self.conn.execute(
            "UPDATE can SET exited_at = ?, rejected = ?, lost = 0, c1 = ?, keyence = ?,"
            " c2 = ? WHERE id = ?",
            (now, 1 if event.get("rejected") else 0, flags["c1"], flags["keyence"],
             flags["c2"], can["id"]),
        )
        if can["run_id"] is not None:
            self._check_finished(can["run_id"])

    def _on_health(self, event, session, now):
        health = {k: v for k, v in event.items() if k not in ("e", "session", "seq")}
        self.conn.execute(
            "UPDATE rj_session SET health = ? WHERE session = ?",
            (json.dumps(health), session),
        )

    # --- Can bookkeeping --------------------------------------------------------

    def _can(self, session, acn):
        return self._one("SELECT * FROM can WHERE session = ? AND acn = ?", session, acn)

    def _last_entered(self, session):
        return self._one(
            "SELECT * FROM can WHERE session = ? ORDER BY acn DESC LIMIT 1", session)

    def _ensure_can(self, session, acn):
        """The can for a flag/exit event, recovering it if its entry was lost."""
        can = self._can(session, acn)
        if can is None:
            self._fill_missing_entries(session, acn)
            can = self._can(session, acn)
        if can is None:
            # Older than every can we know in this session; we can't place it.
            self._add_can(session, acn, None, entered_at=None, recovered=True)
            can = self._can(session, acn)
        return can

    def _fill_missing_entries(self, session, upto_acn):
        """Create cans whose entry datagrams were lost, up to and including upto_acn.

        They go to whichever run is active now (or none), the same as if their
        entry events had arrived.
        """
        last = self._last_entered(session)
        first = 1 if last is None else last["acn"] + 1
        if first > upto_acn:
            return
        active = self.active_run()
        run_id = active["id"] if active else None
        for acn in range(first, upto_acn + 1):
            self._add_can(session, acn, run_id, entered_at=None, recovered=True)

    def _add_can(self, session, acn, run_id, entered_at, recovered):
        position = None
        if run_id is not None:
            position = self._one(
                "UPDATE run SET entered = entered + 1 WHERE id = ? RETURNING entered",
                run_id)["entered"]
        self.conn.execute(
            "INSERT INTO can (run_id, session, acn, position, entered_at, recovered)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, session, acn, position, entered_at, 1 if recovered else 0),
        )
