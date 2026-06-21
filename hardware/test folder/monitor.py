import serial
import serial.tools.list_ports
import sys

def find_pico():
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if "usbmodem" in p.device or "usbserial" in p.device:
            return p.device
    return None

port = find_pico()

if not port:
    print("Pico not found. Make sure MicroPython is flashed and it's plugged in.")
    print("Available ports:", [p.device for p in serial.tools.list_ports.comports()])
    sys.exit(1)

print(f"Connected to Pico on {port}")
print("Waiting for button presses... (Ctrl+C to quit)\n")

with serial.Serial(port, 115200, timeout=1) as ser:
    while True:
        line = ser.readline().decode("utf-8", errors="ignore").strip()
        if line:
            print(line)
