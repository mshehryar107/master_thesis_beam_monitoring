import os
import cv2
import numpy as np
import matplotlib.pyplot as plt
from scipy.ndimage import gaussian_filter, label, map_coordinates
from skimage.measure import EllipseModel

# ---------- CONFIG ----------
IMAGE_PATH = r"D:\My Folder\Tests\2025_08_11 - Spotsize Measurement over Samples\Sample Copper Spot without Background.png"  # Change here
PIXEL_WIDTH_UM = 5.3
PIXEL_HEIGHT_UM = 5.3
THRESHOLD_VALUE = 80  # adjustable
GAUSS_SIGMA = 2
OUT_DIR = os.path.join(os.path.dirname(IMAGE_PATH), "ellipse_results")
os.makedirs(OUT_DIR, exist_ok=True)
# ----------------------------

# Load grayscale image
gray_image = cv2.imread(IMAGE_PATH, cv2.IMREAD_GRAYSCALE)
if gray_image is None:
    raise FileNotFoundError(f"Cannot read {IMAGE_PATH}")

# Smooth to suppress noise
smoothed_image = gaussian_filter(gray_image, sigma=GAUSS_SIGMA)

# Threshold to isolate spot
binary_image = (smoothed_image > THRESHOLD_VALUE).astype(np.uint8) * 255

# Label connected regions
labeled_array, num_features = label(binary_image)
sizes = np.bincount(labeled_array.ravel())
if len(sizes) < 2:
    raise ValueError("No beam spot detected.")

# Pick largest blob
max_size_label = np.argmax(sizes[1:]) + 1
beam_spot = (labeled_array == max_size_label).astype(np.uint8)

# Fit ellipse
y, x = np.nonzero(beam_spot)
points = np.column_stack((x, y))
model = EllipseModel()
if not model.estimate(points):
    raise RuntimeError("Ellipse fitting failed.")

xc, yc, a, b, theta = model.params
diameter_major_um = 2 * a * PIXEL_WIDTH_UM
diameter_minor_um = 2 * b * PIXEL_HEIGHT_UM
spot_size_um = np.mean([diameter_major_um, diameter_minor_um])

# Print results
print(f"Major Axis: {diameter_major_um:.2f} µm")
print(f"Minor Axis: {diameter_minor_um:.2f} µm")
print(f"Average Spot Size: {spot_size_um:.2f} µm")

# Annotated image
result_image = cv2.cvtColor(gray_image, cv2.COLOR_GRAY2BGR)
center = (int(xc), int(yc))
axes = (int(abs(a)), int(abs(b)))
angle = np.rad2deg(theta)
cv2.ellipse(result_image, center, axes, angle, 0, 360, (0, 255, 0), 2)

cv2.putText(result_image, f"Major: {diameter_major_um:.2f} um", (10, 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
cv2.putText(result_image, f"Minor: {diameter_minor_um:.2f} um", (10, 40),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
cv2.putText(result_image, f"Spot: {spot_size_um:.2f} um", (10, 60),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

annotated_path = os.path.join(OUT_DIR, f"{os.path.splitext(os.path.basename(IMAGE_PATH))[0]}_annotated.png")
cv2.imwrite(annotated_path, result_image)

# Beam profiles
theta_rad = theta
cos_theta = np.cos(theta_rad)
sin_theta = np.sin(theta_rad)
t = np.linspace(-1, 1, 100)

x_major = xc + a * t * cos_theta - b * t * sin_theta
y_major = yc + a * t * sin_theta + b * t * cos_theta
x_minor = xc - a * t * sin_theta - b * t * cos_theta
y_minor = yc + a * t * cos_theta - b * t * sin_theta

major_profile = map_coordinates(smoothed_image, [y_major, x_major])
minor_profile = map_coordinates(smoothed_image, [y_minor, x_minor])
x_major_um = np.linspace(-a * PIXEL_WIDTH_UM, a * PIXEL_WIDTH_UM, 100)
y_minor_um = np.linspace(-b * PIXEL_HEIGHT_UM, b * PIXEL_HEIGHT_UM, 100)

plt.figure(figsize=(12, 6))
plt.subplot(1, 2, 1)
plt.plot(x_major_um, major_profile, label='Major Axis')
plt.xlabel('Position (µm)')
plt.ylabel('Intensity')
plt.title('Major Axis Profile')
plt.legend()

plt.subplot(1, 2, 2)
plt.plot(y_minor_um, minor_profile, label='Minor Axis', color='orange')
plt.xlabel('Position (µm)')
plt.ylabel('Intensity')
plt.title('Minor Axis Profile')
plt.legend()

profile_path = os.path.join(OUT_DIR, f"{os.path.splitext(os.path.basename(IMAGE_PATH))[0]}_profiles.png")
plt.tight_layout()
plt.savefig(profile_path, dpi=300)
plt.close()

print(f"\n✅ Annotated image and profile saved in:\n{OUT_DIR}")
