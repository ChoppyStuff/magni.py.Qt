#!/usr/bin/python3
# Open camera preview, wait for button/key press event on raspi and react by
# adapting camera parameters
import asyncio
from datetime import datetime
import evdev                   # for input from mouse and command line
from gpiozero import Button    # for external buttons

# load available camera lib (picamera on legacy, picamera2 on newer OS) 
try:
    from picamera import PiCamera  # Import camera functions
    print("Using picamera")
except ImportError:
    pass
try:
    from picamera2 import Picamera2, Preview, MappedArray
    from picamera2.encoders import H264Encoder
    from picamera2.outputs import PyavOutput
    from libcamera import controls, Transform
    print("Using picamera2 and libcamera")
except ImportError:
    pass

# numpy and cv2 are only needed for the overlay text
try:
    import cv2
    print("Using cv2")
    import numpy as np
    print("Using numpy")
except ImportError:
    pass

import os                      # for background OCR and TTS process
import re                      # for parsing fbset output
import signal                  # to kill background process
import faulthandler             # diagnostic Python stack dumps
faulthandler.register(signal.SIGUSR1, all_threads=True)
print('Diagnostics: send SIGUSR1 to dump Python thread stacks.', flush=True) 
import subprocess              # for calling fbset to detect screen resolution and readout
import sys                     # for checking if modules are loaded
import time
import threading

# Latest camera metadata is cached from frame callbacks. Never call
# capture_metadata() synchronously from an asyncio request handler.
latest_camera_metadata = {}
latest_camera_metadata_lock = threading.Lock()
# Video recording state (Picamera2 only).
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 30
VIDEO_BITRATE = 4_000_000

video_encoder = None
video_output = None
video_recording = False
video_started_at = None
video_filename = None

# You can adapt this script to your specific setup, by changing the constants 
# SCALE_FACTORS can be modified for a fixed set of scale factors
# PIN_NUMBER_... can be changed if you connect buttons to different GPIO pins

# Picamera2 needs the screen resolution for preview
# Default is full HD, will try to read actual values in init_camera
SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080

# Rotate view by 180 degrees for the typical use-case with camera behind object
ROTATION = 180

# Increase contrast to make text more readable
# 1 is default, bigger numbers increase contrast
CONTRAST = 1

# Increase brightness for more clarity, 0 is default, range -1..1
BRIGHTNESS = 0.2

# Increase saturation for stronger colours, 1 is default, range 0..32
SATURATION = 1

# Increase sharpness, 1 is default, range 0..16
SHARPNESS = 1

# Distance in cm between camera objective and the surface (e.g. table)
# Adapt this if you have a camera v3 in fixed setup and want to fix the focus
# The default None will run autofocus on each change of magnification
DISTANCE_TO_SURFACE_CM = None # replace with your distance, e.g. 24.5

# Pre-defined scale factors to cycle through with button/enter
# These factors are camera pixels to screen pixels ratio, the actual
# magnification depends also on the camera, the screen size and the distance
# between camera and object
DEFAULT_FACTOR = 1.5
SCALE_FACTORS = [DEFAULT_FACTOR, 3, 4.5, 8]
factor = SCALE_FACTORS[0]  # use first entry as initial factor on boot up

# Language codes for readout, need to be installed on your system
OCR_LANG = 'eng'   # Tesseract's character recognition: eng, deu, spa, fra, ita
TTS_LANG = 'en-GB' # Pico's Text to Speech: en-GB, en-US, de-DE, es-ES, fr-FR, it-IT

AUDIO = 'aplay'
# uncomment next line to get audio via HDMI, see https://forums.raspberrypi.com/viewtopic.php?t=351718
# AUDIO = 'aplay -D sysdefault:CARD=vc4hdmi'

# define GPIO pins for (optional) push buttons
PIN_NUMBER_SCALE =  4 # physical 7, scale button
PIN_NUMBER_COLOR = 18 # physical 12, colour mode button

# some color definitions (as RGB colors), add your own
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
YELLOW = (255, 255, 0)

# Available color modes to step through
# Add, remove or reorder as needed
# For regular (unmodified) colors, use None
COLOR_MODES = [
    None, # normal colors
    ('White on Black', WHITE, BLACK),
    ('Yellow on Blue', YELLOW, BLUE),
    ('Green on Black', GREEN, BLACK),
]

# Enable overlay text for debugging
ENABLE_OVERLAY = False
OVERLAY_DURATION_S = 3

# Readout uses a background process to run OCR and TTS
bg_process = None

# fbset is supported on new and legacy OS
# shows actual framebuffer resolution instead of physical screen size

def hdmi_is_connected():
    """Return True when a DRM HDMI connector reports a connected monitor."""
    import glob

    status_files = sorted(
        glob.glob('/sys/class/drm/card*-HDMI-A-*/status')
    )
    for status_file in status_files:
        try:
            with open(status_file, encoding='ascii') as f:
                if f.read().strip() == 'connected':
                    return True
        except OSError:
            continue
    return False


