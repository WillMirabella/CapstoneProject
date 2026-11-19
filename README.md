# Can Line Rejector and HUB

This repo currently holds Will's work for the Rejector and the HUB:

- **Rejector (Rj-Pi):** a Raspberry Pi that takes reject requests from every
  camera, matches each one to the right can, and fires the reject relay when that
  can reaches the end of the belt.
- **HUB:** a server that records every can the Rj-Pi handles. It runs the
  operator screen and the defect trends dashboard.

**On the belt** (cans move left to right):

```
entry sensor ─> Telodyne (c1) ─> Keyence ─> ELP (c2) ─> exit sensor + reject relay
```

**Data flow:**

```
Telodyne (c1) ──┐
Keyence ────────┼─ UDP :5000 ─> Rj-Pi ─ UDP :5001 ─> HUB <─ HTTP :8000 ─ browsers
ELP (c2) ───────┘                │ ▲
                                 │ ├── entry sensor (USB)
                                 │ └── exit sensor (GPIO17)
                                 └───> reject relay (GPIO27)
```

Arrows point toward the receiving device, and each port belongs to that
device. The cameras, the Rj-Pi, and the HUB all connect to one Ethernet switch. The
sensors and the relay are wired straight to the Rj-Pi.

| Folder | Contents |
| --- | --- |
| [`Rejector/`](Rejector) | Rj-Pi program, design notes, simulator, camera team guides |
| [`HUB/`](HUB) | Run tracking server, operator screen, dashboard |
| [`KeyenceFiles/`](KeyenceFiles) | Sample Keyence images from the 9/26 demo |
| [`Documentation/`](Documentation) | Rejector + HUB overview slides |

---

## Rejector

### For camera teams: sending a reject request

Send reject requests only for bad cans. Good cans send nothing.

| | Telodyne | ELP | Keyence |
| --- | --- | --- | --- |
| Flag name | `c1` | `c2` | `keyence` |
| Transport | UDP to Rj-Pi port **5000** | UDP to Rj-Pi port **5000** | UDP to Rj-Pi port **5000** |
| Packet | 2 bytes `[1, RACN]` | 2 bytes `[2, RACN]` | 1 byte `[RACN]` |

**RACN** is your can count modulo 256. To get it right:

- Count **every** can that passes your trigger, good or bad, exactly once. The
  first can is 1. After 255 the count wraps to 0.
- Send the number of the can **in the image you analyzed**. Save it when you
  capture the image, because your counter will have moved on by the time
  analysis finishes.
- Start counting when the Rj-Pi starts. If you miss or double-count a can, every
  later request flags the wrong can.

Copy-paste code and a test listener for the Pi cameras are in
[`Rejector/team_additions/telodyne.md`](Rejector/team_additions/telodyne.md).
ELP uses the same code with system number `2`.

### How the Rj-Pi works

1. **Entry sensor** (USB PCsensor trigger): each can gets the next **ACN**
   (Absolute Can Number: 1, 2, 3, ... never wraps).
2. **Reject request arrives:** the Rj-Pi maps the RACN back to the ACN of the
   on-belt can with that number and sets that camera's flag on the can's
   `RejectItem`.
3. **Exit sensor** (GPIO17): the next can in order reaches the rejector. If
   any flag is set, the relay (GPIO27) pulses.

Each `RejectItem` records which cameras flagged the can (`c1`, `keyence`,
`c2`). Any one flag rejects it.

**Limits and edge cases**

- Fewer than 256 cans can be on the belt between the sensors at once.
  Otherwise two cans would share a RACN. A 256th entry is refused and logged
  as `overflow`.
- A request for a can that has already exited is dropped as `late`. That keeps
  it from rejecting the next can with the same RACN.
- Malformed packets or unknown system numbers are counted as `invalid`.
- ACN restarts at 1 when the Rj-Pi reboots.

### Files

| File | Purpose |
| --- | --- |
| `Rejector/rj_main.py` | Runs on the Pi: sensors, relay, UDP listener, HUB link |
| `Rejector/simulation/rejector_pi.py` | Core logic (`RejectorPi`, `RejectItem`). Deploy it next to `rj_main.py` |
| `Rejector/simulation/` | Discrete-event simulator of the full line, with tests |
| `Rejector/notes.md` | Design notes and the Rj-Pi -> HUB event protocol |
| `Rejector/pi_archive/` | Earlier version from before ACN/RACN and the HUB |

