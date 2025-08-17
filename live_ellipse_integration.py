#%%
import cv2
import os
import sys
import time
import h5py
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
from PIL import Image, ImageDraw
import win32com.client
import serial
from scipy.stats import binned_statistic
from skimage.measure import label, regionprops
from scipy.ndimage import gaussian_filter, label as ndi_label
from skimage.measure import EllipseModel
from pypyueye import Camera
from pyueye import ueye

#%% ---------------------------- CONFIGURATION ----------------------------
TARGET_DURATION_MIN = 2            # total acquisition duration in minutes
MOVE_INTERVAL_SEC   = 30           # interval between moves and snapshots
COM_PORT_STAGE      = 'COM3'
BAUDRATE_STAGE      = 57600
COM_PORT_TEMP       = 'COM15'
BAUDRATE_TEMP       = 9600
SAVE_DIR            = r"C:\Users\mshehryar\Desktop\Tests\2025_07_28"
os.makedirs(SAVE_DIR, exist_ok=True)
TARGET_POSITION     = (4847.3, 859.3, 2959)  # XYZ for snapshot
ELLIPSE_THRESH      = 60           # hard-coded threshold for ellipse fitting
SIGMA_SMOOTH        = 2            # gaussian smoothing sigma

#%% ---------------------------- HELPER FUNCTION ----------------------------
def process_live_frame(frame, thresh=ELLIPSE_THRESH, sigma=SIGMA_SMOOTH):
    """
    Given a mono8 (grayscale) frame, apply gaussian smoothing,
    binary threshold, label components, fit an ellipse using EllipseModel,
    draw the ellipse in green, and return a 3-channel BGR image.
    """
    # assume frame is single-channel uint8
    gray = frame
    # smooth
    smoothed = gaussian_filter(gray.astype(float), sigma=sigma)
    # binary mask
    binary = (smoothed > thresh).astype(np.uint8) * 255

    kernel_open = cv2.getStructuringElement(cv2.MORPH_RECT,(3,3))
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel_open)

    kernel_v = cv2.getStructuringElement(cv2.MORPH_RECT, (3,25))
    closed_v = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel_v)

    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (100,3))
    processed = cv2.morphologyEx(closed_v, cv2.MORPH_CLOSE, kernel_h)

    labeled, num = ndi_label(processed)
    if num < 1:
        return cv2.merge([gray,gray,gray])
    
    # pick largest component
    sizes = np.bincount(labeled.ravel())
    if sizes.size <= 1:
        return cv2.merge([gray, gray, gray])
    max_lbl = np.argmax(sizes[1:]) + 1
    mask = (labeled == max_lbl)
    # fit ellipse
    y_pts, x_pts = np.nonzero(mask)
    pts = np.column_stack((x_pts, y_pts))
    
    model = EllipseModel()
    if not model.estimate(pts):
        return cv2.merge([gray, gray, gray])
    xc, yc, a, b, theta = model.params

    #beam waist
    spot_size_um = np.mean([2 * a * 5.3, 2 * b * 5.3])

    #annotated = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    # draw ellipse
    bgr = cv2.merge([gray, gray, gray])
    center = (int(round(xc)), int(round(yc)))
    axes = (int(round(a)), int(round(b)))
    angle = np.degrees(theta)
    cv2.ellipse(bgr, center, axes, angle, 0, 360, (0,255,0), 2)
    cv2.putText(bgr,f"Spot Size: {spot_size_um:.1f} um", (10,20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255,255,255), 1)
    return bgr

# Init Ophir energy meter
sensor = win32com.client.Dispatch("OphirLMMeasurement.CoLMMeasurement")
for _ in range(2):
    try:
        sensor.StopAllStreams()
        sensor.CloseAll()
    except:
        pass
devices = sensor.ScanUSB()
if not devices:
    raise RuntimeError("No Ophir devices found.")
handle = sensor.OpenUSBDevice(devices[0])
sensor.SetRange(handle, 0, 2)
sensor.SetWavelength(handle, 0, 5)
sensor.SetMeasurementMode(handle, 0, 1)
sensor.SetThreshold(handle, 0, 0)
sensor.ConfigureStreamMode(handle, 0, 0, 0)
sensor.ConfigureStreamMode(handle, 0, 2, 0)

#%% ---------------------------- STAGE INITIALIZATION ----------------------------
ser_stage = serial.Serial(
    port=COM_PORT_STAGE,
    baudrate=BAUDRATE_STAGE,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_TWO,
    timeout=1
)

ser_stage.write(('?ver' + '\r').encode())
time.sleep(0.2)
stagever = ser_stage.readline().decode().strip()
print(f"Tango stage version: {stagever}")
      
def send_stage_cmd(cmd, delay=0.2, expect_response=True):
    ser_stage.write((cmd + '\r').encode())
    time.sleep(delay)
    if expect_response:
        return ser_stage.readline().decode().strip()
    return ""

def get_position():
    resp = send_stage_cmd("?pos")
    if not resp:
        print("❌ Could not retrieve initial position.")
        ser_stage.close()
        exit()
    
    try:
        x, y, z = map(float, resp.split())
        return x, y, z
    except ValueError as e:
        print(f"❌ Error parsing position: {e}")
        ser_stage.close()
        exit()

def move_to(x, y, z):
    send_stage_cmd(f"!moa {x} {y} {z}")
    time.sleep(1)
    return get_position()