def screen_resolution_fbset():
    """Return the preferred mode of a connected HDMI monitor.

    A safe 1080p fallback is used only for camera configuration when HDMI
    is absent; it does not cause a preview to be opened on the touchscreen.
    """
    import glob

    for status_file in sorted(
        glob.glob('/sys/class/drm/card*-HDMI-A-*/status')
    ):
        try:
            with open(status_file, encoding='ascii') as f:
                if f.read().strip() != 'connected':
                    continue

            modes_file = status_file.replace('/status', '/modes')
            with open(modes_file, encoding='ascii') as f:
                for line in f:
                    match = re.fullmatch(r'\s*(\d+)x(\d+)\s*', line)
                    if match:
                        width, height = map(int, match.groups())
                        print(f'HDMI resolution detected: {width}x{height}')
                        return width, height
        except (OSError, ValueError) as e:
            print(f'HDMI resolution detection failed: {e}')

    print('No connected HDMI monitor; using 1920x1080 camera configuration.')
    return 1920, 1080


# show text on screen for a duration given in seconds
def overlay(text, duration_s = OVERLAY_DURATION_S):
    global camera

    if ENABLE_OVERLAY and 'numpy' in sys.modules and 'cv2' in sys.modules:
        colour = (255, 0, 0, 255)
        origin = (0, 50)
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 1
        thickness = 2
        buffer = np.zeros((200, 400, 4), dtype=np.uint8)
        cv2.putText(buffer, text, origin, font, scale, colour, thickness)
        camera.set_overlay(buffer)

        if duration_s == 0:
            overlay.content = None
        else:
            ts_now = datetime.timestamp(datetime.now())
            ts_end = ts_now + duration_s
            overlay.content = (text, ts_end)

# Create a colormap / palette as gradient from one color to another
# The colors are given as 8 bit RGB tuple
def colormap(color1, color2):
    palette = np.zeros((256, 1, 3), dtype=np.uint8)
    for i in range(256):
        palette[i, 0, 0] = int(color1[0] + i * (color2[0] - color1[0]) / 255)
        palette[i, 0, 1] = int(color1[1] + i * (color2[1] - color1[1]) / 255)
        palette[i, 0, 2] = int(color1[2] + i * (color2[2] - color1[2]) / 255)
    return palette

# Precompute colour palettes once, rather than rebuilding them every frame.
COLOR_PALETTES = {}

for mode in COLOR_MODES:
    if mode is not None:
        _, dark, light = mode
        COLOR_PALETTES[(dark, light)] = colormap(dark, light)

# called on each frame with picamera2 to modify the colors
def color_mode_callback(request):
    # XRGB8888 uses 4 bytes per pixel.
    if hasattr(color_mode, 'is_inverted') and color_mode.is_inverted:
        with MappedArray(request, "main") as m:
            m.array[..., :3] = 255 - m.array[..., :3]

    elif hasattr(color_mode, 'index') and COLOR_MODES[color_mode.index] is not None:
        name, dark, light = COLOR_MODES[color_mode.index]

        with MappedArray(request, "main") as m:
            # Convert the image to grayscale.
            buffer = cv2.cvtColor(m.array[..., :3], cv2.COLOR_BGR2GRAY)

            heatmap = cv2.applyColorMap(
                buffer,
                COLOR_PALETTES[(dark, light)]
            )

            # Copy the three colour channels into the 4-channel buffer.
            m.array[..., :3] = heatmap

# called on each frame by picamera2 for modifications like color modes
def pre_callback(request):
    # Cache metadata while Picamera2 is delivering frames. This avoids a
    # blocking capture_metadata() call from the asyncio control-server thread.
    try:
        metadata = request.get_metadata()
        with latest_camera_metadata_lock:
            latest_camera_metadata.update(metadata)
    except Exception:
        pass

    color_mode_callback(request)

    if ENABLE_OVERLAY and hasattr(overlay, 'content') and overlay.content != None:
        # remove overlay text if display duration is over
        text, ts_end = overlay.content
        ts_now = datetime.timestamp(datetime.now())
        if ts_now > ts_end:
            overlay('', 0)

def color_mode():
    global camera
    if hasattr(camera, 'image_effect'):
        # in legacy picamera toggle between normal and inverted colours
        camera.image_effect = 'none' if camera.image_effect == 'negative' else 'negative'
    elif 'numpy' in sys.modules and 'cv2' in sys.modules:
        # if opencv is installed with picamera2, step through predefined color modes
        if not hasattr(color_mode, 'index'):
            color_mode.index = 0
        color_mode.index = (color_mode.index + 1) % len(COLOR_MODES)
    else:
        # otherwise invert only (doesn't need opencv)
        if not hasattr(color_mode, 'is_inverted'):
            color_mode.is_inverted = False
        color_mode.is_inverted = not color_mode.is_inverted

    update_frame_rate_for_color_mode()

# react on button pressed
def next_factor():
    global factor
        
    # find the highest entry in SCALE_FACTORS that is <= current factor
    # if the current factor is less due to direct input, switch to default value
    same_or_less = [v for v in SCALE_FACTORS if v <= factor]
    if len(same_or_less) == 0:
        factor = DEFAULT_FACTOR
    else:
        # step to next scale factor, after last entry go back to first
        closest_factor = max(same_or_less)
        i = SCALE_FACTORS.index(closest_factor)
        i_next = (i + 1) % len(SCALE_FACTORS)
        factor = SCALE_FACTORS[i_next]
    
    scale(factor)
    
    
