# Button Test

Press the BOOTSEL button on the Pico → see "Button pressed!" print on your Mac.

## What's on the Pico

`pico_code/main.py` runs on the Pico. It loops forever watching the BOOTSEL button (the small white button on the board). When pressed, it prints a message and lights up the onboard LED.

## Requirements

- Pico must be plugged into your Mac via USB
- MicroPython must be flashed on the Pico (already done)

## How to run

**Step 1 — Send the code to the Pico (only needed when you change the code):**
```bash
mpremote connect /dev/tty.usbmodem21201 cp pico_code/main.py :main.py + reset
```

**Step 2 — Start the monitor on your Mac to see output:**
```bash
python3 monitor.py
```

**Step 3 — Press the BOOTSEL button on the Pico.**

You'll see `Button pressed!` appear in your terminal.

## Notes

- If the serial port isn't `/dev/tty.usbmodem21201`, run `ls /dev/tty.usb*` to find the right one
- If mpremote says the port is in use, run `pkill screen` then try again
- The Pico does NOT need to be re-flashed or re-programmed between uses — just plug it in and run `monitor.py`
