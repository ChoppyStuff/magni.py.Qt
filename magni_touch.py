#!/usr/bin/env python3

import socket
import subprocess
import sys
import os
import time
import json
import shutil

from PyQt5.QtCore import Qt, QTimer, QSettings
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QBoxLayout,
    QPushButton,
    QLabel,
    QSlider,
    QMessageBox,
    QGridLayout,
    QFrame,
    QCheckBox
)

HOST = "127.0.0.1"
PORT = 8765

def get_camera_status():
    """Fetch camera status JSON from the main application."""
    try:
        with socket.create_connection((HOST, PORT), timeout=2) as sock:
            sock.sendall(b"get_status\n")

            with sock.makefile("r", encoding="utf-8") as response:
                line = response.readline()

        data = json.loads(line)

        if isinstance(data, dict):
            return data

    except (OSError, ValueError, json.JSONDecodeError):
        pass

    return None

def send_command(command):
    """Send one existing command to the Magni control server."""
    try:
        with socket.create_connection((HOST, PORT), timeout=3) as sock:
            sock.settimeout(5)
            sock.sendall((command + "\n").encode())
            response = b""

            while not response.endswith(b"\n"):
                chunk = sock.recv(256)
                if not chunk:
                    break
                response += chunk

        result = response.decode().strip() or "No response"
        status.setText(result)
        return result == "OK"

    except Exception as exc:
        status.setText("Connection error: " + str(exc))
        return False


def shutdown_pi():
    """Show a large touchscreen confirmation before shutting down."""

    dialog = QDialog(window)
    dialog.setWindowTitle("Shut Down Raspberry Pi")
    dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    dialog.setWindowModality(Qt.ApplicationModal)

    dialog.setStyleSheet("""
        QDialog {
            background: #202020;
            color: white;
        }

        QLabel {
            color: white;
            font-size: 26px;
        }

        QPushButton {
            min-height: 100px;
            font-size: 27px;
            font-weight: bold;
            border: 3px solid #aaaaaa;
            border-radius: 15px;
            padding: 12px;
        }
    """)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 35, 24, 35)
    layout.setSpacing(30)

    title = QLabel("SHUT DOWN RASPBERRY PI?")
    title.setAlignment(Qt.AlignCenter)
    title.setWordWrap(True)
    title.setStyleSheet(
        "font-size: 32px; font-weight: bold;"
    )
    layout.addWidget(title)

    message = QLabel(
        "Are you sure you want to switch off the Raspberry Pi?\n\n"
        "Magni and the camera will stop working until you "
        "power the Pi on again."
    )
    message.setAlignment(Qt.AlignCenter)
    message.setWordWrap(True)
    layout.addWidget(message)

    layout.addStretch()

    cancel_button = QPushButton("CANCEL")
    cancel_button.setStyleSheet("""
        QPushButton {
            background: #383838;
            color: white;
        }
        QPushButton:pressed {
            background: #555555;
        }
    """)

    shutdown_button = QPushButton("SHUT DOWN")
    shutdown_button.setStyleSheet("""
        QPushButton {
            background: #a51d25;
            color: white;
            border-color: #ff7777;
        }
        QPushButton:pressed {
            background: #6f1017;
        }
    """)

    layout.addWidget(cancel_button)
    layout.addWidget(shutdown_button)

    cancel_button.clicked.connect(dialog.reject)
    shutdown_button.clicked.connect(dialog.accept)

    # Display the dialog full-screen on the touchscreen.
    dialog.setGeometry(window.geometry())
    dialog.showFullScreen()

    if dialog.exec_() != QDialog.Accepted:
        status.setText("Shutdown cancelled")
        return

    status.setText("Shutting down...")

    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            error = result.stderr.strip() or "Shutdown command failed"
            status.setText(error)
            QMessageBox.critical(window, "Shutdown failed", error)

    except Exception as exc:
        status.setText("Shutdown error: " + str(exc))
        QMessageBox.critical(window, "Shutdown error", str(exc))

app = QApplication(sys.argv)
app.setApplicationName("Magni Touch Controls")

window = QWidget()
window.setWindowTitle("Magni Touch Controls")