# change to given scale factor
# always start in top left corner to maintain the same reading position
def scale(new_factor):
    global camera
    global factor
    global screen
    
    # ensure that factor is at least 1
    factor = max(1, new_factor)

    # in legacy OS update roi value
    if hasattr(camera, 'crop'):
        # offset and width / height in range [0,1]
        diameter = min(1 / factor, 1)
        camera.crop = (0, 0, diameter, diameter)
        return

    # in current OS compute pixel positions in sensor based on scale factor and screen ratio
    screen_w, screen_h = screen
    screen_ratio = screen_w / screen_h
    x, y, camera_w, camera_h = camera.camera_properties['ScalerCropMaximum']

    crop_w = int(camera_w / factor)
    crop_h = min(int(crop_w / screen_ratio), camera_h)

    window = (x, y, crop_w, crop_h)
    camera.set_controls({'ScalerCrop': window})
    overlay(f'{factor:.2f}')
    
    # focus on cropped area if camera supports autofocus
    if 'AfMode' in camera.camera_controls and DISTANCE_TO_SURFACE_CM is None:
        camera.set_controls({'AfMode': controls.AfModeEnum.Auto, 'AfMetering': controls.AfMeteringEnum.Windows})
        camera.set_controls({'AfWindows': [window]})
        camera.autofocus_cycle()

# change scale factor by given amount
def zoom(change_by):
    global factor
    scale(factor + change_by)

# change brightness by given amount
def brightness(change_by):
    global camera

    if hasattr(camera, 'camera_controls') and 'Brightness' in camera.camera_controls:
        min_val, max_val, def_val = camera.camera_controls['Brightness']
        if not hasattr(brightness, 'val'):
            # init static var with default brightness value
            brightness.val = BRIGHTNESS
        val = brightness.val + change_by
        if min_val <= val <= max_val:
            brightness.val = val
            camera.set_controls({'Brightness': val})
            overlay(f'{val:.2f}')


# multiply current contrast by given value
def contrast(multiply_by):
    global camera

    if hasattr(camera, 'camera_controls') and 'Contrast' in camera.camera_controls:
        min_contrast, max_contrast, def_contrast = camera.camera_controls['Contrast']
        if not hasattr(contrast, 'contrast'):
            # init static var with default contrast value (1)
            contrast.contrast = CONTRAST
        val = contrast.contrast * multiply_by
        if min_contrast <= val <= max_contrast:
            contrast.contrast = val
            camera.set_controls({'Contrast': val})
            overlay(f'{val:.2f}')
    elif hasattr(camera, 'contrast'):
        # legacy picamera, uses range from -100 to 100 so just stepping +-10
        diff = 10 if multiply_by > 1 else -10
        val = camera.contrast + diff
        if -100 <= val <= 100:
            camera.contrast = val

# multiply current saturation by given value
def saturation(multiply_by):
    global camera
    if hasattr(camera, 'camera_controls') and 'Saturation' in camera.camera_controls:
        min_val, max_val, def_val = camera.camera_controls['Saturation']
        if not hasattr(saturation, 'val'):
            # init static var with default value
            saturation.val = SATURATION
        val = saturation.val * multiply_by
        if min_val <= val <= max_val:
            saturation.val = val
            camera.set_controls({'Saturation': val})
            overlay(f'{val:.2f}')

# multiply current sharpness by given value
def sharpness(multiply_by):
    global camera
    if hasattr(camera, 'camera_controls') and 'Sharpness' in camera.camera_controls:
        min_val, max_val, def_val = camera.camera_controls['Sharpness']
        if not hasattr(sharpness, 'val'):
            # init static var with default value
            sharpness.val = SHARPNESS
        val = sharpness.val * multiply_by
        if min_val <= val <= max_val:
            sharpness.val = val
            camera.set_controls({'Sharpness': val})
            overlay(f'{val:.2f}')

# change focus (only on supported cameras like the v3 camera, using picamera2)
# multiply current LensPosition (in dioptrien, i.e. 1/distance_m) by given factor
# if None given, autofocus on whole sensor field regardless of current preview
def focus(multiply_by = None):
    global camera

    # update autofocus (only supported on picamera2)
    if hasattr(camera, 'camera_controls') and 'AfMode' in camera.camera_controls:
        current_val = camera.capture_metadata()['LensPosition']
        if multiply_by is None:
            overlay(f'Auto: {current_val:.2f}')
            camera.set_controls({'AfMode': controls.AfModeEnum.Auto, 'AfMetering': controls.AfMeteringEnum.Auto})
            camera.autofocus_cycle()
            current_val = camera.capture_metadata()['LensPosition']
            overlay(f'Auto: {current_val:.2f}')
        else:
            camera.set_controls({'AfMode': controls.AfModeEnum.Manual})
            val = current_val * multiply_by
            camera.set_controls({'LensPosition': val})
            overlay(f'{val:.2f}')

