# HUB

Records every can the Rj-Pi handles, labels it with the run it belongs to, and
serves the operator touchscreen (`/`) and the defect trends dashboard (`/dashboard`).

**Quick start: see [RUNNING.md](RUNNING.md)** for starting the HUB, live runs,
and testing with the simulator.

The Rj-Pi never waits on the HUB: it sends events over UDP port 5001 and keeps
rejecting whether or not the HUB is running. Protocol: `../Rejector/notes.md`.

## Concepts

- **Shipment**: one delivery of empty cans (supplier, arrival date, PO, bill of lading, ...).
- **Shipment line**: one SKU within a shipment.
- **Production run**: exactly one shipment line. Change SKU or shipment -> new run.
- **Validation run**: the operator runs `planned_cans` cans with known defects at
  seeded positions (position 1 = first can of the run), optionally naming which
  cameras should catch each one. The HUB scores each camera's detection rate.
- **Can**: identified by `(session, acn)`; `position` is its order within its run.

## Try it at home (no hardware)

Everything runs on one laptop. `tools/replay.py` pretends to be the Rj-Pi and
sends the HUB the same UDP events the real one does.

One-time setup. Keep the virtual environment **outside iCloud Drive**, which
would otherwise try to sync thousands of library files:

    python3 -m venv ~/.venvs/hub
    ~/.venvs/hub/bin/pip install -r requirements.txt

Each time (two terminals, both in this HUB folder):

    # Terminal 1: the HUB. Demo data is optional (fake shipments + 30 days of runs).
    python3 tools/demo_data.py --db hub.db
    ~/.venvs/hub/bin/python -m hub --db hub.db

    # Operator screen: http://localhost:8000   Dashboard: http://localhost:8000/dashboard
    # On the operator screen pick a shipment and SKU, press START RUN, then:

    # Terminal 2: the fake Rj-Pi
    python3 tools/replay.py --cans 300 --rate 5

Things to try with the replay (`python3 tools/replay.py --help` for all of them):

| Command | What it shows |
| --- | --- |
| `--defects telodyne=0.12` | one camera over-flagging |
| `--cans 40 --defects 0 --seed-defects 5:c1,20:keyence,33:` | a validation run (match the positions you entered on screen; `33:` = every camera misses it) |
| `--drop-rate 0.02` | lost network datagrams: recovered cans and the "events lost" banner |
| `--restart-at 100` | the Rj-Pi rebooting mid-run: lost cans, run marked incomplete |
| `--speed 5` | everything 5x faster |
| Ctrl-C the replay | "Rj-Pi offline" after about 12 s |

To test touch on a phone or tablet, connect it to the same Wi-Fi and open
`http://<your laptop's IP>:8000`. In desktop Chrome, DevTools device mode at
800x480 matches the official Raspberry Pi 7" touchscreen.

## Dashboard

For office staff on the plant network. Filters (date range, supplier, SKU) apply
to everything on the page and are kept in the URL, so a filtered view can be
bookmarked or shared. Defect trends count production runs only; validation runs
have their own table. Every chart has a Table button with the same numbers.

- **Reject rate per day** and **flag rate by camera per day**: a camera drifting
  away from the other two shows up here.
- **Reject rate by shipment**: one bar per SKU in each delivery. A bar turns red
  with a warning when it is at least 1.75x the period average over 300+ cans
  (`OUTLIER_RATIO` / `OUTLIER_MIN_CANS` in `hub/dashboard.py`).
- **Camera agreement**: how often each camera is the only one that flagged a can.
- **Validation runs**, **runs**, and **Rj-Pi health** (lost datagrams,
  recovered/lost cans, late reject requests per camera) tables.
- **Export runs (CSV)** downloads every run in the current filter.

## Layout

    hub/main.py       server: UDP listener (5001), JSON API, live stream, pages (8000)
    hub/db.py         SQLite schema
    hub/tracker.py    Rj-Pi events + operator actions -> runs and cans
    hub/reports.py    run summary, validation scoring, operator-screen state
    hub/dashboard.py  dashboard aggregates + runs CSV export
    web/              operator touchscreen + dashboard (plain HTML/CSS/JS + SVG, works offline)
    tools/replay.py   fake Rj-Pi
    tools/demo_data.py  fake shipments and run history
    tests/            driven by ../Rejector/simulation events

## Tests

    ~/.venvs/hub/bin/python -m unittest discover tests -v

(With plain `python3` the API tests are skipped; the rest still run.)

Needs Python 3.9+ and SQLite 3.35+ (Raspberry Pi OS Bookworm or any recent macOS/Linux).
