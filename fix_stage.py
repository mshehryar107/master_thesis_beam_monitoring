#%%
import os
import time
import cv2
import numpy as np
from datetime import datetime
from pypyueye import Camera
from pyueye import ueye
import serial
import csv
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.widgets import CheckButtons
from matplotlib.widgets import Slider, Button

# ------------------- CONFIG -------------------
COM_PORT_STAGE = 'COM3'
BAUDRATE_STAGE = 57600
TARGET_POSITION_X = 32510.4
Initial_pos_saved = (33410.4, 23880.6, -21733.5)
PIXEL_SIZE_UM = 5.3
SAVE_DIR = r"C:\Users\mshehryar\Desktop\Tests\2025_08_06 - Stage Precision Test\Images_10Steps_3"
os.makedirs(SAVE_DIR, exist_ok=True)


#%%
# ------------------- STAGE -------------------
ser_stage = serial.Serial(COM_PORT_STAGE, BAUDRATE_STAGE, serial.EIGHTBITS,
                          serial.PARITY_NONE, serial.STOPBITS_TWO, timeout=1)

#%%
def send_stage_cmd(cmd, delay=0.2, expect_response=True):
    ser_stage.write((cmd + '\r').encode())
    time.sleep(delay)
    return ser_stage.readline().decode().strip() if expect_response else ""

def get_position():
    resp = send_stage_cmd("?pos")
    try: return tuple(map(float, resp.split()))
    except: return None

def move_to(x, y, z):
    send_stage_cmd(f"!moa {x} {y} {z}")
    time.sleep(1)
    return get_position()


def wait_for_position(target_position, tolerance=0.1, max_attempts=10):
    attempts = 0
    time.sleep(1)
    while attempts < max_attempts:
        current_position = get_position()
        if current_position is None:
            print("Failed to read position.")
            break
        if all(abs(a - b) <= tolerance for a, b in zip(current_position, target_position)):
            print(f"Reached target position: {current_position}")
            return True
        attempts += 1
        print(f"Waiting for target position. Current: {current_position}, Target: {target_position}")
        time.sleep(2)
    print("Failed to reach target position.")
    return False

#%%
# ------------------- CAMERA -------------------
cam = Camera()
cam.init()
cam.alloc()
cam.set_colormode(ueye.IS_CM_MONO8)
cam.set_aoi(0, 0, 1280, 1024)  # Adjust AOI as needed
cam.set_fps(14.59)
cam.set_exposure(4.402)
cam.set_pixelclock(24)

def capture_and_save(name):
    img = cam.capture_image()
    while img is None:
        try:
            img = cam.capture_image()
        except Exception as e:
            print(f"Failed to capture image for {name}. Skipping this step, due to: {e}")
            return None
    path = os.path.join(SAVE_DIR, name)
    cv2.imwrite(path, img)
    print(f"📸 Saved {path}")
    return path  # Return the file path instead of the image data

#%%
# ------------------- MAIN PROCESS -------------------
print("🔁 Moving to initial saved position...")
move_to(*Initial_pos_saved)
wait_for_position(Initial_pos_saved)
#%%
init_position = get_position()
print(f"Initial Position: {init_position}") 

#%%
print("📍 Starting data collection...")
initial_x, initial_y, initial_z = Initial_pos_saved
target_x = TARGET_POSITION_X
step_size_um = 100  # 10 micrometers
current_x = initial_x
# Move to the target position in X direction with step size of 10 um
images_forward = []
positions_forward = []

while current_x >= target_x:
    move_to(current_x, initial_y, initial_z)
    position_x = get_position()
    print(f"Current Position: {position_x}") 
    if wait_for_position((current_x, initial_y, initial_z)):
        img_name = f"forward_{int(current_x):d}.png"
        time.sleep(1)
        img_path = capture_and_save(img_name)
        if img_path is not None:
            images_forward.append(img_path)
            positions_forward.append((current_x, initial_y, initial_z))
    current_x -= step_size_um

# Return to the initial position with step size of 10 um
images_backward = []
positions_backward = []