def quit():
    global devices    
    asyncio.get_event_loop().stop()
    for dev in devices:
        dev.ungrab()

# Shared destination settings written by magni_touch.py.
SAVE_PATHS_FILE = os.path.expanduser("~/.config/magni/paths.json")
DEFAULT_IMAGE_SAVE_PATH = "/home/pi"
DEFAULT_VIDEO_SAVE_PATH = "/home/pi/Videos/Magni"

def load_save_paths():
    """Read the current photo/video folders from the shared settings file."""
    paths = {
        "image_save_path": DEFAULT_IMAGE_SAVE_PATH,
        "video_save_path": DEFAULT_VIDEO_SAVE_PATH,
    }
    try:
        with open(SAVE_PATHS_FILE, "r", encoding="utf-8") as config_file:
            saved = json.load(config_file)
        if isinstance(saved, dict):
            for key in paths:
                value = saved.get(key)
                if isinstance(value, str) and value.strip():
                    paths[key] = os.path.expanduser(value.strip())
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return paths

def save_photo(filename = ''):
    global camera
    if len(filename) == 0:
        image_dir = load_save_paths()["image_save_path"]
        os.makedirs(image_dir, exist_ok=True)
        timestamp = datetime.now().isoformat()
        filename = os.path.join(image_dir, f'{timestamp}.jpg')
    if hasattr(camera, 'capture_file'):
        # saves the preview stream as an image
        camera.capture_file(filename)
    else:
        camera.stop_preview()
        camera.capture(filename)
        camera.start_preview()

def start_video_recording():
    """Start recording the clean camera stream to a timestamped MP4."""
    global video_encoder, video_output
    global video_recording, video_started_at, video_filename

    if video_recording:
        return

    if not hasattr(camera, "start_recording"):
        raise RuntimeError("Video recording requires Picamera2.")

    # Use the folder selected in touchscreen Settings.
    video_dir = load_save_paths()["video_save_path"]
    os.makedirs(video_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    video_filename = os.path.join(video_dir, f"Magni_{timestamp}.mp4")

    try:
        video_encoder = H264Encoder(
            bitrate=VIDEO_BITRATE,
            framerate=VIDEO_FPS,
        )
        video_output = PyavOutput(video_filename)

        camera.start_encoder(
            video_encoder,
            video_output,
            name="lores",
        )

        video_started_at = time.monotonic()
        video_recording = True
        print(f"Started video recording: {video_filename}")

    except Exception:
        video_encoder = None
        video_output = None
        video_recording = False
        video_started_at = None
        video_filename = None
        raise


def stop_video_recording():
    """Stop recording and finalize the MP4 file."""
    global video_encoder, video_output
    global video_recording, video_started_at

    if not video_recording:
        return

    try:
        camera.stop_encoder()
        print(f"Saved video recording: {video_filename}")
    finally:
        video_recording = False
        video_started_at = None
        video_encoder = None
        video_output = None

def readout():
    global factor
    global bg_process

    # remove hyphens at end of line and append the next line, so TTS won't read them out
    # See https://unix.stackexchange.com/a/26289
    FIX_HYPHENS = r"perl -i.original -p0e 's/(\w)-[\n]+(\w)/$1$2/igs' tmp.txt"

    # command to run OCR, remove hyphens, play a sound, run TTS and play the result
    cmd = f'tesseract tmp.jpg tmp -l {OCR_LANG} && {FIX_HYPHENS} && {AUDIO} plop.wav && pico2wave -w tmp.wav -l {TTS_LANG} < tmp.txt && {AUDIO} tmp.wav'
    overlay('')
    if bg_process != None and bg_process.poll() == None:
        # if background process is running, just kill it and do nothing
        os.killpg(os.getpgid(bg_process.pid), signal.SIGTERM)
    else:
        subprocess.call(f'{AUDIO} plop.wav', shell=True)
        save_photo('tmp.jpg')
        overlay('Reading')
        bg_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True, preexec_fn=os.setsid)

# start displaying the default camera view
def init_camera(width, height, preview_enabled=True):
    try:
        # for current OS use picamera2
        picam2 = Picamera2()
        transform = Transform(hflip=1, vflip=1) if ROTATION == 180 else Transform()
        config = picam2.create_preview_configuration(
            # Main stream: touchscreen preview and colour processing.
            main={'size': (width, height), 'format': 'XRGB8888'},

            # Separate clean stream: video recording only.
            lores={
                'size': (VIDEO_WIDTH, VIDEO_HEIGHT),
                'format': 'YUV420',
            },

            transform=transform,
            controls={'FrameDurationLimits': (33333, 33333)},
        )
        picam2.configure(config)
        print("Configured camera:", picam2.camera_configuration())
        picam2.pre_callback = pre_callback
        if preview_enabled:
            picam2.start_preview(
                Preview.QTGL, x=0, y=0, width=width, height=height
            )  # no transform!
        picam2.start()
        picam2.set_controls({
            'Brightness': BRIGHTNESS,
            'Contrast': CONTRAST,
            'Saturation': SATURATION,
            'Sharpness': SHARPNESS
        })
        if 'AfMode' in picam2.camera_controls:
            if DISTANCE_TO_SURFACE_CM is None:
                # if no distance given, use autofocus on magnification change
                picam2.set_controls({'AfMode': controls.AfModeEnum.Auto})
                picam2.set_controls({'AfSpeed': controls.AfSpeedEnum.Fast})
            else:
                # set focus to the given fixed distance
                picam2.set_controls({'AfMode': controls.AfModeEnum.Manual})
                picam2.set_controls({'LensPosition': 100 / DISTANCE_TO_SURFACE_CM})

        print('Started picamera2', ROTATION)
        print('FrameDurationLimits supported:',
            picam2.camera_controls.get('FrameDurationLimits', 'Not available'))
        print('Configured frame duration:',
            picam2.camera_configuration()['controls'].get('FrameDurationLimits', 'Not reported'))
        return picam2
    except:
        # for legacy OS use picamera
        camera = PiCamera()
        camera.rotation = ROTATION
        if preview_enabled:
            camera.start_preview()
        print('Started legacy picamera', ROTATION)
        return camera



