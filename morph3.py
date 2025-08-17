import os
import cv2
import numpy as np
import matplotlib.pyplot as plt

# ================== USER SETTINGS ==================
IMAGE_PATH      = r"D:\My Folder\Tests\2025_08_11 - Spotsize Measurement over Samples\Sample USAF Glass Spot without Backgound.png"  # change me
PIXEL_SIZE_UM_X = 5.3
PIXEL_SIZE_UM_Y = 5.3


# Preprocessing / masking
GAUSS_SIGMA     = 0.0    # set 0.0 to avoid smoothing; >0 to denoise slightly
MASK_MODE       = "percentile" # "percentile" | "otsu" | "absolute"
PERCENTILE_KEEP = 99.8
ABS_THRESHOLD   = 70
MORPH_CLOSE_IT  = 1

# Background subtraction (helps avoid a pedestal)
BACKGROUND_MODE = "auto"   # "auto" | "none"

# Pulse parameters
PULSE_ENERGY_J  = 100e-9   # 100 nJ per pulse
PULSE_WIDTH_S   = 40e-9    # 40 ns
REP_RATE_HZ     = 1000     # 1 kHz (for info only)

OUT_DIR         = os.path.join(os.path.dirname(IMAGE_PATH), "intensity_from_image_results")
os.makedirs(OUT_DIR, exist_ok=True)
# ===================================================

# ---- Load image ----
img_u8 = cv2.imread(IMAGE_PATH, cv2.IMREAD_GRAYSCALE)
if img_u8 is None:
    raise FileNotFoundError(f"Cannot read {IMAGE_PATH}")
H, W = img_u8.shape

# ---- Optional smoothing (keep 0.0 for raw look) ----
img_f = cv2.GaussianBlur(img_u8, (0,0), GAUSS_SIGMA).astype(np.float64) if GAUSS_SIGMA > 0 else img_u8.astype(np.float64)

# ---- Build mask for the beam region ----
if MASK_MODE == "percentile":
    thr = np.percentile(img_f, PERCENTILE_KEEP)
    mask = img_f >= thr
elif MASK_MODE == "otsu":
    _, bw = cv2.threshold(img_u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = bw > 0
elif MASK_MODE == "absolute":
    mask = img_u8 >= ABS_THRESHOLD
else:
    raise ValueError("Invalid MASK_MODE")

if MORPH_CLOSE_IT > 0:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3,3))
    bw = (mask.astype(np.uint8) * 255)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k, iterations=MORPH_CLOSE_IT)
    mask = bw > 0

# If mask tiny, relax it
if mask.sum() < 50:
    thr = np.percentile(img_f, 98.0)
    mask = img_f >= thr

# ---- Background subtraction (optional) ----
weights = img_f.copy()
if BACKGROUND_MODE == "auto":
    # estimate background from outside mask (median)
    outside = weights[~mask]
    bg = float(np.median(outside)) if outside.size else 0.0
    weights = np.clip(weights - bg, 0, None)

# ---- Convert to physical intensity using energy + pulse width ----
pix_w_m = PIXEL_SIZE_UM_X * 1e-6
pix_h_m = PIXEL_SIZE_UM_Y * 1e-6
pix_area_m2 = pix_w_m * pix_h_m

weights[~mask] = 0.0
sum_w = weights.sum()
if sum_w <= 0:
    raise RuntimeError("No signal after masking; adjust thresholds or background subtraction.")

# Fluence per pixel (J/m^2) so that total energy equals PULSE_ENERGY_J
fluence_J_m2 = (weights / sum_w) * (PULSE_ENERGY_J / pix_area_m2)

# Irradiance (W/m^2), assuming top-hat pulse of 40 ns
irradiance_W_m2 = fluence_J_m2 / PULSE_WIDTH_S
irradiance_W_cm2 = irradiance_W_m2 / 1e4

# ---- Second moments on the *real intensity map* (ISO 11146) ----
I = irradiance_W_m2.copy()
I[~mask] = 0.0
total_I = I.sum()

x_idx = np.arange(W); y_idx = np.arange(H)
X_px, Y_px = np.meshgrid(x_idx, y_idx)
X_um = X_px * PIXEL_SIZE_UM_X
Y_um = Y_px * PIXEL_SIZE_UM_Y