window.setStyleSheet("""
    QWidget {
        background: #202020;
        color: white;
    }

    QLabel {
        font-size: 21px;
    }

    QLabel#title {
        font-size: 29px;
        font-weight: bold;
        padding: 8px;
    }

    QLabel#sliderLabel {
        font-size: 21px;
        font-weight: bold;
        padding-top: 4px;
    }

    QPushButton {
        background: #383838;
        color: white;
        border: 2px solid #999;
        border-radius: 12px;
        font-size: 21px;
        font-weight: bold;
        min-height: 64px;
        padding: 5px;
    }

    QPushButton:pressed {
        background: #0066aa;
    }

    QSlider {
        min-height: 65px;
    }

    QSlider::groove:horizontal {
        height: 18px;
        background: #777;
        border-radius: 9px;
    }

    QSlider::sub-page:horizontal {
        background: #1594e8;
        border-radius: 9px;
    }

    QSlider::add-page:horizontal {
        background: #555;
        border-radius: 9px;
    }

    QSlider::handle:horizontal {
        background: white;
        border: 4px solid #1594e8;
        width: 42px;
        margin: -16px 0;
        border-radius: 12px;
    }

    QDialog {
        background: #202020;
        color: white;
    }
""")

layout = QVBoxLayout(window)
layout.setContentsMargins(14, 12, 14, 12)
layout.setSpacing(8)

# Header title with a settings gear at the top-right.
header = QHBoxLayout()
header.setContentsMargins(0, 0, 0, 0)
title = QLabel("MAGNI CONTROLS")
title.setObjectName("title")
title.setAlignment(Qt.AlignCenter)
header.addWidget(title, 1)

# Rotate the touchscreen through 0, 90, 180 and 270 degrees.
rotation_button = QPushButton("⟳")
rotation_button.setAccessibleName("Rotate display")
rotation_button.setToolTip("Rotate touchscreen (currently 0°)")
rotation_button.setFixedSize(54, 54)
rotation_button.setStyleSheet("""
    QPushButton {
        font-size: 34px;
        padding: 0;
        border-radius: 10px;
    }
""")
header.addWidget(rotation_button, 0, Qt.AlignRight | Qt.AlignTop)

settings_button = QPushButton("⚙")
settings_button.setAccessibleName("Settings")
settings_button.setToolTip("Settings")
settings_button.setFixedSize(54, 54)
settings_button.setStyleSheet("""
    QPushButton {
        font-size: 30px;
        padding: 0;
        border-radius: 10px;
    }
""")
header.addWidget(settings_button, 0, Qt.AlignRight | Qt.AlignTop)
layout.addLayout(header)

# Responsive centre area: controls and information side by side in landscape,
# stacked in portrait. Status and action buttons remain below it.
body_layout = QBoxLayout(QBoxLayout.LeftToRight)
body_layout.setContentsMargins(0, 0, 0, 0)
body_layout.setSpacing(12)
layout.addLayout(body_layout, 1)

controls_panel = QWidget()
controls_layout = QGridLayout(controls_panel)
controls_layout.setContentsMargins(4, 4, 4, 4)
controls_layout.setHorizontalSpacing(12)
controls_layout.setVerticalSpacing(2)
body_layout.addWidget(controls_panel, 3)

preferences = QSettings("Magni", "MagniTouch")
advanced_info_enabled = preferences.value(
    "advanced_info_enabled", False, type=bool
)
slow_frame_rate_enabled = preferences.value(
    "slow_frame_rate_enabled", False, type=bool
)
display_rotation = preferences.value("display_rotation", 0, type=int)
if display_rotation not in (0, 90, 180, 270):
    display_rotation = 0


