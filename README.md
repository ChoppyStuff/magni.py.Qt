# Magni.py Qt — Touchscreen Edition

A customised version of Magni.py Qt for a Raspberry Pi, adding a dedicated touchscreen interface for controlling the magnifier.  The original Magni.py source is included here for comparison, but has been replaced with **magni-qt.py** and **magni_touch.py**.  The original OCR code is currently disabled, and can easily be reinstated.

<img width="1277" height="717" alt="Screenshot from 2026-10-25 14-24-02" src="https://github.com/user-attachments/assets/a03be5e6-d17d-4f7d-a21e-7fa77c8cfb28" />


## Features

- Touchscreen control panel for magnification and camera adjustments.
- Colour modes, including white on black, yellow on blue, and green on black.
- Live camera status and optional advanced camera information.
- Touchscreen rotation and persistent user settings.
- Configurable reduced frame rate for colour processing.
- Touchscreen shutdown control.

The touchscreen application communicates with the main camera application through a local control interface. The HDMI display is used for the magnified camera image, while the touchscreen provides the controls.

## Hardware and OS

Designed for a Raspberry Pi running Raspberry Pi OS, with:

- Raspberry PI.  This was developed on a Raspberry Pi 3 with 1Gb memory.  This should be considered an absolute minimum spec. 
- A compatible Raspberry Pi camera, tested with the Camera Module v2.1 (IMX219).
- An HDMI display for the magnified image.
- A DSI touchscreen for the control interface. Designed on 5", should work fine on other sizes.
- A Wayland desktop environment using Labwc.

The touchscreen code uses PyQt5. Display rotation relies on `wlr-randr`, and the applications require access to the camera and any configured GPIO or input devices.

## OS Configuration

The following OS-level configuration is required:

1. **Display setup:** Configure the HDMI display and DSI touchscreen as separate outputs. The touchscreen interface expects the touchscreen output to be named `DSI-1`.
2. **Python dependencies:** Install the dependencies required by the camera application and touchscreen interface, including Picamera2, OpenCV, NumPy, PyQt5, and the relevant GPIO and input-device libraries.
3. **Device permissions:** Ensure the application user has the permissions required to access the camera, GPIO controls, and input devices used by the installation.
4. **Shutdown permission:** If touchscreen shutdown is enabled, configure a narrowly scoped `sudoers` rule allowing the application user to execute the system shutdown command without a password. Do not grant unrestricted passwordless sudo.
5. **Startup:** Configure the system to start both the main Magni application and the touchscreen control application in the appropriate graphical session.

Exact package names and startup configuration may vary with the Raspberry Pi OS release.

## Main Files

- `magni-qt.py` — Camera preview, magnification, image processing, controls, and local control interface.
- `magni_touch.py` — Touchscreen interface, settings, camera adjustments, status display, rotation, and shutdown.

## Notes

The reduced-frame-rate setting is intended to improve responsiveness when using processed colour modes. Actual performance depends on the Raspberry Pi, camera resolution, and processing workload.
