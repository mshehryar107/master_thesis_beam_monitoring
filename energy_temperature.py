#%%
import win32com.client
import time
import h5py
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import serial
from scipy.stats import binned_statistic

#%%
# ---------------------------- Energy Meter Initialization ----------------------------
sensor = win32com.client.Dispatch("OphirLMMeasurement.CoLMMeasurement")
try:
    sensor.StopAllStreams()
except:
    pass
try:
    sensor.CloseAll()
except:
    pass
devices = sensor.ScanUSB()
if not devices:
    print("No Ophir devices found.")
    exit()
handle = sensor.OpenUSBDevice(devices[0])
deviceName, romVersion, serialNumber = sensor.GetDeviceInfo(handle)
serial_number, sensor_type, model_name = sensor.GetSensorInfo(handle, 0)
print(f"Connected to: {deviceName}, Serial: {serialNumber}, Sensor: {sensor_type} {model_name}")
sensor.SetRange(handle, 0, 2)  # 200 nJ range
sensor.SetWavelength(handle, 0, 5)  # 1064 nm
sensor.SetMeasurementMode(handle, 0, 1)  # Energy
sensor.SetThreshold(handle, 0, 0)  # Minimum threshold
sensor.ConfigureStreamMode(handle, 0, 0, 0)  # Turbo OFF
sensor.ConfigureStreamMode(handle, 0, 2, 0)  # Immediate OFF

#%%
# ---------------------------- Temperature Sensor Initialization ----------------------------
SERIAL_PORT = 'COM15'
BAUD_RATE = 9600
ser = serial.Serial(
    port=SERIAL_PORT,
    baudrate=BAUD_RATE,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=1
)
#%%
ser.write(b'V\n')
time.sleep(0.2)
sensor_version = ser.readline().decode("ascii").strip()
print(f"Temperature Sensor Version: {sensor_version}")

#%%
# ---------------------------- Data Collection ----------------------------
target_duration_minutes = 1200
skip_initial = 3
samples_collected = 0
timestamps_sys = []
energies = []
temperatures = []
statuses = []
elapsed_seconds = []

sensor.StartStream(handle, 0)
print("Starting data collection...")
start_time = time.time()

while True:
    values, ts, stats = sensor.GetData(handle, 0)
    if not values:
        time.sleep(0.5)
        continue
    for i in range(len(values)):
        elapsed_time = time.time() - start_time
        if samples_collected < skip_initial:
            samples_collected += 1
            continue
        if elapsed_time >= target_duration_minutes * 60:
            break
        now = datetime.now()
        ser.write(b'T\n')
        time.sleep(0.5)
        try:
            temperature = float(ser.readline().decode("ascii").strip())
        except ValueError:
            print("Skipping invalid temperature")
            continue
        if stats[i] != 0:
            print(f"Bad energy status: {stats[i]}, skipping...")
            continue
        timestamps_sys.append(now.isoformat())
        energies.append(float(values[i]))
        temperatures.append(temperature)
        statuses.append(int(stats[i]))
        elapsed_seconds.append(round(elapsed_time))
        print(f"{now.strftime('%H:%M:%S')} | Energy: {values[i]:.2e} J | Temp: {temperature:.2f} °C")
        samples_collected += 1
    if time.time() - start_time >= target_duration_minutes * 60:
        break
    time.sleep(0.5)

sensor.StopAllStreams()
sensor.Close(handle)
ser.close()
print("Data collection complete. Devices disconnected.")

#%%
# ---------------------------- Save to HDF5 ----------------------------
filename = 'Energy_Temperature_Log_1200mins.h5'
with h5py.File(filename, "w") as h5f:
    h5f.create_dataset("timestamps", data=np.array(timestamps_sys, dtype='S32'))
    h5f.create_dataset("elapsed_seconds", data=np.array(elapsed_seconds, dtype='int32'))
    h5f.create_dataset("energies", data=np.array(energies, dtype='float32'))
    h5f.create_dataset("temperatures", data=np.array(temperatures, dtype='float32'))
    h5f.create_dataset("statuses", data=np.array(statuses, dtype='int32'))
print(f"✅ Data saved to '{filename}'")

#%%
# ---------------------------- Plotting with Error Bars ----------------------------
#filename = 'Energy_Temperature_Log.h5'

with h5py.File(filename, "r") as h5r:
    minutes = np.array(h5r["elapsed_seconds"][:]) // 60
    energy_data = h5r["energies"][:] * 1e9  # J to nJ
    temp_data = h5r["temperatures"][:]

#%%
bin_width = 1
max_minute = minutes.max()
bins = np.arange(0, max_minute + bin_width, bin_width)
bin_centers = (bins[:-1] + bins[1:]) / 2

energy_mean, _, _ = binned_statistic(minutes, energy_data, statistic='mean', bins=bins)
energy_std, _, _ = binned_statistic(minutes, energy_data, statistic='std', bins=bins)
temp_mean, _, _ = binned_statistic(minutes, temp_data, statistic='mean', bins=bins)
temp_std, _, _ = binned_statistic(minutes, temp_data, statistic='std', bins=bins)

# Energy Plot
plt.figure(figsize=(10, 5))
plt.errorbar(bin_centers, energy_mean , yerr=energy_std , fmt='-', color='black', ecolor='darkred', capsize=3, label='Energy (nJ)')
plt.xlabel("Elapsed Time (minutes)")
plt.ylabel("Energy (nJ)")
plt.title("Energy vs Time (Binned Mean with Std Dev)")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.savefig(f"{filename}_Energy.png", dpi=300)
plt.show()

plt.figure(figsize=(10, 5))
plt.errorbar(bin_centers, temp_mean, yerr=temp_std, fmt='-', capsize=3, color='darkblue', label='Temperature (°C)')
plt.xlabel("Elapsed Time (minutes)")
plt.ylabel("Temperature (°C)")
plt.title("Temperature vs Time (Binned Mean with Std Dev)")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.savefig(f"{filename}_Temperature.png", dpi=300)
plt.show()

# %%
