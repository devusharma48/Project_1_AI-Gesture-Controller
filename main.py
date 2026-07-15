import cv2 # type: ignore
import mediapipe as mp # type: ignore
import pyautogui # type: ignore
import numpy as np
import math
import sys
import ctypes
import time
import threading
import pystray # type: ignore
from PIL import Image, ImageDraw
import tkinter as tk

# Global control flags for background running and thread safety
running = True
tracking_enabled = True
show_camera_feed = True
tray_icon = None
settings_window = None

# Set DPI awareness for accurate cursor mapping on high-DPI Windows screens
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2) # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# Configure PyAutoGUI settings
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0  # Minimize pyautogui's internal command latency

# Initialize MediaPipe Hands
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(
    max_num_hands=1,
    min_detection_confidence=0.7,
    min_tracking_confidence=0.7
)
mp_drawing = mp.solutions.drawing_utils

# Get Screen Dimensions
screen_w, screen_h = pyautogui.size()

import json
import os

CONFIG_FILE = "config.json"

# Dynamic parameters (initially loaded from config.json or defaults)
MARGIN_X = 0.23
MARGIN_Y = 0.23
CLICK_THRESHOLD = 0.06
RESET_THRESHOLD = 0.08
SCROLL_SENSITIVITY = 1.5
SMOOTHING_FACTOR = 0.38

def load_config():
    global MARGIN_X, MARGIN_Y, CLICK_THRESHOLD, RESET_THRESHOLD, SCROLL_SENSITIVITY, SMOOTHING_FACTOR
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                loaded = json.load(f)
                MARGIN_X = float(loaded.get("margin_x", 0.23))
                MARGIN_Y = float(loaded.get("margin_y", 0.23))
                CLICK_THRESHOLD = float(loaded.get("click_threshold", 0.06))
                RESET_THRESHOLD = float(loaded.get("reset_threshold", 0.08))
                SCROLL_SENSITIVITY = float(loaded.get("scroll_sensitivity", 1.5))
                SMOOTHING_FACTOR = float(loaded.get("smoothing_factor", 0.38))
                print("Config loaded from config.json")
        except Exception as e:
            print("Error loading config:", e)
    else:
        save_config()

def save_config():
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump({
                "margin_x": MARGIN_X,
                "margin_y": MARGIN_Y,
                "click_threshold": CLICK_THRESHOLD,
                "reset_threshold": RESET_THRESHOLD,
                "scroll_sensitivity": SCROLL_SENSITIVITY,
                "smoothing_factor": SMOOTHING_FACTOR
            }, f, indent=4)
        print("Config saved to config.json")
    except Exception as e:
        print("Error saving config:", e)

# Initial load of configuration
load_config()