# HDMI preview management -------------------------------------------------
# The touchscreen is reserved for Magni controls. Never let the camera
# preview fall back onto DSI-1 when the HDMI monitor is switched off.
camera_preview_active = False


def stop_camera_preview():
    """Stop the current preview window, but leave camera capture running."""
    global camera_preview_active

    if not camera_preview_active:
        return

    try:
        camera.stop_preview()
        print("Camera preview stopped: HDMI disconnected.")
    except Exception as e:
        # Keep the monitor task alive even if Picamera2 reports that the
        # preview was already detached during HDMI hot-unplug.
        print(f"Warning: could not stop camera preview cleanly: {e}")
    finally:
        camera_preview_active = False


def start_camera_preview(width, height, x=0, y=0):
    """Create a fresh preview window on the HDMI output."""
    global camera_preview_active

    if camera_preview_active:
        return

    if hasattr(camera, "camera_configuration"):  # Picamera2
        camera.start_preview(
            Preview.QTGL, x=x, y=y, width=width, height=height
        )
    else:  # Legacy PiCamera
        camera.start_preview()

    camera_preview_active = True
    print(
        f"Camera preview started for HDMI at ({x},{y}) "
        f"with size {width}x{height}."
    )



def ensure_labwc_hdmi_preview_rule():
    """Ensure Labwc sends Picamera2 Preview windows fullscreen to HDMI-A-1.

    Back up rc.xml only when a change is actually needed.
    """
    from pathlib import Path
    import shutil
    from datetime import datetime as _datetime

    config = Path.home() / ".config" / "labwc" / "rc.xml"
    if not config.exists():
        print(f"Labwc config not found; skipping automatic preview rule: {config}",
              flush=True)
        return

    try:
        original = config.read_text()
    except Exception as exc:
        print(f"Could not read Labwc config; leaving it unchanged: {exc}",
              flush=True)
        return

    rule_pattern = r'<windowRule\s+title="\*Preview\*"\s*>(.*?)</windowRule>'
    matches = list(re.finditer(rule_pattern, original,
                               flags=re.IGNORECASE | re.DOTALL))
    for match in matches:
        body = match.group(1)
        move_ok = re.search(
            r'<action\s+name="MoveToOutput"\s+output="HDMI-A-1"\s*/>',
            body, flags=re.IGNORECASE)
        fullscreen_ok = re.search(
            r'<action\s+name="ToggleFullscreen"\s*/>',
            body, flags=re.IGNORECASE)
        if move_ok and fullscreen_ok and move_ok.start() < fullscreen_ok.start():
            print("Labwc HDMI fullscreen preview rule already configured; no change needed.",
                  flush=True)
            return

    rule = (
        '<windowRule title="*Preview*">\n'
        '      <action name="MoveToOutput" output="HDMI-A-1" />\n'
        '      <action name="ToggleFullscreen" />\n'
        '    </windowRule>'
    )

    updated = re.sub(rule_pattern, '', original,
                     flags=re.IGNORECASE | re.DOTALL)

    if re.search(r'</windowRules\s*>', updated, flags=re.IGNORECASE):
        updated = re.sub(
            r'</windowRules\s*>',
            '  ' + rule + '\n  </windowRules>',
            updated, count=1, flags=re.IGNORECASE)
    elif re.search(r'</labwc_config\s*>', updated, flags=re.IGNORECASE):
        block = '<windowRules>\n    ' + rule + '\n  </windowRules>\n'
        updated = re.sub(
            r'</labwc_config\s*>',
            block + '</labwc_config>',
            updated, count=1, flags=re.IGNORECASE)
    else:
        print("Could not find a Labwc windowRules or labwc_config closing tag; "
              "configuration left unchanged.", flush=True)
        return

    backup = config.with_name(
        f"rc.xml.magni-backup-{_datetime.now():%Y%m%d-%H%M%S}")
    try:
        shutil.copy2(config, backup)
        config.write_text(updated)
    except Exception as exc:
        print(f"Could not update Labwc config: {exc}", flush=True)
        print("Previous config backup (if created): " + str(backup), flush=True)
        return

    print("Installed Labwc rule: move Preview windows to HDMI-A-1 and fullscreen.",
          flush=True)
    print(f"Previous Labwc config backed up to: {backup}", flush=True)
    print("Restart Labwc or reboot for the updated rule to take effect.",
          flush=True)

