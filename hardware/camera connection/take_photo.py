from picamera2 import Picamera2
from datetime import datetime
import os

SAVE_PATH = os.path.expanduser("~/photos")
os.makedirs(SAVE_PATH, exist_ok=True)

picam2 = Picamera2()
picam2.configure(picam2.create_still_configuration())
picam2.start()

filename = os.path.join(SAVE_PATH, f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
picam2.capture_file(filename)
picam2.stop()

print(f"Saved: {filename}")