current_x = target_x #- step_size_um  # Start from one step before the target
while current_x <= initial_x:
    move_to(current_x, initial_y, initial_z)
    position_x = get_position()
    print(f"Current Position: {position_x}") 
    if wait_for_position((current_x, initial_y, initial_z)):
        img_name = f"backward_{int(current_x):d}.png"
        time.sleep(1)
        img_path = capture_and_save(img_name)
        if img_path is not None:
            images_backward.append(img_path)
            positions_backward.append((current_x, initial_y, initial_z))
    current_x += step_size_um


# Load images
images_forward = [cv2.imread(img_path, cv2.IMREAD_GRAYSCALE) for img_path in images_forward]
backward_images_sorted = [cv2.imread(img_path, cv2.IMREAD_GRAYSCALE) for img_path in reversed(images_backward)]

def compute_shift(img1, img2):
    if img1 is None or img2 is None:
        raise ValueError("One of the images is not loaded properly.")
    try:
        img1_float = np.float32(img1)
        img2_float = np.float32(img2)
        shift, confi = cv2.phaseCorrelate(img1_float, img2_float)
        return shift, confi
    except Exception as e:
        print(f"Error computing shift: {e}")
        return (0, 0), 0.0

# Compute shifts and precision errors when returning to the initial position
min_steps = min(len(images_forward), len(backward_images_sorted))
dx_micrometer, dy_micrometer, confidence_list = [], [], []
for i in range(min_steps):
    forward_img = images_forward[i]
    backward_img = backward_images_sorted[i]
    shift, confidence = compute_shift(forward_img, backward_img)
    dx_um = shift[0] * PIXEL_SIZE_UM
    dy_um = shift[1] * PIXEL_SIZE_UM
    dx_micrometer.append(dx_um)
    dy_micrometer.append(dy_um)
    confidence_list.append(confidence)
    print(f"\n🔬 Stage Repeatability Result for step {i+1}:")
    print(f"  Pixel shift: dx = {shift[0]:.4f}, dy = {shift[1]:.4f}")
    print(f"  Precision error: {dx_um:.2f} µm (X), {dy_um:.2f} µm (Y)")
    print(f"  Confidence: {confidence:.4f}")

# Create DataFrame
data = {
    "Step": list(range(1, min_steps + 1)),
    "Position_X_Forward": [pos[0] for pos in positions_forward[:min_steps]],
    "Position_Y_Forward": [pos[1] for pos in positions_forward[:min_steps]],
    "Position_Z_Forward": [pos[2] for pos in positions_forward[:min_steps]],
    "Position_X_Backward": [pos[0] for pos in positions_backward[-min_steps:]][::-1],
    "Position_Y_Backward": [pos[1] for pos in positions_backward[-min_steps:]][::-1],
    "Position_Z_Backward": [pos[2] for pos in positions_backward[-min_steps:]][::-1],
    "dx_micrometer": dx_micrometer,
    "dy_micrometer": dy_micrometer,
    "Confidence": confidence_list
}
df = pd.DataFrame(data)

# Save DataFrame to CSV
csv_path = os.path.join(SAVE_DIR, "stage_precision_data.csv")
df.to_csv(csv_path, index=False)
print(f"Data saved to {csv_path}")



def show_images(images, title):
    num_images = len(images)
    fig, axes = plt.subplots(1, num_images, figsize=(20, 5))
    if num_images == 1:
        axes = [axes]  # Ensure axes is always a list
    for ax, img in zip(axes, images):
        ax.imshow(img, cmap='gray')
        ax.set_title(title)
        ax.axis('off')
    plt.tight_layout()
    plt.show()

# Show forward images
show_images(images_forward, "Forward Images")

# Show backward images
show_images(backward_images_sorted, "Backward Images")