x_mean_um = (I * X_um).sum() / total_I
y_mean_um = (I * Y_um).sum() / total_I

dx = X_um - x_mean_um
dy = Y_um - y_mean_um
Sxx = (I * dx * dx).sum() / total_I
Syy = (I * dy * dy).sum() / total_I
Sxy = (I * dx * dy).sum() / total_I
Cov = np.array([[Sxx, Sxy],[Sxy, Syy]])

eigvals, eigvecs = np.linalg.eigh(Cov)
order = np.argsort(eigvals)[::-1]
eigvals = eigvals[order]; eigvecs = eigvecs[:, order]
v_major = eigvecs[:,0]; v_minor = eigvecs[:,1]
sigma_major_um = np.sqrt(eigvals[0]); sigma_minor_um = np.sqrt(eigvals[1])
D4_major_um = 4.0 * sigma_major_um
D4_minor_um = 4.0 * sigma_minor_um
spot_size_um = 0.5 * (D4_major_um + D4_minor_um)
angle_rad = np.arctan2(v_major[1], v_major[0])
angle_deg = float(np.degrees(angle_rad))

# ---- Extract profiles *from the real intensity map* (W/cm^2) ----
# Use ~pixel-step sampling to avoid oversmoothing
step_um = min(PIXEL_SIZE_UM_X, PIXEL_SIZE_UM_Y)
half_len_um = 0.75 * max(D4_major_um, D4_minor_um)
N = max(3, int(2*half_len_um/step_um) + 1)
s_um = np.linspace(-half_len_um, half_len_um, N)

x_line_major_um = x_mean_um + v_major[0] * s_um
y_line_major_um = y_mean_um + v_major[1] * s_um
x_line_minor_um = x_mean_um + v_minor[0] * s_um
y_line_minor_um = y_mean_um + v_minor[1] * s_um

x_major_px = np.clip(np.rint(x_line_major_um / PIXEL_SIZE_UM_X).astype(int), 0, W-1)
y_major_px = np.clip(np.rint(y_line_major_um / PIXEL_SIZE_UM_Y).astype(int), 0, H-1)
x_minor_px = np.clip(np.rint(x_line_minor_um / PIXEL_SIZE_UM_X).astype(int), 0, W-1)
y_minor_px = np.clip(np.rint(y_line_minor_um / PIXEL_SIZE_UM_Y).astype(int), 0, H-1)

prof_major = irradiance_W_cm2[y_major_px, x_major_px]
prof_minor = irradiance_W_cm2[y_minor_px, x_minor_px]

# ---- FWHM from profiles at 0.5*I0 (peak of that profile) ----
def fwhm_from_profile(s_axis_um, vals):
    peak = float(np.max(vals))
    hm = 0.5 * peak
    above = vals >= hm
    idx = np.where(np.diff(above.astype(int)) != 0)[0]
    if idx.size < 2:
        return np.nan, None, None, peak, hm
    segs = []
    for k in range(0, len(idx)-1, 2):
        i1, i2 = idx[k], idx[k+1]
        # linear interpolate left crossing
        x1, x2 = s_axis_um[i1], s_axis_um[i1+1]
        y1, y2 = vals[i1], vals[i1+1]
        sL = x1 + (hm - y1) * (x2 - x1) / (y2 - y1 + 1e-12)
        # and right crossing
        x1, x2 = s_axis_um[i2], s_axis_um[i2+1]
        y1, y2 = vals[i2], vals[i2+1]
        sR = x1 + (hm - y1) * (x2 - x1) / (y2 - y1 + 1e-12)
        segs.append((sL, sR, sR - sL))
    sL, sR, width = max(segs, key=lambda t: t[2])
    return width, sL, sR, peak, hm

FWHM_major_um, sL_major, sR_major, I0_major, HM_major = fwhm_from_profile(s_um, prof_major)
FWHM_minor_um, sL_minor, sR_minor, I0_minor, HM_minor = fwhm_from_profile(s_um, prof_minor)

