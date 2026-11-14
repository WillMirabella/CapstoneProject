# Running the HUB

How to start the dashboard and operator screen, run live runs, and test
everything with the simulator when the cameras and Rj-Pi aren't available.

All commands run from the `HUB` folder:

```bash
cd ~/Library/Mobile\ Documents/com~apple~CloudDocs/Course/CAPSTONE/CapstoneProject/HUB
```

## 1. One-time setup

```bash
python3 -m venv ~/.venvs/hub
~/.venvs/hub/bin/pip install -r requirements.txt
```

Keep the virtual environment in your home folder, not in this project:
iCloud Drive would try to sync its thousands of library files.

## 2. Start the HUB

```bash
~/.venvs/hub/bin/python -m hub --db hub.db
```

| Page | Address | Who uses it |
| --- | --- | --- |
| Operator screen | http://localhost:8000 | The person at the front of the line |
| Dashboard | http://localhost:8000/dashboard | Anyone looking at defect trends |

Leave this terminal open. **Ctrl-C** stops the HUB.

`hub.db` is created on first start and holds every shipment, run, and can.
It's git-ignored. Want a dashboard with something on it before you have real
data? Load a month of made-up history **before** starting the HUB:

```bash
python3 tools/demo_data.py --db hub.db
```

## 3. Do a live run

1. On the operator screen, under **Production run**, pick a shipment, then a
   SKU. Use **+ New shipment** for a new delivery (supplier, arrival date,
   PO, bill of lading, and its SKUs).
2. Type your name and press **START RUN**.
3. Put the cans on the line. The screen counts cans entered, on the belt,
   passed inspection, and rejected, plus each camera's flags.
4. When the last can of the run is on the belt, tap **STOP RUN** twice.
   The run shows as *finishing*, and its passed/rejected counts keep
   updating until its last can reaches the exit sensor. You can start the
   next run right away; cans are never mixed up between runs.

One run = one SKU from one shipment. Changing SKU or shipment means a new run.

### Validation run (checking the cameras)

1. Open the **Validation run** tab and set how many cans you'll run.
2. Add the seeded (known-defective) cans by position: can #1 is the first
   can on the belt. Toggle which camera(s) should catch each defect, or
   leave them all off if any camera counts. **Random 5** picks positions for you.
3. Line the cans up in that order and press **START VALIDATION**. The screen
   highlights when the next can should be a seeded one.
4. Stop the run after the last can. The summary shows each camera's
   detection rate. The dashboard keeps the history.

## 4. Test without hardware (simulation)

`tools/replay.py` pretends to be the Rj-Pi. It sends the HUB the same events
the real one does, using the Rejector simulation. With the HUB running, start
a run on the operator screen, then in a **second terminal**:

```bash
python3 tools/replay.py --cans 300 --rate 5
```

Stop the run on the operator screen when the replay says all cans were sent.
The replay then idles like a real Rj-Pi; Ctrl-C stops it.

| To see | Add to the replay command |
| --- | --- |
| A camera over-flagging | `--defects telodyne=0.15` |
| A validation run (match the positions you entered on screen) | `--cans 40 --defects 0 --seed-defects 5:c1,20:keyence,33:` |
| Lost network messages | `--drop-rate 0.02` |
| The Rj-Pi rebooting mid-run | `--restart-at 100` |
| Everything faster | `--speed 5` |
| "Rj-Pi offline" on the operator screen | Ctrl-C the replay and wait about 12 s |

In `--seed-defects`, each `position:cameras` entry says which cameras will
*detect* that can (`c1` = Telodyne, `keyence`, `c2` = ELP; join several with
`+`). An empty camera list (`33:`) simulates every camera missing it.
`python3 tools/replay.py --help` lists every option.

The Rejector simulation can also run on its own, without the HUB:

```bash
cd ../Rejector/simulation
python3 simulation.py --cans 1000 -v
python3 -m unittest test_simulation -v
```

## 5. With the real Rj-Pi

1. Plug the laptop into the switch and give it a fixed IP on the same subnet
   as the Rj-Pi, for example `192.168.1.50`.
2. In `Rejector/rj_main.py`, set `HUB_ADDR` to that IP (port `5001`).
3. Start the HUB as in step 2. The operator screen shows **Rj-Pi online**
   once the Rj-Pi is running.

Anyone else on the switch can open `http://<laptop IP>:8000/dashboard`.

## Useful extras

- **Start over:** stop the HUB, `rm hub.db*`, start it again.
- **Phone or tablet:** on the same network, open `http://<laptop IP>:8000`.
  The laptop's IP is in System Settings → Wi-Fi → Details.
- **Run the HUB tests:** `~/.venvs/hub/bin/python -m unittest discover tests -v`

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `Address already in use` | A HUB is already running. Stop it, or run `pkill -f "python -m hub"` |
| `No module named fastapi` | Use `~/.venvs/hub/bin/python`, not plain `python3`, to start the HUB |
| Operator screen says **Waiting for Rj-Pi** | Nothing has sent events yet: start the replay or the real Rj-Pi |
| No shipments to pick | Add one with **+ New shipment**, or load demo data (step 2) |
| Counts don't move during a replay | A run must be active; cans that enter with no run active aren't counted in any run |