def apply_display_rotation(angle):
    """Apply a rotation to the DSI touchscreen output."""
    transform = "normal" if angle == 0 else str(angle)
    try:
        result = subprocess.run(
            ["wlr-randr", "--output", "DSI-1", "--transform", transform],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or "wlr-randr failed"
            status.setText(f"Display rotation failed: {error}")
            return False
        return True
    except (OSError, subprocess.TimeoutExpired) as exc:
        status.setText(f"Display rotation failed: {exc}")
        return False


def touchscreen_screen():
    """Find DSI-1 by its position in the multi-display desktop."""
    # Qt reports the Wayland output names directly (confirmed as DSI-1).
    # Use the identity, not the geometry, because geometry changes on rotation.
    for screen in app.screens():
        if screen.name() == "DSI-1":
            return screen

    # Do not silently fall back to HDMI: that would put controls on the
    # magnifier preview. Return None so we can report the issue instead.
    return None


def position_window_on_touchscreen():
    """Place the window on DSI-1 before requesting fullscreen."""
    screen = touchscreen_screen()
    if screen is None:
        status.setText("Touchscreen DSI-1 not detected; not opening controls")
        return

    try:
        # Set the DSI output geometry while the window is still hidden.
        # On Wayland, fullscreen placement can be fixed when fullscreen is
        # requested, so do not fullscreen first and attempt to move it later.
        window.setGeometry(screen.geometry())
        handle = window.windowHandle()
        if handle is not None:
            handle.setScreen(screen)
        window.showFullScreen()
    except Exception as exc:
        status.setText(f"Could not position controls on touchscreen: {exc}")


def rotate_display():
    """Cycle orientation, save it, and keep the controls on the touchscreen."""
    global display_rotation
    next_rotation = (display_rotation + 90) % 360
    if not apply_display_rotation(next_rotation):
        return
    display_rotation = next_rotation
    preferences.setValue("display_rotation", display_rotation)
    rotation_button.setToolTip(f"Rotate touchscreen (currently {display_rotation}°)")
    status.setText(f"Touchscreen rotation: {display_rotation}°")
    # Allow Labwc/Qt to process the updated output geometry before repositioning.
    QTimer.singleShot(500, position_window_on_touchscreen)


status = QLabel("Ready")
status.setAlignment(Qt.AlignCenter)
status.setWordWrap(True)
status.setMaximumHeight(42)
layout.addWidget(status)

# Restore the user's saved touchscreen orientation after status exists.
if display_rotation:
    QTimer.singleShot(100, lambda: apply_display_rotation(display_rotation))


def open_settings():
    """Open settings and save the advanced-information preference."""
    global advanced_info_enabled, slow_frame_rate_enabled

    dialog = QDialog(window)
    dialog.setWindowTitle("Magni Settings")
    dialog.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setStyleSheet("""
        QDialog { background: #202020; color: white; }
        QLabel { color: white; font-size: 23px; }
        QCheckBox { color: white; font-size: 22px; spacing: 14px; }
        QCheckBox::indicator { width: 32px; height: 32px; }
        QPushButton { min-height: 64px; font-size: 22px; }
    """)

    dialog_layout = QVBoxLayout(dialog)
    dialog_layout.setContentsMargins(24, 28, 24, 28)
    dialog_layout.setSpacing(22)

    heading = QLabel("SETTINGS")
    heading.setStyleSheet("font-size: 30px; font-weight: bold;")
    heading.setAlignment(Qt.AlignCenter)
    dialog_layout.addWidget(heading)

    advanced_checkbox = QCheckBox("Show advanced camera information")
    advanced_checkbox.setChecked(advanced_info_enabled)
    dialog_layout.addWidget(advanced_checkbox)
    
    slow_rate_checkbox = QCheckBox(
    "Reduce frame rate for colour processing"
    )
    slow_rate_checkbox.setChecked(slow_frame_rate_enabled)
    dialog_layout.addWidget(slow_rate_checkbox)
    
    note = QLabel(
        "Advanced information includes analogue gain, exposure, "
        "focus position and FPS."
    )
    note.setWordWrap(True)
    note.setStyleSheet("font-size: 19px;")
    dialog_layout.addWidget(note)
    dialog_layout.addStretch()

    close_button = QPushButton("SAVE & CLOSE")
    dialog_layout.addWidget(close_button)
    close_button.clicked.connect(dialog.accept)

    dialog.setGeometry(window.geometry())
    dialog.showFullScreen()

    if dialog.exec_() == QDialog.Accepted:
        advanced_info_enabled = advanced_checkbox.isChecked()
        preferences.setValue(
            "advanced_info_enabled",
            advanced_info_enabled
        )

        requested_slow_rate = slow_rate_checkbox.isChecked()

        command = (
            "slow_rate_on"
            if requested_slow_rate
            else "slow_rate_off"
        )

        if send_command(command):
            slow_frame_rate_enabled = requested_slow_rate
            preferences.setValue(
                "slow_frame_rate_enabled",
                slow_frame_rate_enabled
            )
            refresh_tech_info()
        else:
            QMessageBox.warning(
                dialog,
                "Frame rate not changed",
                "The camera did not accept the frame-rate change. "
                "Please check that Magni is running and try again."
            )

settings_button.clicked.connect(open_settings)
rotation_button.clicked.connect(rotate_display)

def apply_saved_frame_rate():
    """Restore the saved slow frame-rate setting on startup."""
    if preferences.value(
        "slow_frame_rate_enabled", False, type=bool
    ):
        send_command("slow_rate_on")


# Allow the camera control server time to start.
QTimer.singleShot(3000, apply_saved_frame_rate)

# Each slider is a relative adjustment control.
# Moving away from the centre applies the existing Magni step commands.
slider_settings = [
    ("Magnification", 20, 10, "zoom_in", "zoom_out"),
    ("Brightness",    20, 10, "brightness_up", "brightness_down"),
    ("Contrast",      10,  5, "contrast_up", "contrast_down"),
    ("Saturation",    10,  5, "saturation_up", "saturation_down"),
    ("Sharpness",     10,  5, "sharpness_up", "sharpness_down"),
]

slider_states = {}


def add_adjustment_slider(name, maximum, centre, up_command, down_command):
    label = QLabel(f"{name}: centre")
    label.setObjectName("sliderLabel")

    slider = QSlider(Qt.Horizontal)
    slider.setRange(0, maximum)
    slider.setValue(centre)
    slider.setSingleStep(1)
    slider.setPageStep(1)
    slider.setTracking(True)
    slider.setMinimumWidth(0)

    slider_states[name] = {
        "slider": slider,
        "label": label,
        "last": centre,
        "centre": centre,
        "up": up_command,
        "down": down_command,
    }

    def update_label(value, control_name=name, midpoint=centre):
        offset = value - midpoint
        if offset > 0:
            text = f"+{offset} steps"
        elif offset < 0:
            text = f"{offset} steps"
        else:
            text = "centre"
        slider_states[control_name]["label"].setText(
            f"{control_name}: {text}"
        )

    slider.valueChanged.connect(update_label)
    slider.sliderReleased.connect(
        lambda control_name=name: apply_slider(control_name)
    )

    group = QWidget()
    group_layout = QVBoxLayout(group)
    group_layout.setContentsMargins(4, 1, 4, 1)
    group_layout.setSpacing(0)
    group_layout.addWidget(label)
    group_layout.addWidget(slider)
    slider_groups[name] = group


slider_states = {}
slider_groups = {}

for settings in slider_settings:
    add_adjustment_slider(*settings)

# Magnification and brightness span the panel; contrast and saturation share
# a row; sharpness occupies the final full row.
controls_layout.addWidget(slider_groups["Magnification"], 0, 0, 1, 2)
controls_layout.addWidget(slider_groups["Brightness"], 1, 0, 1, 2)
controls_layout.addWidget(slider_groups["Contrast"], 2, 0)
controls_layout.addWidget(slider_groups["Saturation"], 2, 1)
controls_layout.addWidget(slider_groups["Sharpness"], 3, 0, 1, 2)
controls_layout.setColumnStretch(0, 1)
controls_layout.setColumnStretch(1, 1)


def apply_slider(name):
    state = slider_states[name]
    target = state["slider"].value()
    previous = state["last"]

    if target == previous:
        return

    command = state["up"] if target > previous else state["down"]
    count = abs(target - previous)
    completed = 0

    for _ in range(count):
        if not send_command(command):
            break
        completed += 1

    # Record only adjustments successfully sent to Magni.
    if target > previous:
        state["last"] += completed
    else:
        state["last"] -= completed

    if completed == count:
        status.setText(f"{name} adjusted")
    else:
        status.setText(
            f"{name}: applied {completed} of {count} steps"
        )


# Two-column system and camera information panel
tech_panel = QFrame()
tech_panel.setStyleSheet("""
    QFrame {
        background: #303030;
        border: 1px solid #555555;
        border-radius: 8px;
    }
""")

tech_layout = QVBoxLayout(tech_panel)
tech_layout.setContentsMargins(10, 8, 10, 8)
tech_layout.setSpacing(4)
tech_layout.setAlignment(Qt.AlignTop)

tech_title = QLabel("SYSTEM & SETTINGS")
tech_title.setStyleSheet(
    "font-size: 23px; font-weight: bold; border: none;"
)
tech_title.setAlignment(Qt.AlignLeft | Qt.AlignTop)
tech_layout.addWidget(tech_title, 0, Qt.AlignTop)

columns = QGridLayout()
columns.setContentsMargins(0, 0, 0, 0)
columns.setHorizontalSpacing(18)
columns.setVerticalSpacing(2)
tech_layout.addLayout(columns)
tech_layout.addStretch(1)  # Keep spare panel height below the information for future use.

# Raspberry Pi information — left column
pi_title = QLabel("RASPBERRY PI")
pi_title.setStyleSheet(
    "font-size: 19px; font-weight: bold; border: none;"
)
columns.addWidget(pi_title, 0, 0)

pi_info = QLabel()
pi_info.setStyleSheet(
    "font-size: 18px; border: none; background: transparent;"
)
pi_info.setWordWrap(True)
columns.addWidget(pi_info, 1, 0)

# Camera information — right column
camera_title = QLabel("CAMERA")
camera_title.setStyleSheet(
    "font-size: 19px; font-weight: bold; border: none;"
)
columns.addWidget(camera_title, 0, 1)

camera_info = QLabel()
camera_info.setStyleSheet(
    "font-size: 18px; border: none; background: transparent;"
)
camera_info.setWordWrap(True)
columns.addWidget(camera_info, 1, 1)

columns.setColumnStretch(0, 1)
columns.setColumnStretch(1, 1)

body_layout.addWidget(tech_panel, 2)

def read_pi_model():
    """Read the Raspberry Pi model and optional board revision."""
    import re

    try:
        with open("/proc/device-tree/model", "r") as f:
            description = f.read().strip("\x00\n ")

        match = re.match(r"^(.*?)\s+Rev\s+(\S+)\s*$", description, re.IGNORECASE)

        if match:
            return match.group(1), match.group(2)

        return description, "Unknown"

    except OSError:
        return "Unknown Raspberry Pi model", "Unknown"

start_time = time.monotonic()


def read_system_info():
    """Read Raspberry Pi system information."""
    cpu_temp = "Unavailable"
    memory = "Unavailable"
    uptime = "Unavailable"
    cpu_load = "Unavailable"
    disk_space = "Unavailable"

    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            cpu_temp = f"{int(f.read().strip()) / 1000:.1f} °C"
    except (OSError, ValueError):
        pass

    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, value = line.split(":", 1)
                meminfo[key] = int(value.strip().split()[0])

        total = meminfo["MemTotal"] / 1024
        available = meminfo["MemAvailable"] / 1024
        memory = f"{available:.0f} / {total:.0f} MiB free"
    except (OSError, ValueError, KeyError):
        pass

    try:
        with open("/proc/uptime") as f:
            seconds = int(float(f.read().split()[0]))

        hours, remainder = divmod(seconds, 3600)
        days, hours = divmod(hours, 24)
        minutes = remainder // 60
        uptime = f"{days}d {hours}h {minutes}m"
    except (OSError, ValueError):
        pass

    try:
        with open("/proc/loadavg") as f:
            cpu_load = f.read().split()[0]
    except OSError:
        pass

    try:
        usage = shutil.disk_usage("/")
        gib = 1024 ** 3
        used = (usage.total - usage.free) / gib
        total = usage.total / gib
        #free = usage.free / gib
        disk_space = f"{used:.1f}/{total:.1f} GiB used"
    except OSError:
        pass

    return cpu_temp, memory, uptime, cpu_load, disk_space


def refresh_tech_info():
    temp, memory, uptime, load, disk_space = read_system_info()
    pi_model, pi_revision = read_pi_model()
    
    elapsed = int(time.monotonic() - start_time)
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)

    pi_info.setText(
        f"Model: {pi_model}\n"
        f"Revision: {pi_revision}\n"
        f"CPU temp: {temp}\n"
        f"Memory: {memory}\n"
        f"Disk: {disk_space}\n"
        f"Uptime: {uptime}\n"
        f"System load: {load}\n"
        f"Touch session: {hours:02d}:{minutes:02d}:{seconds:02d}"
    )
    # These are touchscreen adjustment steps, not confirmed camera values.
    adjustment_lines = []

    for name in (
        "Magnification",
        "Brightness",
        "Contrast",
        "Saturation",
        "Sharpness",
    ):
        state = slider_states[name]
        steps = state["last"] - state["centre"]
        adjustment_lines.append(f"{name}: {steps:+d} steps")

    camera_status = get_camera_status()


    if camera_status is None:
        camera_info.setText("Camera status unavailable")
    else:
        def show_value(value, fmt="{}"):
            return "Unavailable" if value is None else fmt.format(value)

        lines = [
            f"Magnification: {show_value(camera_status.get('magnification'), '{:.1f}')}x",
            f"Resolution: {camera_status.get('resolution', 'Unavailable')}",
            f"Colour: {camera_status.get('colour_mode', 'Unknown')}",
            f"Brightness: {show_value(camera_status.get('brightness'), '{:.2f}')}",
            f"Contrast: {show_value(camera_status.get('contrast'), '{:.2f}')}",
            f"Saturation: {show_value(camera_status.get('saturation'), '{:.2f}')}",
            f"Sharpness: {show_value(camera_status.get('sharpness'), '{:.2f}')}",
        ]

        if advanced_info_enabled:
            lines.extend([
                f"FPS: {show_value(camera_status.get('fps'), '{:.1f}')}",
                f"Exposure: {show_value(camera_status.get('exposure_us'))} µs",
                f"Analogue gain: {show_value(camera_status.get('analogue_gain'))}",
                f"Focus position: {show_value(camera_status.get('lens_position'))}",
            ])

        camera_info.setText("\n".join(lines))
    