def get_hdmi_layout():
    """Return HDMI output position once Labwc has enabled the output."""
    try:
        result = subprocess.run(
            ["wlr-randr"], capture_output=True, text=True,
            timeout=3, check=True
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"Could not query wlr-randr: {e}")
        return None

    blocks = re.split(r"(?m)(?=^[^\s].*\(HDMI-A-\d+\))", result.stdout)
    for block in blocks:
        if "(HDMI-A-" not in block:
            continue
        enabled = re.search(r"(?m)^\s*Enabled:\s*(yes|no)", block)
        position = re.search(r"(?m)^\s*Position:\s*(-?\d+),(-?\d+)", block)
        if enabled and enabled.group(1) == "yes" and position:
            return tuple(map(int, position.groups()))
    return None


async def monitor_hdmi():
    """Monitor HDMI hotplug with explicit, flushed diagnostics."""
    print("HDMI monitor: task started.", flush=True)

    last_connector_state = None
    last_heartbeat = time.monotonic()

    while True:
        try:
            connected = hdmi_is_connected()

            # Log every state transition, independently of preview state.
            if connected != last_connector_state:
                print(
                    f"HDMI monitor: connector state changed to "
                    f"{'CONNECTED' if connected else 'DISCONNECTED'}. "
                    f"preview_active={camera_preview_active}",
                    flush=True,
                )
                last_connector_state = connected

            if not connected:
                if camera_preview_active:
                    print("HDMI monitor: stopping preview.", flush=True)
                    stop_camera_preview()

            elif connected and not camera_preview_active:
                print(
                    "HDMI monitor: HDMI is connected but preview is inactive; "
                    "waiting for Labwc layout.",
                    flush=True,
                )

                layout = None
                for attempt in range(15):
                    await asyncio.sleep(1)

                    if not hdmi_is_connected():
                        print(
                            "HDMI monitor: HDMI disconnected during layout wait.",
                            flush=True,
                        )
                        break

                    layout = get_hdmi_layout()
                    print(
                        f"HDMI monitor: layout check {attempt + 1}/15: {layout}",
                        flush=True,
                    )

                    if layout is not None:
                        # Allow Labwc to finish applying the output geometry.
                        await asyncio.sleep(1)
                        break

                if not hdmi_is_connected():
                    continue

                if layout is None:
                    print(
                        "HDMI monitor: HDMI is connected, but Labwc has not "
                        "reported an enabled HDMI position; will retry.",
                        flush=True,
                    )
                else:
                    x, y = layout
                    width, height = screen_resolution_fbset()
                    print(
                        f"HDMI monitor: attempting preview start at "
                        f"({x},{y}), {width}x{height}.",
                        flush=True,
                    )
                    try:
                        start_camera_preview(width, height, x=x, y=y)
                    except Exception as e:
                        print(
                            f"HDMI monitor: preview start FAILED: "
                            f"{type(e).__name__}: {e}",
                            flush=True,
                        )
                    else:
                        print(
                            f"HDMI monitor: preview start returned; "
                            f"camera_preview_active={camera_preview_active}.",
                            flush=True,
                        )

            now = time.monotonic()
            if now - last_heartbeat >= 10:
                print(
                    f"HDMI monitor heartbeat: connected={connected}, "
                    f"preview_active={camera_preview_active}.",
                    flush=True,
                )
                last_heartbeat = now

        except Exception as e:
            print(
                f"HDMI monitor exception (will retry): "
                f"{type(e).__name__}: {e}",
                flush=True,
            )

        await asyncio.sleep(1)


# MAGNI_CONTROL_SERVER
import json
import socket

