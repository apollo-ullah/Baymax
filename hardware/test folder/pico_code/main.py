import rp2
from machine import Pin
import time

led = Pin(25, Pin.OUT)  # onboard LED

print("Pico ready — press the BOOTSEL button!")

was_pressed = False

while True:
    pressed = rp2.bootsel_button() == 1

    if pressed and not was_pressed:
        print("Button pressed!")
        led.on()

    if not pressed and was_pressed:
        led.off()

    was_pressed = pressed
    time.sleep(0.02)