tech_timer = QTimer(window)
tech_timer.timeout.connect(refresh_tech_info)
tech_timer.start(2000)

refresh_tech_info()

# Keep all four main actions together in one bottom row.
buttons_layout = QHBoxLayout()
buttons_layout.setSpacing(8)
layout.addLayout(buttons_layout)

buttons = [
    ("Autofocus", "focus_auto"),
    ("Next Colour", "next_color"),
    ("Save Photo", "save_photo"),
]

for label, command in buttons:
    button = QPushButton(label)
    button.setMinimumHeight(58)
    button.setStyleSheet("""
        QPushButton {
            font-size: 17px;
            padding: 3px;
        }
    """)
    button.clicked.connect(
        lambda checked=False, cmd=command: send_command(cmd)
    )
    buttons_layout.addWidget(button, 1)

off_button = QPushButton("Shutdown")
off_button.setMinimumHeight(58)
off_button.setStyleSheet("""
    QPushButton {
        background: #a51d25;
        border: 2px solid #ff7777;
        font-size: 18px;
        font-weight: bold;
    }
    QPushButton:pressed {
        background: #6f1017;
    }
""")
off_button.clicked.connect(shutdown_pi)
buttons_layout.addWidget(off_button, 1)

def update_responsive_layout():
    # The compositor changes the window geometry when the touchscreen rotates.
    landscape = window.width() > window.height()
    wanted = QBoxLayout.LeftToRight if landscape else QBoxLayout.TopToBottom
    if body_layout.direction() != wanted:
        body_layout.setDirection(wanted)
    body_layout.setStretch(0, 3)
    body_layout.setStretch(1, 2)

responsive_timer = QTimer(window)
responsive_timer.timeout.connect(update_responsive_layout)
responsive_timer.start(400)

# Place the control UI on the touchscreen before requesting fullscreen.
# Do not show fullscreen on the default screen first.
QTimer.singleShot(300, position_window_on_touchscreen)
sys.exit(app.exec_())