def get_magni_status():
    """Return current magnification, camera settings and metadata."""
    status = {}

    # Magnification
    try:
        status["magnification"] = float(factor)
    except (NameError, TypeError, ValueError):
        status["magnification"] = None

    # Actual camera stream resolution
    try:
        config = camera.camera_configuration()
        size = config["main"]["size"]
        status["resolution"] = f"{size[0]} x {size[1]}"
    except (AttributeError, KeyError, TypeError, NameError):
        status["resolution"] = "Unavailable"

    # Read the current application values maintained by the controls.
    def current_value(function, attribute, default=None):
        return getattr(function, attribute, default)

    status["brightness"] = current_value(
        brightness, "val", BRIGHTNESS
    )
    status["contrast"] = current_value(
        contrast, "contrast", CONTRAST
    )
    status["saturation"] = current_value(
        saturation, "val", SATURATION
    )
    status["sharpness"] = current_value(
        sharpness, "val", SHARPNESS
    )

    # Colour mode
    if hasattr(color_mode, "index"):
        index = color_mode.index
        try:
            mode = COLOR_MODES[index]
            status["colour_mode"] = (
                "Normal" if mode is None else str(mode[0])
            )
        except (IndexError, TypeError):
            status["colour_mode"] = f"Mode {index}"
    elif hasattr(color_mode, "is_inverted"):
        status["colour_mode"] = (
            "Inverted" if color_mode.is_inverted else "Normal"
        )
    elif hasattr(camera, "image_effect"):
        status["colour_mode"] = str(camera.image_effect)
    else:
        status["colour_mode"] = "Unknown"

    # Camera metadata
    # Autofocus capability
    try:
        available_controls = camera.camera_controls
        status["autofocus_supported"] = (
            "AfMode" in available_controls
            and "AfTrigger" in available_controls
        )
    except (AttributeError, TypeError):
        # Legacy PiCamera and cameras without reported AF controls.
        status["autofocus_supported"] = False
    
    # Read the most recently delivered frame metadata from the cache.
    # capture_metadata() is blocking and must not run in this asyncio handler.
    with latest_camera_metadata_lock:
        metadata = dict(latest_camera_metadata)

    exposure = metadata.get("ExposureTime")
    gain = metadata.get("AnalogueGain")
    lens = metadata.get("LensPosition")
    frame_duration = metadata.get("FrameDuration")

    status["exposure_us"] = exposure
    status["analogue_gain"] = round(float(gain), 5) if gain is not None else None
    status["lens_position"] = lens
    status["fps"] = (
        round(1_000_000 / frame_duration, 1)
        if frame_duration else None
    )

    status["slow_frame_rate"] = slow_frame_rate_enabled
    status["slow_frame_rate_active"] = slow_frame_rate_active
    
    # Detect whether the camera supports autofocus.
    try:
        controls_available = camera.camera_controls
        status["autofocus_supported"] = (
            "AfMode" in controls_available
            and "AfTrigger" in controls_available
        )
    except (AttributeError, TypeError):
        # Legacy camera API or camera without reported AF controls.
        status["autofocus_supported"] = False

    # Video recording status
    status["video_recording"] = video_recording
    status["video_filename"] = video_filename

    status["video_elapsed"] = (
        int(time.monotonic() - video_started_at)
        if video_recording and video_started_at is not None
        else 0
    )
    return status

# False = normal 30 FPS; True = reduced 10 FPS.
# Whether automatic slow frame rate is enabled in Settings.
slow_frame_rate_enabled = False

# Whether the camera is currently running at the reduced frame rate.
slow_frame_rate_active = False

def set_frame_rate_mode(slow):
    """Set the camera frame rate without restarting the camera."""
    global slow_frame_rate_active

    frame_duration = 100000 if slow else 33333

    camera.set_controls({
        "FrameDurationLimits": (frame_duration, frame_duration)
    })

    slow_frame_rate_active = slow

    print(
        "Frame rate:",
        "10 FPS mode" if slow else "30 FPS mode"
    )


def update_frame_rate_for_color_mode():
    """Use reduced FPS for processed colours when enabled in Settings."""
    current_index = getattr(color_mode, "index", 0)
    is_normal_colour = current_index == 0

    use_slow_rate = (
        slow_frame_rate_enabled and not is_normal_colour
    )

    if use_slow_rate != slow_frame_rate_active:
        set_frame_rate_mode(use_slow_rate)


def configure_slow_frame_rate(enabled):
    """Enable or disable automatic slow frame-rate behaviour."""
    global slow_frame_rate_enabled

    slow_frame_rate_enabled = enabled
    update_frame_rate_for_color_mode()

async def magni_control_client(reader, writer):
    try:
        line = await reader.readline()
        command = line.decode("utf-8").strip()
        actions = {
            "zoom_in": lambda: zoom(0.5),
            "zoom_out": lambda: zoom(-0.5),
            "brightness_up": lambda: brightness(0.1),
            "brightness_down": lambda: brightness(-0.1),
            "contrast_up": lambda: contrast(2),
            "contrast_down": lambda: contrast(0.5),
            "saturation_up": lambda: saturation(2),
            "saturation_down": lambda: saturation(0.5),
            "sharpness_up": lambda: sharpness(2),
            "sharpness_down": lambda: sharpness(0.5),
            "focus_auto": lambda: focus(),
            "next_color": lambda: color_mode(),
            "slow_rate_on": lambda: configure_slow_frame_rate(True),
            "slow_rate_off": lambda: configure_slow_frame_rate(False),
            "readout": lambda: readout(),
            "save_photo": lambda: save_photo(),
            "start_recording": lambda: start_video_recording(),
            "stop_recording": lambda: stop_video_recording(),
        }
        if command == "get_status":
            writer.write((json.dumps(get_magni_status()) + "\n").encode("utf-8")
            )
        elif command in actions:
            actions[command]()
            writer.write(b"OK\n")
        else:
            writer.write(b"ERROR: unknown command\n")
        await writer.drain()
    except Exception as e:
        try:
            writer.write(("ERROR: " + str(e) + "\n").encode())
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        await writer.wait_closed()

async def start_magni_control_server():
    server = await asyncio.start_server(
        magni_control_client, "127.0.0.1", 8765
    )
    print("Magni control server listening on 127.0.0.1:8765")
    return server