class OneEuroFilter:
    def __init__(self, min_cutoff=1.0, beta=0.007, d_cutoff=1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    def filter(self, t, x):
        if self.x_prev is None:
            self.x_prev = x
            self.t_prev = t
            return x

        dt = t - self.t_prev
        if dt <= 0:
            return self.x_prev

        # Estimate velocity
        d_x = (x - self.x_prev) / dt

        # Filter velocity
        tau_d = 1.0 / (2.0 * math.pi * self.d_cutoff)
        alpha_d = 1.0 / (1.0 + tau_d / dt)
        dx_hat = alpha_d * d_x + (1.0 - alpha_d) * self.dx_prev

        # Filter signal
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        tau = 1.0 / (2.0 * math.pi * cutoff)
        alpha = 1.0 / (1.0 + tau / dt)
        x_hat = alpha * x + (1.0 - alpha) * self.x_prev

        self.x_prev = x_hat
        self.dx_prev = dx_hat
        self.t_prev = t
        return x_hat

# High-performance Win32 API mouse helpers for zero-latency operations on Windows
def win32_mouse_down():
    if sys.platform == "win32":
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
    else:
        pyautogui.mouseDown()

def win32_mouse_up():
    if sys.platform == "win32":
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
    else:
        pyautogui.mouseUp()

def win32_right_click():
    if sys.platform == "win32":
        ctypes.windll.user32.mouse_event(0x0008, 0, 0, 0, 0)
        ctypes.windll.user32.mouse_event(0x0010, 0, 0, 0, 0)
    else:
        pyautogui.rightClick()

def win32_double_click():
    if sys.platform == "win32":
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
    else:
        pyautogui.doubleClick()

def tracking_loop():
    global running, tracking_enabled, show_camera_feed

    # Initialize webcam
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not open webcam.")
        running = False
        return

    # Set camera resolution (standard 640x480) and request 60 FPS for maximum smoothness
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 60)

    # Move mouse to center of the screen to avoid starting at a corner and triggering fail-safe
    pyautogui.FAILSAFE = False
    pyautogui.moveTo(screen_w // 2, screen_h // 2)
    pyautogui.FAILSAFE = True

    prev_x, prev_y = pyautogui.position()
    prev_pixel_x = int(prev_x)
    prev_pixel_y = int(prev_y)
    is_clicked = False
    is_right_clicked = False
    is_double_clicked = False
    in_scroll_mode = False
    scroll_ref_y = 0.0
    was_showing_feed = True

    # Left click stabilization / drag variables
    left_click_start_time = 0.0
    left_click_start_pos = (0.0, 0.0)
    is_dragging = False
    min_pinch_dist = 1.0

    # Initialize One Euro Filters for smooth, adaptive, and lag-free coordinate estimation
    filter_x = OneEuroFilter(min_cutoff=0.05, beta=0.005, d_cutoff=1.0)
    filter_y = OneEuroFilter(min_cutoff=0.05, beta=0.005, d_cutoff=1.0)

    print("--- Gesture Controller Tracking Thread Started ---")

    try:
        while running and cap.isOpened():
            success, frame = cap.read()
            if not success:
                print("Ignoring empty camera frame.")
                time.sleep(0.05) # Prevent 100% CPU spinning if webcam fails/disconnects
                continue

            # Flip frame horizontally to act like a mirror
            frame = cv2.flip(frame, 1)
            h, w, _ = frame.shape

            # Manual fail-safe check: if mouse cursor is placed in top-left corner, exit
            try:
                cursor_x, cursor_y = pyautogui.position()
                if cursor_x < 10 and cursor_y < 10:
                    print("Fail-safe triggered! Cursor is near top-left corner.")
                    running = False
                    break
            except Exception:
                pass

            # If tracking is disabled, release any active mouse click and skip processing
            if not tracking_enabled:
                if is_clicked:
                    try:
                        win32_mouse_up()
                    except Exception:
                        pass
                    is_clicked = False
                    print("Tracking disabled: Released Left Mouse Button")
                is_right_clicked = False
                is_double_clicked = False
                in_scroll_mode = False
                is_dragging = False
                time.sleep(0.05)
                if not show_camera_feed:
                    try:
                        cv2.destroyAllWindows()
                    except Exception:
                        pass
                continue

            # Convert BGR image to RGB for MediaPipe Hand landmarking
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = hands.process(rgb_frame)

            # Draw the active bounding box boundaries on the screen (only if feed is visible)
            if show_camera_feed:
                bx1, by1 = int(MARGIN_X * w), int(MARGIN_Y * h)
                bx2, by2 = int((1 - MARGIN_X) * w), int((1 - MARGIN_Y) * h)
                cv2.rectangle(frame, (bx1, by1), (bx2, by2), (255, 0, 0), 2)
                cv2.putText(frame, "Active Tracking Zone", (bx1, by1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)

            if results.multi_hand_landmarks:
                for hand_landmarks in results.multi_hand_landmarks:
                    if show_camera_feed:
                        # Draw hand skeletons
                        mp_drawing.draw_landmarks(
                            frame, hand_landmarks, mp_hands.HAND_CONNECTIONS,
                            mp_drawing.DrawingSpec(color=(0, 0, 255), thickness=2, circle_radius=2),
                            mp_drawing.DrawingSpec(color=(0, 255, 0), thickness=2, circle_radius=2)
                        )

                    # Get coordinates of Index finger tip (Landmark 8) and Thumb tip (Landmark 4)
                    # We track the index knuckle (MCP joint) for cursor position because it is much more stable,
                    # but we keep index_landmark (fingertip) for click distance calculations to avoid click-drift.
                    index_landmark = hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_TIP]
                    index_mcp = hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_MCP]
                    thumb_landmark = hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_TIP]

                    # Cursor coordinates follow the stable knuckle (reduces click-drift and shaking)
                    ix, iy = index_mcp.x, index_mcp.y
                    tx, ty = thumb_landmark.x, thumb_landmark.y

                    # Convert fingertip and thumb tip to pixel coords for visualization
                    pixel_index = (int(index_landmark.x * w), int(index_landmark.y * h))
                    pixel_thumb = (int(tx * w), int(ty * h))

                    # Bounding box clamping: map tracking area margins to full screen coordinates
                    mapped_x = (ix - MARGIN_X) / (1 - 2 * MARGIN_X)
                    mapped_y = (iy - MARGIN_Y) / (1 - 2 * MARGIN_Y)

                    # Clamp between 0.0 and 1.0 to prevent moving cursor beyond screen bounds
                    mapped_x = np.clip(mapped_x, 0.0, 1.0)
                    mapped_y = np.clip(mapped_y, 0.0, 1.0)

                    # Map coordinates to actual screen resolution
                    target_x = mapped_x * screen_w
                    target_y = mapped_y * screen_h

                    # Get additional landmarks needed for Right Click, Scroll Mode, and HUD
                    middle_landmark = hand_landmarks.landmark[mp_hands.HandLandmark.MIDDLE_FINGER_TIP]
                    middle_pip = hand_landmarks.landmark[mp_hands.HandLandmark.MIDDLE_FINGER_PIP]
                    
                    ring_landmark = hand_landmarks.landmark[mp_hands.HandLandmark.RING_FINGER_TIP]
                    ring_pip = hand_landmarks.landmark[mp_hands.HandLandmark.RING_FINGER_PIP]
                    
                    pinky_landmark = hand_landmarks.landmark[mp_hands.HandLandmark.PINKY_TIP]
                    pinky_pip = hand_landmarks.landmark[mp_hands.HandLandmark.PINKY_PIP]
                    
                    index_pip = hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_PIP]

                    # Convert Middle finger tip to pixel coords
                    mx, my = middle_landmark.x, middle_landmark.y
                    pixel_middle = (int(mx * w), int(my * h))

                    # Compute 2D distances between finger tips and thumb tip (using fingertips for pinch detection)
                    left_click_dist = math.hypot(index_landmark.x - thumb_landmark.x, index_landmark.y - thumb_landmark.y)
                    right_click_dist = math.hypot(middle_landmark.x - thumb_landmark.x, middle_landmark.y - thumb_landmark.y)
                    double_click_dist = math.hypot(ring_landmark.x - thumb_landmark.x, ring_landmark.y - thumb_landmark.y)

                    # Finger extension check (comparing tip to PIP joint is much more accurate for scroll detection)
                    index_extended = index_landmark.y < index_pip.y
                    middle_extended = middle_landmark.y < middle_pip.y
                    ring_extended = ring_landmark.y < ring_pip.y
                    pinky_extended = pinky_landmark.y < pinky_pip.y

                    # Scroll Mode Gesture: Index and Middle fingers extended, Ring and Pinky closed
                    scroll_gesture = index_extended and middle_extended and not ring_extended and not pinky_extended

                    # Handle scroll mode state transitions and execution (Joystick style: hold to scroll)
                    if scroll_gesture:
                        if not in_scroll_mode:
                            in_scroll_mode = True
                            scroll_ref_y = index_landmark.y
                        else:
                            dy = index_landmark.y - scroll_ref_y
                            dy_pixels = dy * h
                            if abs(dy_pixels) > 15: # 15px dead zone around starting point
                                # Proportional continuous scrolling: hold hand higher to scroll up, lower to scroll down
                                scroll_ticks = -int(dy_pixels * SCROLL_SENSITIVITY)
                                pyautogui.scroll(scroll_ticks)
                    else:
                        in_scroll_mode = False

                    # Determine if user is in the process of pinching (close to clicking)
                    # to freeze/dampen cursor movement to prevent drift.
                    # We freeze the cursor during a Left Click unless they explicitly transition to dragging.
                    is_pinching = (right_click_dist < (CLICK_THRESHOLD + 0.02)) or \
                                  (double_click_dist < (CLICK_THRESHOLD + 0.02)) or \
                                  (is_clicked and not is_dragging)

                    # Smooth coordinates using One Euro Filter (always computed to track hand location under the hood)
                    t = time.time()
                    smoothed_x = filter_x.filter(t, target_x)
                    smoothed_y = filter_y.filter(t, target_y)
                    smoothed_x = prev_x + (smoothed_x - prev_x) * SMOOTHING_FACTOR
                    smoothed_y = prev_y + (smoothed_y - prev_y) * SMOOTHING_FACTOR

                    if in_scroll_mode:
                        # Freeze cursor when scrolling
                        curr_x, curr_y = prev_x, prev_y
                    elif is_pinching:
                        # Freeze cursor when pinching or clicking to prevent drift
                        curr_x, curr_y = prev_x, prev_y
                    else:
                        curr_x, curr_y = smoothed_x, smoothed_y

                        # Dead zone check: if the filtered cursor moved less than 2.0 pixels,
                        # hold it in place to eliminate any remaining vibrations and allow Windows hover states (like taskbar previews).
                        if math.hypot(curr_x - prev_x, curr_y - prev_y) < 2.0:
                            curr_x, curr_y = prev_x, prev_y

                    # Prevent hand tracking from moving mouse to (0, 0) and triggering fail-safe.
                    move_x = max(5, int(curr_x))
                    move_y = max(5, int(curr_y))

                    # Only move cursor if we are not in scroll mode
                    if not in_scroll_mode:
                        # Only send mouse movement to OS if coordinates have actually changed on pixel level.
                        # This allows the OS to see the mouse as "idle/stationary" when the hand is still,
                        # which triggers native hover states (like taskbar previews and tooltips).
                        if move_x != prev_pixel_x or move_y != prev_pixel_y:
                            try:
                                if sys.platform == "win32":
                                    # Use Win32 mouse_event with absolute mapping to trigger hover states naturally (instant)
                                    rx = int(move_x * 65535 / screen_w)
                                    ry = int(move_y * 65535 / screen_h)
                                    ctypes.windll.user32.mouse_event(0x8001, rx, ry, 0, 0)
                                else:
                                    pyautogui.moveTo(move_x, move_y)
                                prev_pixel_x, prev_pixel_y = move_x, move_y
                                prev_x, prev_y = curr_x, curr_y
                            except pyautogui.FailSafeException as e:
                                print(f"Fail-safe triggered! Current position: {pyautogui.position()}, Target: ({move_x}, {move_y})")
                                running = False
                                break
                        else:
                            # Keep prev_x/y updated to avoid jumping
                            prev_x, prev_y = curr_x, curr_y
                    else:
                        # Keep prev_x/y updated to avoid jumps when exiting scroll mode
                        prev_x, prev_y = curr_x, curr_y

                    # Click logic with hysteresis to prevent double clicks/jitter
                    circle_color = (0, 255, 255) # Yellow by default (Hovering)
                    
                    # Prevent accidental clicks during scroll mode or when transition gesture is active
                    if in_scroll_mode or scroll_gesture:
                        if is_clicked:
                            try:
                                win32_mouse_up()
                            except Exception:
                                pass
                            print("Scroll/Gesture mode: Released Left Mouse Button")
                        is_clicked = False
                        is_right_clicked = False
                        is_double_clicked = False
                    else:
                        # Determine the active pinch gesture (mutual exclusivity to prevent confusion)
                        # We only trigger a click if that specific finger is the closest to the thumb
                        min_dist = min(left_click_dist, right_click_dist, double_click_dist)

                        # Left Click & Drag check
                        if left_click_dist < CLICK_THRESHOLD and min_dist == left_click_dist:
                            circle_color = (0, 255, 0) # Green for left click/drag
                            if not is_clicked:
                                win32_mouse_down()
                                is_clicked = True
                                left_click_start_time = time.time()
                                left_click_start_pos = (prev_x, prev_y)
                                is_dragging = False
                                min_pinch_dist = left_click_dist
                                print("Left Mouse Down (Drag Start)")
                            else:
                                min_pinch_dist = min(min_pinch_dist, left_click_dist)
                                if not is_dragging:
                                    # Calculate distance moved from start of click
                                    # using the smoothed coordinates (not the noisy raw ones)
                                    dist_moved = math.hypot(smoothed_x - left_click_start_pos[0], smoothed_y - left_click_start_pos[1])
                                    # Transition to dragging if moved more than 15 pixels (prevents accidental drag on single click)
                                    if dist_moved > 15.0:
                                        is_dragging = True
                                        print("Drag mode activated")
                        elif left_click_dist > (RESET_THRESHOLD + 0.025 if is_dragging else RESET_THRESHOLD) or \
                             left_click_dist > (min_pinch_dist + (0.045 if is_dragging else 0.02)):
                            if is_clicked:
                                win32_mouse_up()
                                is_clicked = False
                                is_dragging = False
                                min_pinch_dist = 1.0
                                print("Left Mouse Up (Drag End / Click)")

                        # Right Click check
                        if right_click_dist < CLICK_THRESHOLD and min_dist == right_click_dist:
                            circle_color = (255, 0, 255) # Magenta for right click
                            if is_clicked:
                                try:
                                    win32_mouse_up()
                                except Exception:
                                    pass
                                is_clicked = False
                                is_dragging = False
                                min_pinch_dist = 1.0
                                print("Right click safety: Released Left Mouse Button")
                            if not is_right_clicked:
                                win32_right_click()
                                is_right_clicked = True
                                print("Right Click detected!")
                        elif right_click_dist > RESET_THRESHOLD:
                            is_right_clicked = False

                        # Double Click check
                        if double_click_dist < CLICK_THRESHOLD and min_dist == double_click_dist:
                            circle_color = (255, 0, 0) # Red for double click
                            if is_clicked:
                                try:
                                    win32_mouse_up()
                                except Exception:
                                    pass
                                is_clicked = False
                                is_dragging = False
                                min_pinch_dist = 1.0
                                print("Double click safety: Released Left Mouse Button")
                            if not is_double_clicked:
                                win32_double_click()
                                is_double_clicked = True
                                print("Double Click detected!")
                        elif double_click_dist > RESET_THRESHOLD:
                            is_double_clicked = False

                    # Visualize interactive states on the frame (only if camera feed is active)
                    if show_camera_feed:
                        if in_scroll_mode:
                            # Draw scroll indicator lines
                            cv2.circle(frame, pixel_index, 10, (255, 128, 0), cv2.FILLED)
                            cv2.circle(frame, pixel_middle, 10, (255, 128, 0), cv2.FILLED)
                            status_text = "SCROLL MODE"
                            status_color = (255, 128, 0)
                        else:
                            cv2.circle(frame, pixel_index, 10, circle_color, cv2.FILLED)
                            cv2.circle(frame, pixel_thumb, 10, (0, 255, 255), cv2.FILLED)
                            cv2.line(frame, pixel_index, pixel_thumb, circle_color, 2)
                            
                            # Also visualize ring finger position
                            ring_tip_pixel = (int(ring_landmark.x * w), int(ring_landmark.y * h))
                            cv2.circle(frame, ring_tip_pixel, 10, (255, 0, 0) if is_double_clicked else (0, 255, 255), cv2.FILLED)
                            
                            if is_clicked:
                                status_text = "LEFT CLICKED"
                                status_color = (0, 255, 0)
                            elif is_right_clicked:
                                status_text = "RIGHT CLICKED"
                                status_color = (255, 0, 255)
                            elif is_double_clicked:
                                status_text = "DOUBLE CLICKED"
                                status_color = (255, 0, 0)
                            else:
                                status_text = "HOVERING"
                                status_color = (0, 255, 255)

                        cv2.putText(frame, f"State: {status_text}", (10, 30),
                                    cv2.FONT_HERSHEY_SIMPLEX, 1, status_color, 2)
                        cv2.putText(frame, f"L-Dist: {left_click_dist:.3f} | R-Dist: {right_click_dist:.3f} | D-Dist: {double_click_dist:.3f}", (10, 70),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2)
            else:
                # If no hands are detected, release any active mouse clicks or drags and reset states
                if is_clicked:
                    try:
                        win32_mouse_up()
                    except Exception:
                        pass
                    is_clicked = False
                    print("Hand lost: Released Left Mouse Button")
                is_right_clicked = False
                is_double_clicked = False
                in_scroll_mode = False
                is_dragging = False

            if show_camera_feed:
                # Show the frame
                cv2.imshow("Gesture Controller", frame)
                was_showing_feed = True

                # Quit window via 'q' or 'Esc' key
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q') or key == 27:
                    running = False
                    break

                # Check if user clicked the 'X' button of the OpenCV window to close it
                try:
                    if cv2.getWindowProperty("Gesture Controller", cv2.WND_PROP_VISIBLE) < 1:
                        print("Window closed by user.")
                        running = False
                        break
                except Exception:
                    # If window is destroyed, cv2 raises an exception. We interpret this as window closed.
                    print("Window closed by user.")
                    running = False
                    break
            else:
                # If window was visible previously, close it ONCE on this thread
                if was_showing_feed:
                    try:
                        cv2.destroyWindow("Gesture Controller")
                        # Pump events multiple times to let Windows clean up the taskbar icon
                        for _ in range(10):
                            cv2.waitKey(1)
                    except Exception:
                        pass
                    was_showing_feed = False
                time.sleep(0.01)

    except KeyboardInterrupt:
        print("Script interrupted manually.")
    finally:
        # Clean up
        try:
            win32_mouse_up() # Ensure mouse is released on exit
        except Exception:
            pass
        cap.release()
        try:
            cv2.destroyWindow("Gesture Controller")
            for _ in range(10):
                cv2.waitKey(1)
        except Exception:
            pass
        try:
            cv2.destroyAllWindows()
            for _ in range(10):
                cv2.waitKey(1)
        except Exception:
            pass
        hands.close()
        running = False
        
        # Stop system tray icon if running
        global tray_icon
        if tray_icon is not None:
            try:
                tray_icon.stop()
            except Exception:
                pass
        print("Resources released. Gesture Controller closed.")

