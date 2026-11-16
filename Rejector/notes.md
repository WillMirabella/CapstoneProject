# Rejector System

## Idea

We need a rejection system that can accept requests from all 3 camera systems.
Each Camera system may have a different payload format, so it may be best to
pass key:value where key is camera system number

### Star or no Star

- We limit all Analysis Systems (AS) to 1 byte transmission
  - If we do, we can have Rejector reset ACN on 256 along with all Analysis Systems
- If some Analysis Systems are limited to 1 byte and some aren't, we employ Star

### Star System

- When tracking ACN (Absolute Can Number) a system limited by 1 byte
  will only be able to track cans up to 256, then it will reset to 1 for ACN 257.
- Star System will use modulo plus Star where Star value is the
  quotient of ACN / 2^8
- The Rejector will either track Star of calculate it when a transmission
  from a limited Analysis System is received.

### Ethernet or UART

- For ethernet communication we would need a switch to manage the connections.
- For UART we would be able to connect all 3 AS's to the Rejector PI.
  - UART requires a lot more code to manage the communication.

## Current Approach

- Use our (Team 2's) Raspberry Pi to act as Rejector Pi (Rj-Pi)
  - This should be okay because it's currently only being used to run the
- All 3 AS's and the Rj-Pi should be connected to a switch for communication
- The Rj-Pi will accept data packets, see **Data Packet**.
- The Rj-Pi will also accept an integer value from the Keyence camera.
  - It will know it's the Keyence value because it will be 1 byte.
- With any transmission, Rj-Pi will build a RejectItem. See **RejecctItem**.
- These RejectItems will be queued for rejection based on xR (exit Rejector Sensor).

### Data Packet

- Pis should provide key value pairs specifying which system it is.
  - May not be needed since Pi could listen on multiple ports.
  - {
    SystemNumber : ACN
    }
- Keyence will only provide ACN
  - { ACN }

### RejectItem

bool c1
bool Keyence
bool c2
int ACN

## ACN vs RACN (current)

- **ACN**: the Rj-Pi's total count of cans past the entry sensor since startup
  (1, 2, 3, ...). Never wraps. `RejectItem.acn` and every HUB event use it.
- **RACN**: `ACN % 256`, the 1-byte number the cameras count and send.
  Camera packet formats are unchanged:
  - Telodyne / ELP: 2 bytes `[SystemNumber, RACN]`
  - Keyence: 1 byte `[RACN]`
- `RejectorPi.racn_to_acn()` maps an incoming RACN to the on-belt can's ACN.
  This needs fewer than 256 cans between the entry and exit sensors.
- ACN restarts at 1 when the Rj-Pi restarts, so the HUB keys cans by
  `(session, acn)`.

## Rj-Pi -> HUB events

UDP to the HUB on port 5001, one compact JSON object per datagram,
fire-and-forget (the Rj-Pi never waits on the HUB). Every event has:

| Field     | Meaning                                                       |
| --------- | ------------------------------------------------------------- |
| `e`       | event type (below)                                            |
| `session` | random hex id chosen at Rj-Pi startup                         |
| `seq`     | 0, 1, 2, ... per session; a gap means datagrams were lost     |
| `t`       | seconds since Rj-Pi startup (the HUB stamps wall-clock time) |

| `e`               | Extra fields                                       | When                                     |
| ----------------- | -------------------------------------------------- | ---------------------------------------- |
| `hello`           |                                                    | Rj-Pi started                            |
| `entry`           | `acn`                                              | can passed the entry sensor              |
| `flag`            | `src` (c1/keyence/c2), `acn`                       | valid reject request for an on-belt can  |
| `late`            | `src`, `racn`                                      | reject request for a can no longer on the belt |
| `invalid`         | `data` (hex bytes)                                 | malformed packet                         |
| `exit`            | `acn`, `rejected`, `c1`, `keyence`, `c2`           | can reached the exit sensor              |
| `unexpected_exit` |                                                    | exit sensor fired with no can on the belt |
| `overflow`        |                                                    | entry refused: 255 cans already on belt  |
| `health`          | `entry_acn`, `exit_acn`, `cans_on_belt`, counters  | every 5 s                                |