# ---- Print summary ----
avg_power_W = PULSE_ENERGY_J * REP_RATE_HZ
print("\n===== Intensity from IMAGE (energy-normalized) =====")
print(f"Average power: {avg_power_W*1e3:.3f} mW (E={PULSE_ENERGY_J*1e9:.1f} nJ @ {REP_RATE_HZ} Hz)")
print(f"Centroid (µm): x={x_mean_um:.2f}, y={y_mean_um:.2f}")
print(f"D4σ Major (µm): {D4_major_um:.2f}  |  D4σ Minor (µm): {D4_minor_um:.2f}")
print(f"Spot size (avg D4σ) (µm): {spot_size_um:.2f}")
print(f"FWHM Major (µm): {FWHM_major_um:.2f}  |  FWHM Minor (µm): {FWHM_minor_um:.2f}")
print(f"Peak I0 (major line): {I0_major:.3e} W/cm²  |  Peak I0 (minor line): {I0_minor:.3e} W/cm²")

# ---- Annotated image: green fit + axes + Spot size + FWHM ----
overlay = cv2.cvtColor(img_u8, cv2.COLOR_GRAY2BGR)

# Draw second-moment ellipse (2σ semi-axes = D4σ/2)
center_px = (int(round(x_mean_um / PIXEL_SIZE_UM_X)), int(round(y_mean_um / PIXEL_SIZE_UM_Y)))
axes_px = (int(round((D4_major_um/2) / PIXEL_SIZE_UM_X)), int(round((D4_minor_um/2) / PIXEL_SIZE_UM_Y)))
cv2.ellipse(overlay, center_px, axes_px, np.degrees(angle_rad), 0, 360, (0,255,0), 2)

# Draw axis lines (±D4σ/2)
def um2px(pt): return (int(round(pt[0]/PIXEL_SIZE_UM_X)), int(round(pt[1]/PIXEL_SIZE_UM_Y)))
maj_a = np.array([x_mean_um, y_mean_um]) + v_major * (D4_major_um/2)
maj_b = np.array([x_mean_um, y_mean_um]) - v_major * (D4_major_um/2)
min_a = np.array([x_mean_um, y_mean_um]) + v_minor * (D4_minor_um/2)
min_b = np.array([x_mean_um, y_mean_um]) - v_minor * (D4_minor_um/2)
cv2.line(overlay, um2px(maj_a), um2px(maj_b), (255,0,0), 1)
cv2.line(overlay, um2px(min_a), um2px(min_b), (0,0,255), 1)
cv2.circle(overlay, center_px, 2, (0,0,255), -1)

# Text
y0 = 22; dy = 22
cv2.putText(overlay, f"Diameter Major: {D4_major_um:.1f} um | FWHM: {FWHM_major_um:.1f} um", (10, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2); y0+=dy
cv2.putText(overlay, f"Diameter Minor: {D4_minor_um:.1f} um | FWHM: {FWHM_minor_um:.1f} um", (10, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2); y0+=dy
cv2.putText(overlay, f"Spot size (avg): {spot_size_um:.1f} um", (10, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2); y0+=dy
cv2.putText(overlay, f"Peak Intensity (I0): {I0_major:.3e} W/cm^2", (10, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)
annotated_path = os.path.join(OUT_DIR, os.path.splitext(os.path.basename(IMAGE_PATH))[0] + "_annotated.png")
cv2.imwrite(annotated_path, overlay)

# ---- Plot profiles (W/cm^2) with shaded FWHM ----
base = os.path.splitext(os.path.basename(IMAGE_PATH))[0]

plt.figure(figsize=(8,5))
plt.plot(s_um, prof_major, label="Major axis")
plt.axhline(HM_major, linestyle="--", linewidth=1, color='gray', label="Half max")
plt.xlabel("Distance (µm)"); plt.ylabel("Intensity (W/cm²)")
plt.title(f"Major Axis — FWHM={FWHM_major_um:.1f} µm, I₀={I0_major:.2e} W/cm²")
plt.legend(); plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, f"{base}_major_profile.png"), dpi=200)
plt.close()

plt.figure(figsize=(8,5))
plt.plot(s_um, prof_minor, label="Minor axis", color='orange')
plt.axhline(HM_minor, linestyle="--", linewidth=1, color='gray', label="Half max")
plt.xlabel("Distance (µm)"); plt.ylabel("Intensity (W/cm²)")
plt.title(f"Minor Axis — FWHM={FWHM_minor_um:.1f} µm, I₀={I0_minor:.2e} W/cm²")
plt.legend(); plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, f"{base}_minor_profile.png"), dpi=200)
plt.close()

print(f"\n✅ Saved annotated image and intensity plots in:\n{OUT_DIR}")