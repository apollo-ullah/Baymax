import os
import time
import threading
import subprocess
import tkinter as tk
from datetime import datetime
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from PIL import Image, ImageTk

SAVE_PATH = "/home/bmonster/Desktop/pi_camera"
IMAGE_PATH = os.path.join(SAVE_PATH, "images")
VIDEO_PATH = os.path.join(SAVE_PATH, "videos")

# Ensure directories exist
os.makedirs(IMAGE_PATH, exist_ok=True)
os.makedirs(VIDEO_PATH, exist_ok=True)

# Initialize the camera
picam2 = Picamera2()
picam2.configure(picam2.create_still_configuration())  # Default to still image mode

# ---------- Tkinter GUI ----------
root = tk.Tk()
root.title("Camera Controller")  
root.resizable(False, False)  
WINDOW_WIDTH = 900  
root.geometry(f"{WINDOW_WIDTH}x100")  

video_window = None
recording = False
encoder = None
video_filename = ""

def get_timestamp():
    """Returns a timestamp string."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def create_video_window():
    """Creates the video feed window when preview starts."""
    global video_window, countdown_label
    if video_window is None or not video_window.winfo_exists():
        video_window = tk.Toplevel(root)
        video_window.title("Live Preview")
        video_window.protocol("WM_DELETE_WINDOW", stop_preview)
        video_window.geometry("650x550")  # Increased height for countdown padding

        global image_label
        image_label = tk.Label(video_window)
        image_label.pack()

        # Countdown Label (Positioned Below Video)
        countdown_label = tk.Label(video_window, text="", font=("Arial", 30, "bold"), fg="red")
        countdown_label.pack(pady=20)  # Adds padding below video feed

def update_preview():
    """Continuously updates the Tkinter Label with live preview frames."""
    if video_window is None or not video_window.winfo_exists():
        return  

    frame = picam2.capture_array()
    img = Image.fromarray(frame).resize((640, 480))
    tk_img = ImageTk.PhotoImage(img)
    image_label.config(image=tk_img)
    image_label.image = tk_img  
    video_window.after(50, update_preview)  

def start_preview():
    """Starts live preview and opens video feed window."""
    create_video_window()  
    try:
        picam2.start()
        update_preview()  
    except Exception as e:
        print(f"Error starting preview: {e}")

def stop_preview():
    """Stops live preview and closes video feed window."""
    try:
        if video_window:
            video_window.destroy()  
        picam2.stop()
    except Exception as e:
        print(f"Error stopping preview: {e}")

# ---------- Capture Image ----------
def capture_image():
    """Captures an image and saves it without freezing."""
    def _capture():
        filename = os.path.join(IMAGE_PATH, f"img_{get_timestamp()}.jpg")
        try:
            print("Capturing image...")
            img = picam2.capture_image()
            img = img.convert("RGB")
            img.save(filename)
            print(f"Image saved: {filename}")
        except Exception as e:
            print(f"Error capturing image: {e}")

    threading.Thread(target=_capture, daemon=True).start()  

# ---------- Countdown Timer Below Video ----------
def show_countdown_timer(count):
    """Updates the countdown timer below the video feed."""
    if video_window and countdown_label.winfo_exists():
        countdown_label.config(text=str(count))  # Display countdown below video
        if count > 0:
            video_window.after(1000, show_countdown_timer, count - 1)  
        else:
            countdown_label.config(text="")  # Clear countdown when finished
            start_recording_after_countdown()  

def start_recording():
    """Starts the countdown timer before recording."""
    show_countdown_timer(3)  

def start_recording_after_countdown():
    """Starts recording video after countdown finishes."""
    global recording, encoder, video_filename
    if recording:
        print("Already recording...")
        return

    video_filename = os.path.join(VIDEO_PATH, f"video_{get_timestamp()}.h264")
    encoder = H264Encoder()
    try:
        print(f"Recording video: {video_filename}")
        recording = True
        picam2.stop()
        picam2.configure(picam2.create_video_configuration())
        picam2.start()
        picam2.start_recording(encoder, video_filename)

    except Exception as e:
        print(f"Error starting recording: {e}")

# ---------- Stop Recording & Convert to MP4 ----------
def stop_recording():
    """Stops recording and converts to MP4 without freezing."""
    global recording, encoder, video_filename
    if not recording:
        print("Not currently recording...")
        return

    def _stop():
        global recording, encoder, video_filename
        try:
            print("Stopping recording...")
            picam2.stop_recording()
            picam2.stop()
            picam2.configure(picam2.create_still_configuration())  
            picam2.start()

            recording = False
            encoder = None

            if video_filename:
                mp4_filename = video_filename.replace(".h264", ".mp4")

                def convert_to_mp4():
                    try:
                        print(f"Converting {video_filename} to {mp4_filename}...")
                        subprocess.run(["ffmpeg", "-i", video_filename, "-c:v", "copy", "-movflags", "+faststart", mp4_filename], check=True)
                        os.remove(video_filename)  
                        print(f"Conversion complete! Saved as: {mp4_filename}")
                    except Exception as e:
                        print(f"Error converting video: {e}")

                threading.Thread(target=convert_to_mp4, daemon=True).start()

        except Exception as e:
            print(f"Error stopping recording: {e}")

    threading.Thread(target=_stop, daemon=True).start()

# ---------- Tkinter Buttons ----------
buttons_frame = tk.Frame(root)
buttons_frame.pack(pady=10)

start_preview_button = tk.Button(buttons_frame, text="Start Preview", width=18, command=start_preview)
start_preview_button.grid(row=0, column=0, padx=5)

stop_preview_button = tk.Button(buttons_frame, text="Stop Preview", width=18, command=stop_preview)
stop_preview_button.grid(row=0, column=1, padx=5)

capture_button = tk.Button(buttons_frame, text="Take a Picture", width=18, command=capture_image)
capture_button.grid(row=0, column=2, padx=5)

start_video_button = tk.Button(buttons_frame, text="Start Recording", width=18, command=start_recording)
start_video_button.grid(row=0, column=3, padx=5)

stop_video_button = tk.Button(buttons_frame, text="Stop Recording", width=18, command=stop_recording)
stop_video_button.grid(row=0, column=4, padx=5)

# Quit button to exit safely
def on_closing():
    """Ensures clean shutdown of camera before exiting."""
    stop_preview()
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_closing)

# Start Tkinter main loop
root.mainloop()