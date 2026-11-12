# Telodyne Team: Rejector Addition

When your code decides a can should be rejected, send one small message to the
Rejector Pi (Rj-Pi). This takes two small changes to your code.

| Setting       | Value                                                 |
| ------------- | ----------------------------------------------------- |
| Protocol      | UDP                                                   |
| Destination   | Rj-Pi IP address, port 5000                           |
| Message       | 2 bytes: `[1, ACN]`                                   |
| System number | `1` (Telodyne)                                        |
| ACN           | Absolute Can Number, 0-255, wraps back to 0 after 255 |

## Step 1: Add this near the top of your file

Replace the IP address with the Rj-Pi's address on the switch.

```python
import socket

RJ_PI_ADDR = ("192.168.1.100", 5000)  # TODO: set to the Rj-Pi's IP address
TELODYNE_SYSTEM_NUMBER = 1
_reject_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def send_reject(acn):
    packet = bytes([TELODYNE_SYSTEM_NUMBER, acn % 256])
    try:
        _reject_socket.sendto(packet, RJ_PI_ADDR)
        print(f"[REJECT SENT] system={packet[0]} acn={packet[1]} to {RJ_PI_ADDR[0]}:{RJ_PI_ADDR[1]}")
    except OSError as error:
        print(f"[REJECT FAILED] system={packet[0]} acn={packet[1]}: {error}")
```

## Step 2: Call it where you decide to reject

Use your own variable names for the decision and the can number.

```python
if should_reject:
    send_reject(acn)
```

Only call `send_reject` for cans you are rejecting. Good cans send nothing.

## What `acn` must be

- Count **every can** that passes your camera trigger, good or bad, exactly once.
- The first can is ACN 1, the second is ACN 2, and so on. Counting past 255 is
  fine because `send_reject` wraps the number for you.
- Use the number of the can **in the image you analyzed**. If your analysis takes
  a while, your counter may have moved on to later cans by the time you decide,
  so save the ACN with the image when it is captured.
- Start your counter when the Rj-Pi and the other cameras start counting. If one
  system misses a can, its numbers no longer match and it will flag the wrong cans.

## Testing it

Run this listener on the Rj-Pi, then run your code and reject a can. You should
see `[REJECT SENT]` on your Pi and a matching `[REJECT RECEIVED]` on the Rj-Pi.

```python
import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 5000))
print("Listening for reject requests on port 5000")

while True:
    data, sender = sock.recvfrom(16)
    print(f"[REJECT RECEIVED] from {sender[0]}: system={data[0]} acn={data[1]}")
```

`[REJECT SENT]` means the message left your Pi. UDP does not confirm delivery, so
only `[REJECT RECEIVED]` on the Rj-Pi proves that it arrived.