### Running

On the Pi, put `rj_main.py` and `rejector_pi.py` in the same folder:

```bash
python3 rj_main.py
```

Before the first run, check the settings at the top of `rj_main.py`:
`HUB_ADDR`, sensor/relay pins, `EXIT_SENSOR_PULL_UP`, `RELAY_ACTIVE_HIGH`, and
the timing values (`REJECT_DELAY_SECONDS`, `RELAY_PULSE_SECONDS`, debounce).
Several of these are still marked `TODO` until they are tuned on the real belt.

Without hardware, run the simulator:

```bash
cd Rejector/simulation
python3 simulation.py --cans 1000 -v          # also --defect-rate, --drop-rate, --seed
python3 -m unittest test_simulation -v
```

---

## HUB

The HUB is a Python (FastAPI + SQLite) server on a laptop on the switch. The
Rj-Pi sends it an event for every entry, flag, and exit. Sending is
fire-and-forget, so **rejecting never depends on the HUB**: if the HUB is down,
the Rj-Pi keeps working.

### What it tracks

- **Shipments** of empty cans (supplier, arrival date, PO, bill of lading)
  and the SKUs in each.
- **Production runs:** one SKU from one shipment. Each run counts cans
  entered, passed, and rejected, plus each camera's flags.
- **Validation runs:** known-defective cans at chosen positions. The HUB scores
  each camera's detection rate.
- **Every can**, keyed by `(session, acn)`. It records which cameras flagged
  the can and whether it was rejected.
- **Rj-Pi health:** online/offline status, lost datagrams, late and invalid
  requests per camera, and reboots mid-run.

### Pages

| Page | URL | Audience |
| --- | --- | --- |
| Operator screen | `http://<hub-ip>:8000/` | Line operator: start/stop runs, live counts |
| Dashboard | `http://<hub-ip>:8000/dashboard` | Anyone: defect trends and camera performance |

The dashboard shows:

- Reject rate per day and per shipment. A shipment is marked as an outlier at
  1.75x the average, once it has 300+ cans.
- Flag rate per camera, which shows when one camera drifts from the others.
- Camera agreement: how often each camera is the only one to flag a can.
- Validation history and Rj-Pi health.
- A CSV export of the runs.

You can filter by date, supplier, and SKU. The filters stay in the URL, so you
can share a filtered view.

A JSON API sits under `/api/` (`/api/state`, `/api/dashboard`, `/api/runs`,
`/api/export/runs.csv`, ...). FastAPI documents it at `/docs`.

### Quick start

```bash
cd HUB
python3 -m venv ~/.venvs/hub && ~/.venvs/hub/bin/pip install -r requirements.txt
python3 tools/demo_data.py --db hub.db         # optional: a month of fake history
~/.venvs/hub/bin/python -m hub --db hub.db     # http://localhost:8000
python3 tools/replay.py --cans 300 --rate 5    # second terminal: fake Rj-Pi
```

To see data from the replay, start a run on the operator screen first. To
connect the real Rj-Pi, set `HUB_ADDR` in `rj_main.py` to the HUB laptop's IP.

More detail:

- [`HUB/README.md`](HUB/README.md): concepts, layout, and replay options
- [`HUB/RUNNING.md`](HUB/RUNNING.md): step-by-step guide for macOS
- [`HUB/RUNNING-WSL.md`](HUB/RUNNING-WSL.md): Windows/WSL guide, including the
  mirrored networking setup the real Rj-Pi needs

Tests: `cd HUB && ~/.venvs/hub/bin/python -m unittest discover tests -v`

Requires Python 3.9+ and SQLite 3.35+.

---

## Ports at a glance

| Port | Protocol | From -> To | Data |
| --- | --- | --- | --- |
| 5000 | UDP | Cameras -> Rj-Pi | Reject requests (1-2 bytes) |
| 5001 | UDP | Rj-Pi -> HUB | JSON events (see [`Rejector/notes.md`](Rejector/notes.md)) |
| 8000 | HTTP | Browsers -> HUB | Operator screen, dashboard, API |
