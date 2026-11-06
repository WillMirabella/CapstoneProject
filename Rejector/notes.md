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