def wait_for_position(target_position, tolerance=0.1, max_attempts=5):
    attempts = 0
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

# high-precision config
print("🔧 Configuring stage precision...")
for cmd in [
    "!resolution 0.000001",    # 1 nm
    "!encperiod 0.001",        # 1 μm
    "!backlash x 1",
    "!backlash y 1",
    "!backlash z 1",
    "!gear x 10.0",
    "!gear y 10.0",
    "!gear z 10.0",
    "!pitch x 2.0",
    "!pitch y 2.0",
    "!pitch z 2.0",
]:
    send_stage_cmd(cmd)

#%% ---------------------------- TEMPERATURE SENSOR INITIALIZATION ----------------------------
ser_temp = serial.Serial(
    port=COM_PORT_TEMP,
    baudrate=BAUDRATE_TEMP,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=1
)
ser_temp.write(b'V\n')
time.sleep(0.5)
ver = ser_temp.readline().decode().strip()
ser_temp.write(b'V\n')
time.sleep(0.5)
ver = ser_temp.readline().decode().strip()
print(f"Temperature sensor version: {ver}")

#%% ---------------------------- CAMERA INITIALIZATION ----------------------------
cam = Camera(); cam.init(); cam.alloc()
cam.set_colormode(ueye.IS_CM_MONO8); cam.set_aoi(0,0,1280,1024); cam.set_fps(15.83); cam.set_exposure(22); cam.set_pixelclock(22)
cam.capture_video()
cv2.namedWindow('Live Fit', cv2.WINDOW_NORMAL); cv2.resizeWindow('Live Fit', 800,600)
print('▶️ Live ellipse-fit ongoing with hard-coded threshold...')

#%% ---------------------------- DATA COLLECTION ----------------------------
# === MOTION SEQUENCE ===
print("📍 Capturing initial position...")
x, y, z = get_position()
INITIAL_POSITION = x,y,z
print(f"✅ Initial Position: x={x}, y={y}, z={z}")

sensor.StartStream(handle,0)
start_t=time.time(); next_sample=start_t; next_move=start_t+MOVE_INTERVAL_SEC
elapsed_s, energies_J, temps_C, statuses = [],[],[],[]
while time.time()<start_t+TARGET_DURATION_MIN*60:
    next_sample+=1.0; dt=next_sample-time.time(); time.sleep(dt) if dt>0 else None
    vals,_,stats=sensor.GetData(handle,0)
    if not vals or stats[0]!=0: continue
    e=float(vals[0])
    ser_temp.write(b'T\n')
    try: tmp=float(ser_temp.readline().decode().strip())
    except: continue
    now=time.time(); elapsed_s.append(now-start_t); energies_J.append(e); temps_C.append(tmp);
    statuses.append(int(stats[0])); print(f"{datetime.now():%H:%M:%S} | E={e:.2e} J | T={tmp:.2f} °C")
    frame=cam.capture_image()
    if frame is not None:
        vis=process_live_frame(frame)
        cv2.imshow('Live Fit',vis); cv2.waitKey(1)
    if now>=next_move:
        print('🔄 Move → snapshot → return')
        move_to(*TARGET_POSITION)
        if wait_for_position(TARGET_POSITION):
            snap=cam.capture_image()
            if snap is not None:
                vis_snap=process_live_frame(snap)
                fname=f"snapshot_{datetime.now():%H_%M_%S}.png"; path=os.path.join(SAVE_DIR,fname)
                cv2.imwrite(path,vis_snap); print(f"📸 Saved {path}")
        move_to(*INITIAL_POSITION)
        if wait_for_position(INITIAL_POSITION): 
            next_move+=MOVE_INTERVAL_SEC

move_to(*INITIAL_POSITION)
if wait_for_position(INITIAL_POSITION): 
    print('✅ Acquisition complete')
# cleanup
sensor.StopAllStreams(); cam.stop_video(); cam.exit(); cv2.destroyAllWindows()
sensor.Close(handle); ser_stage.close(); ser_temp.close()

# Cleanup
cam.exit()
sensor.Close(handle)
ser_stage.close()
ser_temp.close()

#%% ---------------------------- SAVE TO HDF5 ----------------------------
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
h5file = f"EnergyTemp_{TARGET_DURATION_MIN}min_{stamp}.h5"
with h5py.File(h5file,'w') as f:
    f.create_dataset("elapsed_seconds", data=np.array(elapsed_s))
    f.create_dataset("energies", data=np.array(energies_J))
    f.create_dataset("temperatures", data=np.array(temps_C))
    f.create_dataset("statuses", data=np.array(statuses))
print(f"💾 Saved data to {h5file}")

#%% ---------------------------- PLOTTING ----------------------------
with h5py.File(h5file,'r') as f:
    secs=f['elapsed_seconds'][:]
    mins=secs/60.0
    e_nJ=f['energies'][:]*1e9
    t_C=f['temperatures'][:]
plt.figure(figsize=(10,5))
plt.plot(mins,e_nJ,'-o',markersize=3)
plt.xlabel("Time (min)")
plt.ylabel("Energy (nJ)")
plt.title("Energy vs Time")
plt.grid(True)
plt.tight_layout()
plt.show()

plt.figure(figsize=(10,5))
plt.plot(mins,t_C,'-o',markersize=3)
plt.xlabel("Time (min)")
plt.ylabel("Temperature (°C)")
plt.title("Temperature vs Time")
plt.grid(True)
plt.tight_layout()
plt.show()
