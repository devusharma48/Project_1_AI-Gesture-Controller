# AI Gesture Controller (Webcam-Based Mouse Replacement)

I built this project to create a contactless desktop controller using my computer's webcam. It tracks hand landmarks using MediaPipe and OpenCV, and maps them to actual mouse inputs in Windows. 

The main challenge with gesture-based control is making it feel as fast and reliable as a physical mouse or touchpad. To achieve this, I wrote custom coordinate-filtering algorithms, direct Win32 ctypes bindings for click actions, and a state machine to distinguish clicks from drags.

## How it Works under the Hood (Technical Details)

### 1. Smooth Cursor Movement (One Euro Filter)
Hand landmarks from webcams are naturally noisy. Using raw coordinates makes the mouse cursor shake heavily, which makes it impossible to click small buttons. 
To fix this, I implemented a **One Euro Filter** (an adaptive low-pass filter). When you move your hand quickly, the filter decreases smoothing to reduce lag; when you stop your hand, it increases smoothing to keep the cursor completely stationary. I also keep this filter running continuously in the background so that the velocity calculation is always warm, preventing cursor jumps when exiting scroll or click states.

### 2. Zero-Latency Clicks (Bypassing PyAutoGUI)
At first, I used PyAutoGUI for mouse inputs, but its built-in safety sleeps and Python wrappers added over 100ms of lag, causing minimize/maximize animations to stutter. 
I solved this by writing direct Windows API wrappers using Python's `ctypes` library. Clicking now sends `mouse_event(0x0002)` (down) and `mouse_event(0x0004)` (up) immediately. For cursor movement, I use `mouse_event(0x8001)` (absolute movement mapping). Because these commands talk directly to the Windows OS kernel, they execute in microseconds with **zero lag**, allowing native OS hover states (like taskbar window previews and tooltips) to pop up instantly when the hand stops.

### 3. Click vs. Drag Differentiation (Dynamic Hysteresis)
If you click a button but your hand shakes by a few pixels, the OS might register it as a "drag" and cancel the click. 
I created a dual-threshold state machine:
- When you pinch, the mouse goes down, and a starting coordinate is saved.
- If you keep your hand still (within 15 pixels), the cursor freezes in place to eliminate any hand drift, and releasing the pinch triggers a clean click.
- If you move your hand beyond 15 pixels, **Drag Mode** activates. Once active, the release threshold is dynamically increased (using a larger hysteresis buffer) so that natural finger movement while dragging doesn't cause the mouse button to release accidentally.

### 4. Background System Tray & Config GUI
The app runs in a background thread. You can close the camera window, and the app will keep running silently in the Windows system tray. 
- You can right-click the system tray icon to toggle tracking, show/hide the feed, or open the Tkinter-based settings window.
- The settings GUI uses a modern dark layout where you can adjust thresholds, margins, and smoothing weights. All changes are saved automatically to `config.json`.

---

## Gestures Reference

Here is how you control the system:
- **Move Cursor:** Move your hand (it tracks the index finger MCP knuckle for stability).
- **Left Click:** Pinch your Index finger and Thumb together quickly.
- **Drag & Drop:** Pinch Index and Thumb, then move your hand to drag. Open your hand wide to drop.
- **Right Click:** Pinch your Middle finger and Thumb together.
- **Double Click:** Pinch your Ring finger and Thumb together.
- **Scroll Mode:** Raise both your Index and Middle fingers while keeping others closed. Move your hand up or down to scroll (scroll speed is proportional to how far you move your hand from the starting point).

---

## How to Set Up and Run

### Running from Source
Make sure you have Python 3.8 or newer installed on your Windows machine, then follow these steps:

1. Clone or download the repository files.
2. Open your terminal in the project directory and install the requirements:
   ```bash
   pip install -r requirements.txt
Start the program: Run the command given below in your terminal

python main.py

## Running the Standalone Executable (.exe)
If you don't have Python installed, you can go into the dist/ directory and run: GestureController.exe

It contains the bundled Python runtime and all dependencies, running out-of-the-box on any Windows 10/11 PC.