def show_settings_gui():
    global settings_window, tracking_enabled
    if settings_window is not None:
        try:
            # Check if it is a real Tkinter window or still loading
            if hasattr(settings_window, "focus_force"):
                settings_window.focus_force()
                settings_window.lift()
            return
        except Exception:
            settings_window = None

    # Pause tracking while Settings GUI is open to prevent cursor fight/drift during adjustment
    was_tracking = tracking_enabled
    tracking_enabled = False
    settings_window = "loading"

    def run_gui():
        global settings_window, MARGIN_X, MARGIN_Y, CLICK_THRESHOLD, RESET_THRESHOLD, SCROLL_SENSITIVITY, SMOOTHING_FACTOR, tracking_enabled
        
        root = tk.Tk()
        root.title("Gesture Controller Settings")
        root.geometry("450x550")
        root.resizable(False, False)
        settings_window = root

        # Modern Dark Theme Colors
        bg_color = "#1e1e24"
        card_color = "#2a2a35"
        text_color = "#ffffff"
        accent_color = "#00e5ff"
        accent_hover = "#00b2cc"
        button_bg = "#3a3a4a"

        root.configure(bg=bg_color)

        # Typography
        title_font = ("Segoe UI", 16, "bold")
        header_font = ("Segoe UI", 11, "bold")
        label_font = ("Segoe UI", 9)
        value_font = ("Consolas", 10, "bold")

        # Header Section
        header_frame = tk.Frame(root, bg=bg_color, pady=15)
        header_frame.pack(fill="x")
        
        title_label = tk.Label(header_frame, text="AI Gesture Controller Settings", font=title_font, fg=accent_color, bg=bg_color)
        title_label.pack()
        
        subtitle_label = tk.Label(header_frame, text="Tune sensitivities and active zones in real-time.", font=label_font, fg="#aaaaaa", bg=bg_color)
        subtitle_label.pack()

        # Body Container
        body = tk.Frame(root, bg=bg_color, padx=25)
        body.pack(fill="both", expand=True)

        # Linked Slider Variables
        var_margin_x = tk.DoubleVar(value=MARGIN_X)
        var_margin_y = tk.DoubleVar(value=MARGIN_Y)
        var_click = tk.DoubleVar(value=CLICK_THRESHOLD)
        var_reset = tk.DoubleVar(value=RESET_THRESHOLD)
        var_scroll = tk.DoubleVar(value=SCROLL_SENSITIVITY)
        var_smooth = tk.DoubleVar(value=SMOOTHING_FACTOR)

        # Dictionary to store value display labels dynamically
        value_labels = {}

        # Real-time Update Handler
        def on_slider_change(*args):
            global MARGIN_X, MARGIN_Y, CLICK_THRESHOLD, RESET_THRESHOLD, SCROLL_SENSITIVITY, SMOOTHING_FACTOR
            MARGIN_X = round(var_margin_x.get(), 2)
            MARGIN_Y = round(var_margin_y.get(), 2)
            CLICK_THRESHOLD = round(var_click.get(), 3)
            
            # Hysteresis check
            click_val = var_click.get()
            reset_val = var_reset.get()
            if reset_val < click_val + 0.01:
                reset_val = click_val + 0.02
                var_reset.set(reset_val)
            RESET_THRESHOLD = round(reset_val, 3)
            
            SCROLL_SENSITIVITY = round(var_scroll.get(), 1)
            SMOOTHING_FACTOR = round(var_smooth.get(), 2)

            # Update Label displays if they are initialized
            if "margin_x" in value_labels: value_labels["margin_x"].config(text=f"{MARGIN_X:.2f}")
            if "margin_y" in value_labels: value_labels["margin_y"].config(text=f"{MARGIN_Y:.2f}")
            if "click" in value_labels: value_labels["click"].config(text=f"{CLICK_THRESHOLD:.3f}")
            if "reset" in value_labels: value_labels["reset"].config(text=f"{RESET_THRESHOLD:.3f}")
            if "scroll" in value_labels: value_labels["scroll"].config(text=f"{SCROLL_SENSITIVITY:.1f}")
            if "smooth" in value_labels: value_labels["smooth"].config(text=f"{SMOOTHING_FACTOR:.2f}")

        # Grid-based Row Helper
        def create_slider_row(parent, key, label_text, var, from_val, to_val, resolution, row_idx):
            lbl = tk.Label(parent, text=label_text, font=label_font, fg=text_color, bg=bg_color, anchor="w")
            lbl.grid(row=row_idx, column=0, sticky="ew", pady=(8, 0))
            
            val_lbl = tk.Label(parent, font=value_font, fg=accent_color, bg=bg_color, width=6, anchor="e")
            val_lbl.grid(row=row_idx, column=1, sticky="e", pady=(8, 0))
            
            slider = tk.Scale(parent, variable=var, from_=from_val, to=to_val, resolution=resolution, 
                              orient="horizontal", showvalue=False, bg=card_color, fg=accent_color,
                              highlightthickness=0, troughcolor=bg_color, bd=0, activebackground=accent_color,
                              command=on_slider_change)
            slider.grid(row=row_idx+1, column=0, columnspan=2, sticky="ew", pady=(2, 6))
            value_labels[key] = val_lbl

        grid_frame = tk.Frame(body, bg=bg_color)
        grid_frame.pack(fill="x")
        grid_frame.columnconfigure(0, weight=1)
        grid_frame.columnconfigure(1, weight=0)

        create_slider_row(grid_frame, "margin_x", "Tracking Margin X (horizontal active zone margin)", var_margin_x, 0.05, 0.45, 0.01, 0)
        create_slider_row(grid_frame, "margin_y", "Tracking Margin Y (vertical active zone margin)", var_margin_y, 0.05, 0.45, 0.01, 2)
        create_slider_row(grid_frame, "click", "Left Click / Pinch Distance Threshold", var_click, 0.02, 0.15, 0.005, 4)
        create_slider_row(grid_frame, "reset", "Click Release Distance Threshold", var_reset, 0.03, 0.20, 0.005, 6)
        create_slider_row(grid_frame, "scroll", "Scroll Speed Multiplier", var_scroll, 0.5, 5.0, 0.1, 8)
        create_slider_row(grid_frame, "smooth", "Smoothing Factor (higher = faster, lower = more stable)", var_smooth, 0.05, 1.00, 0.01, 10)

        # Initialize labels
        on_slider_change()

        # Action Buttons
        btn_frame = tk.Frame(body, bg=bg_color, pady=20)
        btn_frame.pack(fill="x")
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        def save_and_close():
            global settings_window, tracking_enabled
            save_config()
            settings_window = None
            tracking_enabled = was_tracking
            root.destroy()

        def reset_defaults():
            var_margin_x.set(0.23)
            var_margin_y.set(0.23)
            var_click.set(0.06)
            var_reset.set(0.08)
            var_scroll.set(1.5)
            var_smooth.set(0.38)
            on_slider_change()
            save_config()

        btn_reset = tk.Button(btn_frame, text="Reset Defaults", font=label_font, fg=text_color, bg=button_bg,
                              activebackground=button_bg, activeforeground=accent_color, relief="flat", bd=0,
                              padx=10, pady=8, command=reset_defaults)
        btn_reset.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        btn_save = tk.Button(btn_frame, text="Save & Close", font=header_font, fg=bg_color, bg=accent_color,
                             activebackground=accent_hover, activeforeground=bg_color, relief="flat", bd=0,
                             padx=10, pady=8, command=save_and_close)
        btn_save.grid(row=0, column=1, sticky="ew", padx=(10, 0))

        def on_close():
            global settings_window, tracking_enabled
            settings_window = None
            tracking_enabled = was_tracking
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()

    threading.Thread(target=run_gui, daemon=True).start()

