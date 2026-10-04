// Shared by the browser and release validation; no browser or Node dependencies.
//
// Two detectors, one per engine. Both are v4: YOLO11 trained (Lambda A100, 2026-09-30) on
// frc_v4_complete5 -- only training frames with at least five robots boxed. The earlier
// training sets left about a third of the robots unboxed, which taught the model to see
// them as background; complete frames lifted recall on the locked Einstein holdout from
// 0.51 to 0.69. Both take a dynamic input so the detector reads a field-shaped crop
// (fieldCrop.ts). Replacing one means a new filename (the /models/ cache is a week long).
export type OnDeviceModel = {
  url: string;
  version: string;
  // below this a box is dropped; set per model from its holdout precision/recall curve
  confThreshold: number;
};

// GPU (WebGPU): the full-size model. Holdout mAP@0.5 0.779 (v2: 0.719); at 0.35,
// precision 0.905 / recall 0.678. On the labelled stands-view match it finds 25% more
// robots per frame than v2 with identity as good (95.7% of hand labels, 2 mixed paths).
export const GPU_MODEL: OnDeviceModel = {
  url: '/models/frc_robot_detector_v4_s.onnx',
  version: 'frc_robot_detector_v4_s_onnx',
  confThreshold: 0.35,
};

// CPU (WebAssembly): the nano model, ~3x less work per frame (82 vs 243 ms in WebKit) and a
// 10 MB download instead of 38. Holdout mAP@0.5 0.740 -- still above v2's full-size model;
// at 0.25, precision 0.906 / recall 0.604. Noisier than the full-size model on the match
// (93% of hand labels, 3 mixed paths), which is the price of fitting a CPU-only phone.
export const CPU_MODEL: OnDeviceModel = {
  url: '/models/frc_robot_detector_v4_n.onnx',
  version: 'frc_robot_detector_v4_n_onnx',
  confThreshold: 0.25,
};

export const BUNDLED_MODELS: OnDeviceModel[] = [GPU_MODEL, CPU_MODEL];

// A configured URL (VITE_ONDEVICE_MODEL_URL) replaces both, for testing another export.
export function resolveModelUrl(configured?: string): string | null {
  return configured?.trim() || null;
}
