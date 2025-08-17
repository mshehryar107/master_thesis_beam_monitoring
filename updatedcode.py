#%%
import win32com.client
import time
import h5py
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import serial
from scipy.stats import binned_statistic
from pypyueye import Camera
from pyueye import ueye
from PIL import Image
import os
import sys

# ---------------------------- Camera Utility Function ----------------------------

def capture_and_save_image(save_dir, prefix="image"):
    """Capture and save image using pypyueye camera module."""
    try:
        cam = Camera()
        cam.init()
        cam.alloc()

        cam.set_aoi(0, 0, 1280, 1024)
        cam.set_fps(17)

        image = cam.capture_image()

        while image != 0:
            image = cam.capture_image()

        #image = cam.capture_image()
        #if image is None:
        #    print("Try 1 Failed!")
         #   try:
          #      image = cam.capture_image()
           #     print("Try 2")
            #except Exception as e:
             #   print(f"cam error: {e}")
              #  return

        img_array = np.array(image, dtype=np.uint8)

        timestamp = datetime.now().strftime("%H_%M_%S")
        filename = os.path.join(save_dir, f"{prefix}_{timestamp}.png")

        if img_array.ndim == 2:
            img = Image.fromarray(img_array)
        else:
            img = Image.fromarray(img_array[:, :, :3])
        img.save(filename)
        print(f"📸 Saved image: {filename}")

    except Exception as e:
        print(f"❌ Camera error: {e}")
    finally:
        cam.exit()

# ---------------------------- Energy Meter Initialization ----------------------------
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
deviceName, romVersion, serialNumber = sensor.GetDeviceInfo(handle)
serial_number, sensor_type, model_name = sensor.GetSensorInfo(handle, 0)
print(f"Connected to: {deviceName}, SN: {serialNumber}; Sensor: {sensor_type} {model_name}")

sensor.SetRange(handle, 0, 2)          # 200 nJ range
sensor.SetWavelength(handle, 0, 5)     # 1064 nm
sensor.SetMeasurementMode(handle, 0, 1) # Energy
sensor.SetThreshold(handle, 0, 0)      # Min threshold
sensor.ConfigureStreamMode(handle, 0, 0, 0)  # Turbo OFF
sensor.ConfigureStreamMode(handle, 0, 2, 0)  # Immediate OFF

# ---------------------------- Stage (Movement) Initialization ----------------------------
COM_PORT_STAGE   = 'COM3'
BAUDRATE_STAGE   = 57600

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

# ---------------------------- Temperature Sensor Initialization ----------------------------
COM_PORT_TEMP = 'COM15'
BAUDRATE_TEMP = 9600

ser_temp = serial.Serial(
    port=COM_PORT_TEMP,
    baudrate=BAUDRATE_TEMP,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=1
)

#
# check version
#time.sleep(0.5)
ser_temp.write(b'V\n')
time.sleep(0.5)
ver = ser_temp.readline().decode().strip()
time.sleep(0.5)
ser_temp.write(b'V\n')
ver = ser_temp.readline().decode().strip()
print(f"Temperature sensor version: {ver}")

#%% 
# ---------------------------- Data Collection ----------------------------
target_duration_minutes = 5
target_duration_seconds = target_duration_minutes * 60

TARGET_POSITION  = (4600, 800, 2900)  # absolute XYZ
MOVE_INTERVAL    = 30                  # seconds between moves

# === MOTION SEQUENCE ===
print("📍 Capturing initial position...")
x, y, z = get_position()
INITIAL_POSITION = x,y,z
print(f"✅ Initial Position: x={x}, y={y}, z={z}")

timestamps_sys = []
elapsed_seconds = []
energies = []
temperatures = []
statuses = []

save_directory = r"C:\Users\mshehryar\Desktop\Tests\2025_07_24(Updated)"

sensor.StartStream(handle, 0)
print("Starting data collection...")

t0 = time.time()
next_sample = t0
next_move   = t0 + MOVE_INTERVAL