def show_interactive_overlays(forward_images, overlays, dx_values, dy_values, confidence_values):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 7))
    
    step_slider_ax = plt.axes([0.25, 0.05, 0.65, 0.03])
    step_slider = Slider(step_slider_ax, 'Step', 0, len(forward_images) - 1, valinit=0, valstep=1)
    
    toggle_button_ax = plt.axes([0.8, 0.9, 0.1, 0.05])
    toggle_button = Button(toggle_button_ax, 'Show Backward', color='lightblue')
    
    def update(val):
        step = int(val)
        ax1.clear()
        ax2.clear()
        
        ax1.imshow(forward_images[step], cmap='gray')
        ax1.set_title(f"Forward Image Step {step + 1}")
        ax1.axis('off')
        
        if toggle_button_status:
            overlay = overlays[step]
            ax2.imshow(overlay, cmap='gray', alpha=0.5)  # Semi-transparent overlay
            ax2.imshow(backward_images_sorted[step], cmap='coolwarm', alpha=0.3)  # Overlay backward image with different shade
        else:
            ax2.imshow(forward_images[step], cmap='gray')  # Show forward image only
            
        ax2.set_title(f"Overlay Image Step {step + 1}")
        ax2.axis('off')
        
        # Annotate the overlaid image with dx, dy, and confidence values
        text = f'dx: {dx_values[step]:.2f} µm\n' \
               f'dy: {dy_values[step]:.2f} µm\n' \
               f'Confidence: {confidence_values[step]:.4f}'
        ax2.text(10, 30, text, color='white', fontsize=10, bbox=dict(facecolor='black', alpha=0.5))
        
        plt.draw()
    
    def on_key(event):
        if event.key == 'right':
            step_slider.set_val(min(step_slider.val + 1, len(forward_images) - 1))
        elif event.key == 'left':
            step_slider.set_val(max(step_slider.val - 1, 0))
    
    def toggle_backward(val):
        nonlocal toggle_button_status
        toggle_button_status = not toggle_button_status
        update(step_slider.val)
    
    toggle_button_status = True
    toggle_button.on_clicked(toggle_backward)
    step_slider.on_changed(update)
    fig.canvas.mpl_connect('key_press_event', on_key)
    update(0)  # Initialize the first image
    plt.show()

# Compute overlaid images and annotations
overlays = []
dx_values = []
dy_values = []
confidence_values = []
for i in range(len(images_forward)):
    forward_img = images_forward[i]
    backward_img = backward_images_sorted[i]
    shift, confidence = compute_shift(forward_img, backward_img)
    dx_um = shift[0] * PIXEL_SIZE_UM
    dy_um = shift[1] * PIXEL_SIZE_UM
    
    # Translate the forward image by the computed shift
    translation_matrix = np.float32([[1, 0, -dx_um], [0, 1, -dy_um]])
    translated_img = cv2.warpAffine(forward_img, translation_matrix, (forward_img.shape[1], forward_img.shape[0]))
    
    # Create overlay image
    overlay = cv2.addWeighted(translated_img, 0.5, backward_img, 0.5, 0)
    overlays.append(overlay)
    
    dx_values.append(dx_um)
    dy_values.append(dy_um)
    confidence_values.append(confidence)

# Show interactive overlays with annotations
show_interactive_overlays(images_forward, overlays, dx_values, dy_values, confidence_values)

def show_static_plots(df):
    plt.figure(figsize=(12, 8))
    
    # Plot dx_micrometer vs Step
    plt.subplot(2, 2, 1)
    plt.plot(df['Step'], df['dx_micrometer'], marker='o')
    plt.title('dx_micrometer vs Step')
    plt.xlabel('Step')
    plt.ylabel('dx (µm)')
    plt.minorticks_on()
    plt.grid(which='both', linestyle='--', linewidth=0.5)
    plt.grid(which='minor', linestyle=':', linewidth=0.5)
    
    # Plot dy_micrometer vs Step
    plt.subplot(2, 2, 2)
    plt.plot(df['Step'], df['dy_micrometer'], marker='o', color='orange')
    plt.title('dy_micrometer vs Step')
    plt.xlabel('Step')
    plt.ylabel('dy (µm)')
    plt.minorticks_on()
    plt.grid(which='both', linestyle='--', linewidth=0.5)
    plt.grid(which='minor', linestyle=':', linewidth=0.5)
    
    # Plot Confidence vs Step
    plt.subplot(2, 2, 3)
    plt.plot(df['Step'], df['Confidence'], marker='s', color='green')
    plt.title('Confidence vs Step')
    plt.xlabel('Step')
    plt.ylabel('Confidence')

    plt.minorticks_on()
    plt.grid(which='both', linestyle='--', linewidth=0.5)
    plt.grid(which='minor', linestyle=':', linewidth=0.5)
    # Adjust layout
    plt.tight_layout()
    plt.show()


# Show static plots
show_static_plots(df)

# %%
cam.exit()
ser_stage.close()