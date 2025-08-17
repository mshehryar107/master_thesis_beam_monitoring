import os
import time
import cv2
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
from pypyueye import Camera
from pyueye import ueye
import serial
from matplotlib.widgets import Slider, Button

# ------------------- CONFIG -------------------
COM_PORT_STAGE     = 'COM3'
BAUDRATE_STAGE     = 57600
TARGET_POSITION_X  = 11526.15
Initial_pos_saved  = (33410.4, 23880.6, -21733.5)
PIXEL_SIZE_UM      = 5.3
SAVE_DIR           = r"C:\Users\mshehryar\Desktop\Tests\2025_08_06 - Stage Precision Test\Images_Initial_Target_10_Loops_Mid"
os.makedirs(SAVE_DIR, exist_ok=True)

# ------------------- STAGE -------------------
ser_stage = serial.Serial(
    COM_PORT_STAGE,
    BAUDRATE_STAGE,
    serial.EIGHTBITS,
    serial.PARITY_NONE,
    serial.STOPBITS_TWO,
    timeout=1
)

def send_stage_cmd(cmd, delay=0.2, expect_response=True):
    ser_stage.write((cmd + '\r').encode())
    time.sleep(delay)
    return ser_stage.readline().decode().strip() if expect_response else ""

def get_position():
    resp = send_stage_cmd("?pos")
    try:
        return tuple(map(float, resp.split()))
    except:
        return None

def move_to(x, y, z):
    send_stage_cmd(f"!moa {x} {y} {z}")
    time.sleep(1)
    return get_position()

def wait_for_position(target_position, tolerance=0.1, max_attempts=5):
    attempts = 0
    time.sleep(1)
    while attempts < max_attempts:
        current = get_position()
        if current is None:
            print("Failed to read position.")
            break
        if all(abs(a - b) <= tolerance for a, b in zip(current, target_position)):
            print(f"Reached target: {current}")
            return True
        attempts += 1
        print(f"Waiting... Current={current}, Target={target_position}")
        time.sleep(2)
    print("Failed to reach target.")
    return False

# ------------------- CAMERA -------------------
cam = Camera()
cam.init()
cam.alloc()
cam.set_colormode(ueye.IS_CM_MONO8)
cam.set_aoi(0, 0, 1280, 1024)
cam.set_fps(14.59)
cam.set_exposure(4.402)
cam.set_pixelclock(24)

def capture_and_save(name):
    img = cam.capture_image()
    while img is None:
        try:
            img = cam.capture_image()
        except Exception as e:
            print(f"Capture failed for {name}: {e}")
            return None
    path = os.path.join(SAVE_DIR, name)
    cv2.imwrite(path, img)
    print(f"📸 Saved {path}")
    return path

# ------------------- MAIN PROCESS -------------------
print("🔁 Moving to initial saved position...")
move_to(*Initial_pos_saved)
wait_for_position(Initial_pos_saved)

images_initial = []
images_target  = []

print("📍 Starting 10-cycle repeatability test...")
for i in range(1, 11):
    # 1) Capture at initial
    fname_i = f"initial_{i}.png"
    time.sleep(1)
    p_i = capture_and_save(fname_i)
    if p_i:
        images_initial.append(p_i)

    # 2) Move to target, capture
    move_to(TARGET_POSITION_X, Initial_pos_saved[1], Initial_pos_saved[2])
    wait_for_position((TARGET_POSITION_X, Initial_pos_saved[1], Initial_pos_saved[2]))
    fname_t = f"target_{i}.png"
    time.sleep(1)
    p_t = capture_and_save(fname_t)
    if p_t:
        images_target.append(p_t)

    # 3) Return home
    move_to(*Initial_pos_saved)
    wait_for_position(Initial_pos_saved)

# Load into memory as grayscale
images_initial = [cv2.imread(p, cv2.IMREAD_GRAYSCALE) for p in images_initial]
images_target  = [cv2.imread(p, cv2.IMREAD_GRAYSCALE) for p in images_target]

def compute_shift(img1, img2):
    img1_mean = np.mean(img1)
    img2_mean = np.mean(img2)
    
    img1f = np.float32(img1) - np.float32(img1_mean)
    img2f = np.float32(img2) - np.float32(img2_mean)
    
    win = cv2.createHanningWindow(img1.shape[::-1], cv2.CV_32F)
    img1w = img1f * win
    img2w = img2f * win

    shift, conf = cv2.phaseCorrelate(img1w, img2w)
    return shift, conf

def correlate_list(img_list):
    dxs, dys, confs = [], [], []
    for j in range(len(img_list) - 1):
        (sx, sy), cf = compute_shift(img_list[j], img_list[j+1])
        dxs.append(sx * PIXEL_SIZE_UM)
        dys.append(sy * PIXEL_SIZE_UM)
        confs.append(cf)
        print(f"Step {j+1}: dx={dxs[-1]:.2f}µm, dy={dys[-1]:.2f}µm, conf={cf:.4f}")
    return dxs, dys, confs

# Compute for both series
print("\nInitial-position correlations:")
dx_init, dy_init, conf_init = correlate_list(images_initial)

print("\nTarget-position correlations:")
dx_tgt, dy_tgt, conf_tgt = correlate_list(images_target)

def make_overlays(img_list):
    overlays = []
    for j in range(len(img_list) - 1):
        shift, _ = compute_shift(img_list[j], img_list[j+1])
        tx, ty = -shift[0], -shift[1]
        M = np.float32([[1, 0, tx], [0, 1, ty]])
        warped = cv2.warpAffine(img_list[j], M,
                                (img_list[j].shape[1], img_list[j].shape[0]))
        overlays.append(cv2.addWeighted(warped, .5, img_list[j+1], .5, 0))
    return overlays

