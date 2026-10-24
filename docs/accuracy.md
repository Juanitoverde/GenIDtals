# Accuracy

## How a decision is made

1. **Decode and gate.** The input is decoded under size limits. A 0–100 quality
   score combines sharpness, exposure, contrast, ROI coverage and feature
   detail. Captures below the level's minimum are rejected with actionable
   issues and are not counted as failed attempts.
2. **Normalize.** The region of interest is segmented, its principal axis is
   rotated upright, and it is cropped, resized to 256×256 and equalized
   with CLAHE.
3. **Keypoint verification.** Up to 300 SIFT keypoints are matched with
   RootSIFT, Lowe's ratio test (0.85) and a RANSAC similarity transform
   (5 px, scale limited to 0.75–1.33). Only geometrically consistent, unique
   correspondences count.
4. **Registration.** With 12 or more inliers, the probe is warped into the
   reference's frame, and the position-dependent modalities are re-extracted
   from the registered image. This makes them insensitive to rotation (including
   upside-down captures), scale and residual normalization error.
5. **Modality scores**, each in [0, 1]:
   keypoints (inliers mapped linearly from 8 to 48),
   vascular (normalized cross-correlation of vesselness maps, ±2 cell shift),
   texture (wavelet log-energy distance and LBP χ²),
   follicular (layout correlation, nearest-neighbour spacing and count).
6. **Fusion and decision.** A weighted mean gives a 0–100 score, compared with
   the security level's calibrated threshold. With several references, the
   best-matching one decides.

## Benchmark

The *hard* synthetic generator (`genidtals.synthetic`, `hard=True`) simulates:

- arbitrary in-plane rotation
- ±15% scale
- ±30 px shift
- elastic tissue deformation of 2–5 px
- a ±20% low-frequency illumination field
- ±20% gain and ±20 offset
- 50% chance of a partial occlusion
- 0–1 px blur
- σ 2–5 noise

Each identity has its own shape, vessel network, follicle layout and texture statistics.

`genidtals benchmark --subjects 60 --images 6 --seed 2026` gives 3 enrollment
images and 3 probes per subject:

| Metric | Value |
|---|---|
| Genuine / impostor comparisons | 180 / 10,620 |
| Equal error rate | 0.000% |
| Genuine score, min / mean | 86.9 / 93.4 |
| Impostor score, max / mean | 25.3 / 16.1 |
| Failure to enroll / acquire | 0 / 0 |
| FAR and FRR at every security level (thresholds 40 / 50 / 55 / 60) | 0 / 0 |

Across independent seeds (12–60 subjects each), genuine scores have stayed
≥ 83 and impostor scores ≤ 26.

Per-modality separation (d′) on this set:

| Modality | d′ |
|---|---|
| keypoints | effectively perfect (impostors never register) |
| vascular | ≈ 16 |
| follicular | ≈ 1.4 |
| texture | ≈ 0.8 |

Compared with v0.1, which used canonical grids only with no registration, on
the same hard conditions: EER went from 1.33% to 0%. Genuine-minimum and
impostor-maximum went from 19.6 and 58.5 (overlapping) to 83+ and 26
(a margin of more than 55 points).

## Limits of these numbers

Synthetic data is cleaner than reality. It has no sensor-specific artifacts,
no long-term anatomical change, no lookalikes, and no population-level
correlations. Keypoint matching is especially flattered: real impostors will
produce some geometrically consistent coincidences.

That is why:

- The shipped thresholds (40–60) are far stricter than the thresholds fitted
  on synthetic data (≈ 25–31). No level can be reached without keypoint
  agreement.
- Very low FAR targets (1e-5, 1e-6) cannot be observed empirically on any
  practical dataset. `calibrate` sets thresholds at the larger of the
  empirical quantile and a Gaussian tail extrapolation of impostor scores,
  plus a 3-point margin.

## Calibrating for your deployment

Collect captures from your actual sensor. Aim for at least 50 subjects with 6
or more images each, taken across sessions and days.

```
dataset/
  subject_001/ 1.png 2.png 3.png 4.png 5.png 6.png
  subject_002/ ...
```

```bash
genidtals calibrate --dataset dataset --enroll 3 --out calibration.json
genidtals --calibration calibration.json benchmark --dataset held_out_dataset
```

Then deploy with `EngineConfig(calibration="calibration.json")` or
`--calibration`. Always check on a held-out set of subjects that were not used
for calibration. The report includes EER, the genuine and impostor score
distributions, and FAR/FRR at each level's threshold.