while time.time() < t0 + target_duration_seconds:
    # 1) wait until next whole second
    next_sample += 1.0
    sleep_time = next_sample - time.time()
    if sleep_time > 0:
        time.sleep(sleep_time)
    
    # 2) read one energy sample
    values, ts, stats = sensor.GetData(handle, 0)

    if not values or stats[0] != 0:
       print("⚠️ Bad/no energy data, skipping this second")
       continue
    e = float(values[0])
    
    # 3) read one temperature sample
    ser_temp.write(b'T\n')
    try:
        temp = float(ser_temp.readline().decode().strip())
    except:  # noqa: E722
        try:  
            temp = float(ser_temp.readline().decode().strip())

        except ValueError:
            print("⚠️ Bad temperature, skipping")
            continue
    
    # 4) record
    now     = datetime.now()
    elapsed = time.time() - t0
    #timestamps_sys.append(now.isoformat())
    elapsed_seconds.append(elapsed)
    energies.append(e)
    temperatures.append(temp)
    statuses.append(int(stats[0]))
    print(f"{now:%H:%M:%S} | Energy={e:.2e} J | Temp={temp:.2f} °C")
    #print(f"{now:%H:%M:%S} | Temp={temp:.2f} °C")


    if time.time() >= next_move:
        print("🔄 Moving to target")
        move_to(*TARGET_POSITION)
        if wait_for_position(TARGET_POSITION):
            try:
                capture_and_save_image(save_directory, prefix="snapshot")
            except Exception as e:
                print(f"Skipping image capture due to error : {e}")

            print("🔄 Returning to initial")
            move_to(*INITIAL_POSITION)
            if wait_for_position(INITIAL_POSITION):
                next_move += MOVE_INTERVAL
        else:
            print("Failed to reach target position, skipping this move.")
            next_move += MOVE_INTERVAL

print("Data collection complete.")
sensor.StopAllStreams()

# ---------------------------- Cleanup ----------------------------
sensor.Close(handle)
print("Energy meter closed.")
ser_stage.close()
ser_temp.close()
print("Serial ports closed.")

#%% ---------------------------- Save to HDF5 ----------------------------
filesavingtime = datetime.now()
filename = f'Energy_Temperature_Log_30mins_{filesavingtime.strftime("%Y_%m_%d-%H_%M_%S")}.h5'
with h5py.File(filename, "w") as h5f:
    #h5f.create_dataset("timestamps",      data=np.array(timestamps_sys, dtype='S32'))
    h5f.create_dataset("elapsed_seconds", data=np.array(elapsed_seconds, dtype='float64'))
    h5f.create_dataset("energies",        data=np.array(energies, dtype='float32'))
    h5f.create_dataset("temperatures",    data=np.array(temperatures, dtype='float32'))
    h5f.create_dataset("statuses",        data=np.array(statuses, dtype='int32'))
print(f"✅ Data saved to '{filename}'")

#%% ---------------------------- Plotting ----------------------------
with h5py.File(filename, "r") as h5r:
    secs       = h5r["elapsed_seconds"][:]     # in seconds
    mins       = secs / 60.0                   # convert to minutes
    energy_nJ  = h5r["energies"][:] * 1e9      # J → nJ
    temp_C     = h5r["temperatures"][:]        # °C

plt.figure(figsize=(10, 5))
plt.plot(mins, energy_nJ, '-o', markersize=3, label='Energy (nJ)')
plt.xlabel("Elapsed Time (minutes)")
plt.ylabel("Energy (nJ)")
plt.title("Energy vs Time")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.savefig(f"{filename}_Energy_persecond.png", dpi=300)
plt.show()

plt.figure(figsize=(10, 5))
plt.plot(mins, temp_C, '-o', markersize=3, label='Temperature (°C)')
plt.xlabel("Elapsed Time (minutes)")
plt.ylabel("Temperature (°C)")
plt.title("Temperature vs Time")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.savefig(f"{filename}_Temperature_persecond.png", dpi=300)
plt.show()


#%%
bin_width = 1
max_minute = mins.max()
bins = np.arange(0, max_minute + bin_width, bin_width)
bin_centers = (bins[:-1] + bins[1:]) / 2

energy_mean, _, _ = binned_statistic(mins, energy_nJ, statistic='mean', bins=bins)
energy_std, _, _ = binned_statistic(mins, energy_nJ, statistic='std', bins=bins)
temp_mean, _, _ = binned_statistic(mins, temp_C, statistic='mean', bins=bins)
temp_std, _, _ = binned_statistic(mins, temp_C, statistic='std', bins=bins)

# Energy Plot
plt.figure(figsize=(10, 5))
plt.errorbar(bin_centers, energy_mean , yerr=energy_std , fmt='-', color='black', ecolor='darkred', capsize=3, label='Energy (nJ)')
plt.xlabel("Elapsed Time (minutes)")
plt.ylabel("Energy (nJ)")
plt.title("Energy vs Time (Binned Mean with Std Dev)")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.savefig(f"{filename}_Energy_perminute.png", dpi=300)
plt.show()

plt.figure(figsize=(10, 5))
plt.errorbar(bin_centers, temp_mean, yerr=temp_std, fmt='-', capsize=3, color='darkblue', label='Temperature (°C)')
plt.xlabel("Elapsed Time (minutes)")
plt.ylabel("Temperature (°C)")
plt.title("Temperature vs Time (Binned Mean with Std Dev)")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.savefig(f"{filename}_Temperature_perminute.png", dpi=300)
plt.show()

# %%
