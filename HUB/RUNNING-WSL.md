# Running the HUB on Windows (WSL)

The WSL version of [RUNNING.md](RUNNING.md). The operator screen, live runs,
validation runs, and replay options are the same on both machines. This page
covers what's different on WSL: where things live, which Python to use, and the
networking setup the real Rj-Pi needs.

All commands run in the WSL (Ubuntu) terminal, from the `HUB` folder:

```bash
cd ~/Projects/Capstone/HUB
```

Keep the project in the Linux file system (`~/...`), not under `/mnt/c/...`.
Files on the Windows drive are much slower from WSL, and SQLite's locking
doesn't work reliably there.

## 1. One-time setup

```bash
sudo apt install python3-venv          # only if the next line complains
python3 -m venv ~/Projects/Capstone/.venv
~/Projects/Capstone/.venv/bin/pip install -r requirements.txt
```

The virtual environment lives at the root of the repo in `.venv`. iCloud
doesn't sync here, so it can sit next to the project, and git ignores it (the
venv adds its own `.gitignore`). On this machine it's already set up.

To skip typing the full path, activate it once per terminal:

```bash
source ~/Projects/Capstone/.venv/bin/activate
```

After that, plain `python` means the venv's Python. The rest of this page
writes the full path so the commands work either way.

## 2. Start the HUB

```bash
~/Projects/Capstone/.venv/bin/python -m hub --db hub.db
```

Open these in a browser **on Windows** (Edge, Chrome). WSL forwards
`localhost` to Windows automatically:

| Page | Address |
| --- | --- |
| Operator screen | http://localhost:8000 |
| Dashboard | http://localhost:8000/dashboard |

Leave this terminal open. **Ctrl-C** stops the HUB.

Demo data, if you want it, goes in **before** starting the HUB:

```bash
python3 tools/demo_data.py --db hub.db
```

## 3. Live runs and validation runs

These are the same as on the Mac: see
[RUNNING.md, section 3](RUNNING.md#3-do-a-live-run).

## 4. Test without hardware (simulation)

Same as [RUNNING.md, section 4](RUNNING.md#4-test-without-hardware-simulation).
Open a second WSL terminal (a new tab in Windows Terminal), `cd` to the `HUB`
folder, and run:

```bash
python3 tools/replay.py --cans 300 --rate 5
```

The replay and the Rejector simulation need only the standard library, so
plain `python3` works for them.

## 5. With the real Rj-Pi

This is where WSL needs more setup than the Mac. By default WSL sits behind
its own private network (NAT, with an address like `172.21.x.x`). Your
browser on Windows can reach it, but nothing else on the switch can. The
Rj-Pi's UDP events on port `5001` never arrive, and Windows port forwarding
(`netsh portproxy`) only forwards TCP, so it can't fix this.

The fix is **mirrored networking**, which gives WSL the same network
addresses as Windows. You set it up once:

1. In Windows, create or edit `C:\Users\<you>\.wslconfig` and add:

   ```ini
   [wsl2]
   networkingMode=mirrored
   ```

2. In **PowerShell**, restart WSL, then reopen your WSL terminal:

   ```powershell
   wsl --shutdown
   ```

   Check that it worked: `wslinfo --networking-mode` should now print
   `mirrored`.

3. Let the Rj-Pi and other devices through the firewall. In **PowerShell as
   Administrator**:

   ```powershell
   New-NetFirewallHyperVRule -Name HUB-RjPi -DisplayName "HUB Rj-Pi events" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol UDP -LocalPorts 5001
   New-NetFirewallHyperVRule -Name HUB-Web -DisplayName "HUB web pages" -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 8000
   ```

Then for each session with the line:

1. Plug the laptop into the switch. Give its **Windows** Ethernet adapter a
   fixed IP on the Rj-Pi's subnet, for example `192.168.1.50`: Settings →
   Network & internet → Ethernet → IP assignment → Edit → Manual. With
   mirrored networking, WSL uses the same address.
2. In `Rejector/rj_main.py`, set `HUB_ADDR` to that IP (port `5001`).
3. Start the HUB as in step 2. The operator screen shows **Rj-Pi online**
   once the Rj-Pi is running.

Anyone else on the switch can open `http://<laptop IP>:8000/dashboard`.

## Useful extras

- **Start over:** stop the HUB, `rm hub.db*`, start it again.
- **Find the laptop's IP:** `ip -4 addr` in WSL (with mirrored networking it
  lists the Windows addresses), or `ipconfig` in PowerShell.
- **Phone or tablet:** needs mirrored networking and the firewall rules from
  step 5. Then open `http://<laptop IP>:8000` on the same network.
- **Run the HUB tests:** `~/Projects/Capstone/.venv/bin/python -m unittest discover tests -v`
- **Open the project in VS Code:** `code ~/Projects/Capstone` from WSL.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `Address already in use` | A HUB is already running, maybe in another WSL tab. Stop it, or run `pkill -f "python -m hub"` |
| `No module named fastapi` | Use `~/Projects/Capstone/.venv/bin/python` (or activate the venv), not plain `python3`, to start the HUB |
| `ensurepip is not available` when creating the venv | `sudo apt install python3-venv`, then make the venv again |
| `localhost:8000` doesn't load in the Windows browser | Make sure the HUB terminal is still running. If it is, try the WSL address from `hostname -I`, e.g. `http://172.21.x.x:8000` |
| Operator screen stays on **Waiting for Rj-Pi** with the real Rj-Pi running | Check `wslinfo --networking-mode` says `mirrored`, the firewall rules from step 5 exist, and `HUB_ADDR` matches the Windows Ethernet IP |
| The Rj-Pi still can't reach the HUB with everything above in place | Windows Defender Firewall may also be blocking it: allow inbound UDP 5001 (and TCP 8000) for the Ethernet network there too |
| Operator screen says **Waiting for Rj-Pi** during a simulation | Nothing has sent events yet: start the replay |
| Counts don't move during a replay | A run must be active; cans that enter with no run active aren't counted in any run |