async def handle_events(device):
    async for event in device.async_read_loop():
        if event.type == evdev.ecodes.EV_KEY and event.value == 0:
            code = event.code
            modifiers = device.active_keys()
            is_shift = evdev.ecodes.KEY_LEFTSHIFT in modifiers or evdev.ecodes.KEY_RIGHTSHIFT in modifiers

            # mouse buttons
            if code == evdev.ecodes.BTN_MOUSE: next_factor()
            elif code == evdev.ecodes.BTN_RIGHT: color_mode()
            elif code == evdev.ecodes.BTN_MIDDLE: readout()

            # regular keys
            elif code == evdev.ecodes.KEY_A: focus()
            elif code == evdev.ecodes.KEY_F and is_shift: focus(0.75)
            elif code == evdev.ecodes.KEY_F: focus(1.5)
            elif code == evdev.ecodes.KEY_Q: quit()
            elif code == evdev.ecodes.KEY_ESC: quit()
            elif code == evdev.ecodes.KEY_ENTER: next_factor()
            elif code == evdev.ecodes.KEY_SLASH: color_mode()
            elif code == evdev.ecodes.KEY_S: save_photo()
            elif code == evdev.ecodes.KEY_R: readout()
            elif code == evdev.ecodes.KEY_Z and is_shift: zoom(-0.2)
            elif code == evdev.ecodes.KEY_Z: zoom(0.2)
            elif code == evdev.ecodes.KEY_B and is_shift: brightness(-0.1)
            elif code == evdev.ecodes.KEY_B: brightness(0.1)
            elif code == evdev.ecodes.KEY_C and is_shift: contrast(0.5)
            elif code == evdev.ecodes.KEY_C: contrast(2)
            elif code == evdev.ecodes.KEY_H and is_shift: sharpness(0.5)
            elif code == evdev.ecodes.KEY_H: sharpness(2)
            elif code == evdev.ecodes.KEY_T and is_shift: saturation(0.5)
            elif code == evdev.ecodes.KEY_T: saturation(2)
            elif code == evdev.ecodes.KEY_0: scale(10)
            elif code == evdev.ecodes.KEY_1: scale(1)
            elif code == evdev.ecodes.KEY_2: scale(2)
            elif code == evdev.ecodes.KEY_3: scale(3)
            elif code == evdev.ecodes.KEY_4: scale(4)
            elif code == evdev.ecodes.KEY_5: scale(5)
            elif code == evdev.ecodes.KEY_6: scale(6)
            elif code == evdev.ecodes.KEY_7: scale(7)
            elif code == evdev.ecodes.KEY_8: scale(8)
            elif code == evdev.ecodes.KEY_9: scale(9)

            # numeric keypad
            elif code == evdev.ecodes.KEY_KPENTER: next_factor()
            elif code == evdev.ecodes.KEY_KPSLASH: color_mode()
            elif code == evdev.ecodes.KEY_KP0: scale(10)
            elif code == evdev.ecodes.KEY_KP1: scale(1)
            elif code == evdev.ecodes.KEY_KP2: scale(2)
            elif code == evdev.ecodes.KEY_KP3: scale(3)
            elif code == evdev.ecodes.KEY_KP4: scale(4)
            elif code == evdev.ecodes.KEY_KP5: scale(5)
            elif code == evdev.ecodes.KEY_KP6: scale(6)
            elif code == evdev.ecodes.KEY_KP7: scale(7)
            elif code == evdev.ecodes.KEY_KP8: scale(8)
            elif code == evdev.ecodes.KEY_KP9: scale(9)

# Keep Labwc's HDMI preview placement rule in sync at every startup.
ensure_labwc_hdmi_preview_rule()

button1 = Button(PIN_NUMBER_SCALE)
button1.when_pressed = next_factor
button2 = Button(PIN_NUMBER_COLOR)
button2.when_pressed = color_mode

screen = screen_resolution_fbset()
width, height = screen
print(f"Camera preview resolution: {width}x{height}")

hdmi_connected_at_start = hdmi_is_connected()
camera = init_camera(width, height, preview_enabled=hdmi_connected_at_start)
camera_preview_active = hdmi_connected_at_start
#width, height = 1920, 1080
#screen = (width, height)
#camera = init_camera(width, height)
scale(factor)

try:
    devices = [evdev.InputDevice(fn) for fn in evdev.list_devices()]
    for device in devices:
        # Leave the touchscreen available to Labwc and Qt.
        if "Goodix Capacitive TouchScreen" in device.name:
            print(f"Leaving touchscreen ungrabbed: {device.name}")
            device.close()
            continue
        device.grab()
        asyncio.ensure_future(handle_events(device))
    loop = asyncio.get_event_loop()
    control_server = loop.run_until_complete(start_magni_control_server())
    asyncio.ensure_future(monitor_hdmi())
    try:
        loop.run_forever()
    finally:
        control_server.close()
        loop.run_until_complete(control_server.wait_closed())

finally:
    if bg_process != None and bg_process.poll() == None:
        os.killpg(os.getpgid(bg_process.pid), signal.SIGTERM)
    camera.stop_preview()
    camera.close()