over_init = make_overlays(images_initial)
over_tgt  = make_overlays(images_target)

def show_interactive_overlays(images, overlays, dx_vals, dy_vals, conf_vals):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 7))
    slider_ax = plt.axes([0.25, 0.05, 0.65, 0.03])
    step_slider = Slider(slider_ax, 'Step', 0, len(images)-2,
                         valinit=0, valstep=1)
    toggle_ax = plt.axes([0.8, 0.9, 0.1, 0.05])
    toggle_btn = Button(toggle_ax, 'Toggle Overlay', color='lightblue')

    toggle_status = True

    def update(val):
        step = int(val)
        ax1.clear(); ax2.clear()

        ax1.imshow(images[step], cmap='gray')
        ax1.set_title(f"Frame {step+1}")
        ax1.axis('off')

        if toggle_status:
            ax2.imshow(images[step], cmap='gray', alpha=0.5)
            ax2.imshow(overlays[step], cmap='coolwarm', alpha=0.3)
        else:
            ax2.imshow(images[step], cmap='gray')
        ax2.set_title(f"Overlay {step+1}")
        ax2.axis('off')

        txt = (f"dx: {dx_vals[step]:.2f} µm\n"
               f"dy: {dy_vals[step]:.2f} µm\n"
               f"conf: {conf_vals[step]:.4f}")
        ax2.text(10, 30, txt, color='white', fontsize=10,
                 bbox=dict(facecolor='black', alpha=0.5))
        fig.canvas.draw_idle()

    def on_toggle(event):
        nonlocal toggle_status
        toggle_status = not toggle_status
        update(step_slider.val)

    def on_key(event):
        if event.key == 'right':
            step_slider.set_val(min(step_slider.val + 1, len(images)-2))
        elif event.key == 'left':
            step_slider.set_val(max(step_slider.val - 1, 0))

    toggle_btn.on_clicked(on_toggle)
    step_slider.on_changed(update)
    fig.canvas.mpl_connect('key_press_event', on_key)

    update(0)
    plt.show()

# Show sliders
show_interactive_overlays(images_initial, over_init, dx_init, dy_init, conf_init)
show_interactive_overlays(images_target,  over_tgt,  dx_tgt,  dy_tgt,  conf_tgt)

# ------------------- SAVE & PLOT COMPARISONS -------------------
loops = list(range(1, len(conf_init) + 1))
df_stats = pd.DataFrame({
    'Loop':      loops,
    'dx_init':   dx_init,
    'dy_init':   dy_init,
    'conf_init': conf_init,
    'dx_tgt':    dx_tgt,
    'dy_tgt':    dy_tgt,
    'conf_tgt':  conf_tgt,
})
csv_path = os.path.join(SAVE_DIR, "repeatability_stats.csv")
df_stats.to_csv(csv_path, index=False)
print(f"✔️ Saved stats to {csv_path}")

fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 12), sharex=True)

# dx
ax1.plot(loops, df_stats['dx_init'],  'o-', label='Initial dx')
ax1.plot(loops, df_stats['dx_tgt'],   's--', label='Target dx')
# for x, y in zip(loops, df_stats['dx_init']):
#     ax1.annotate(f"{y:.2f}", (x, y), textcoords="offset points",
#                  xytext=(0, 6), ha='center')
# for x, y in zip(loops, df_stats['dx_tgt']):
#     ax1.annotate(f"{y:.2f}", (x, y), textcoords="offset points",
#                  xytext=(0, -12), ha='center')
ax1.set_ylabel('dx (µm)')
ax1.set_title('dx per Loop')
ax1.grid(which='both', linestyle='--', linewidth=0.5)
ax1.grid(which='minor', linestyle=':', linewidth=0.5)
ax1.legend()

# dy
ax2.plot(loops, df_stats['dy_init'],  'o-', label='Initial dy')
ax2.plot(loops, df_stats['dy_tgt'],   's--', label='Target dy')
# for x, y in zip(loops, df_stats['dy_init']):
#     ax2.annotate(f"{y:.2f}", (x, y), textcoords="offset points",
#                  xytext=(0, 6), ha='center')
# for x, y in zip(loops, df_stats['dy_tgt']):
#     ax2.annotate(f"{y:.2f}", (x, y), textcoords="offset points",
#                  xytext=(0, -12), ha='center')
ax2.set_ylabel('dy (µm)')
ax2.set_title('dy per Loop')
ax2.grid(which='both', linestyle='--', linewidth=0.5)
ax2.grid(which='minor', linestyle=':', linewidth=0.5)
ax2.legend()

# confidence
ax3.plot(loops, df_stats['conf_init'], 'o-', label='Initial conf')
ax3.plot(loops, df_stats['conf_tgt'],  's--', label='Target conf')
# for x, y in zip(loops, df_stats['conf_init']):
#     ax3.annotate(f"{y:.3f}", (x, y), textcoords="offset points",
#                  xytext=(0, 6), ha='center')
# for x, y in zip(loops, df_stats['conf_tgt']):
#     ax3.annotate(f"{y:.3f}", (x, y), textcoords="offset points",
#                  xytext=(0, -12), ha='center')
ax3.set_xlabel('Loop Number')
ax3.set_ylabel('Confidence')
ax3.set_title('Confidence per Loop')
ax3.grid(which='both', linestyle='--', linewidth=0.5)
ax3.grid(which='minor', linestyle=':', linewidth=0.5)
ax3.legend()

plt.tight_layout()
png_path = os.path.join(SAVE_DIR, "comparison_per_loop.png")
plt.savefig(png_path, dpi=300)
plt.show()

# ------------------- CLEANUP -------------------
cam.exit()
ser_stage.close()