def create_icon_image():
    # Create a 64x64 icon image dynamically (a blue circle inside a dark background)
    image = Image.new('RGB', (64, 64), color=(30, 30, 30))
    dc = ImageDraw.Draw(image)
    # Draw a cyan circle with white outline representing hand node
    dc.ellipse([(16, 16), (48, 48)], fill=(0, 255, 255), outline=(255, 255, 255), width=2)
    return image

def setup_system_tray():
    global running, tracking_enabled, show_camera_feed

    def on_show_settings(icon, item):
        show_settings_gui()

    def on_toggle_tracking(icon, item):
        global tracking_enabled
        tracking_enabled = not tracking_enabled
        icon.update_menu()

    def on_toggle_feed(icon, item):
        global show_camera_feed
        show_camera_feed = not show_camera_feed
        icon.update_menu()

    def on_exit(icon, item):
        global running
        running = False
        icon.stop()

    # Define system tray menu
    menu = pystray.Menu(
        pystray.MenuItem('Settings', on_show_settings),
        pystray.MenuItem('Toggle Tracking', on_toggle_tracking, checked=lambda item: tracking_enabled),
        pystray.MenuItem('Show Camera Feed', on_toggle_feed, checked=lambda item: show_camera_feed),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Exit', on_exit)
    )

    # Initialize taskbar icon
    global tray_icon
    icon = pystray.Icon("GestureController", create_icon_image(), "AI Gesture Controller", menu)
    tray_icon = icon
    icon.run()

def main():
    global running

    # 1. Start the hand tracking loop in a background thread
    tracking_thread = threading.Thread(target=tracking_loop, daemon=True)
    tracking_thread.start()

    # 2. Start the system tray icon loop in the main thread (blocking)
    try:
        setup_system_tray()
    except KeyboardInterrupt:
        running = False

if __name__ == "__main__":
    main()
