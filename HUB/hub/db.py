"""SQLite storage for the HUB.

One file holds everything. Times are Unix seconds (float) from the HUB's clock,
because the Rj-Pi may not have a real-time clock on an isolated switch.
"""

import sqlite3

CAMERAS = ("c1", "keyence", "c2")  # RejectItem flag names, in belt order
CAMERA_NAMES = {"c1": "Telodyne", "keyence": "Keyence", "c2": "ELP"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS shipment (
    id              INTEGER PRIMARY KEY,
    supplier        TEXT NOT NULL,
    arrival_date    TEXT,            -- YYYY-MM-DD
    po_number       TEXT,
    bill_of_lading  TEXT,
    carrier         TEXT,
    tracking_number TEXT,
    notes           TEXT,
    created_at      REAL NOT NULL
);

-- One SKU within a shipment. A production run uses exactly one line.
CREATE TABLE IF NOT EXISTS shipment_line (
    id          INTEGER PRIMARY KEY,
    shipment_id INTEGER NOT NULL REFERENCES shipment(id),
    sku         TEXT NOT NULL,
    lot_code    TEXT,
    quantity    INTEGER,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS run (
    id               INTEGER PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ('production', 'validation')),
    shipment_line_id INTEGER REFERENCES shipment_line(id),
    operator         TEXT,
    notes            TEXT,
    planned_cans     INTEGER,        -- validation: how many cans the operator will run
    -- active: cans entering now; finishing: stopped, cans still on the belt;
    -- complete: every can exited; incomplete: some cans never reported an exit
    status           TEXT NOT NULL CHECK (status IN
                         ('active', 'finishing', 'complete', 'incomplete')),
    entered          INTEGER NOT NULL DEFAULT 0,  -- cans assigned so far
    started_at       REAL NOT NULL,
    stopped_at       REAL,
    finished_at      REAL,
    CHECK (kind = 'validation' OR shipment_line_id IS NOT NULL)
);

-- Validation runs: which positions in the run are known-defective cans.
CREATE TABLE IF NOT EXISTS seeded_can (
    run_id   INTEGER NOT NULL REFERENCES run(id),
    position INTEGER NOT NULL CHECK (position >= 1),  -- 1 = first can of the run
    expected TEXT NOT NULL DEFAULT '',  -- comma list of cameras that should flag it; '' = any
    defect   TEXT,                      -- what was done to the can, e.g. "side dent"
    PRIMARY KEY (run_id, position)
);

CREATE TABLE IF NOT EXISTS can (
    id         INTEGER PRIMARY KEY,
    run_id     INTEGER REFERENCES run(id),  -- NULL: entered with no active run
    session    TEXT NOT NULL,
    acn        INTEGER NOT NULL,
    position   INTEGER,                     -- 1-based order within its run
    entered_at REAL,                        -- NULL if the entry event was lost
    exited_at  REAL,
    c1         INTEGER NOT NULL DEFAULT 0,
    keyence    INTEGER NOT NULL DEFAULT 0,
    c2         INTEGER NOT NULL DEFAULT 0,
    rejected   INTEGER,                     -- NULL until the can exits
    recovered  INTEGER NOT NULL DEFAULT 0,  -- run inferred because entry was lost
    lost       INTEGER NOT NULL DEFAULT 0,  -- Rj-Pi restarted before it exited
    UNIQUE (session, acn)
);
CREATE INDEX IF NOT EXISTS can_run ON can(run_id, position);

CREATE TABLE IF NOT EXISTS rj_session (
    session    TEXT PRIMARY KEY,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL,
    last_seq   INTEGER NOT NULL,
    missed     INTEGER NOT NULL DEFAULT 0,  -- datagrams lost (gaps in seq)
    health     TEXT                         -- last health event, JSON
);

CREATE TABLE IF NOT EXISTS event_log (
    id          INTEGER PRIMARY KEY,
    received_at REAL NOT NULL,
    session     TEXT,
    seq         INTEGER,
    e           TEXT,
    raw         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS event_log_e ON event_log(e, received_at);
"""


def connect(path=":memory:"):
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")  # dashboard reads don't block writes
    conn.executescript(SCHEMA)
    return conn
