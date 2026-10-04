// In-browser FRC robot detector via onnxruntime-web. Loads the exported YOLO model
// (frc_robot_detector_v2.onnx), runs inference on a captured frame, and returns
// field-ready boxes. WebGPU when available (fast), WASM fallback (works everywhere).
// The heavy decode/NMS math lives in yoloDecode.ts so it can be unit-tested without ort.

import * as ort from 'onnxruntime-web';
import { CPU_MODEL, GPU_MODEL, resolveModelUrl, type OnDeviceModel } from './modelArtifact';

import { cropPlan, type PixelRect } from './fieldCrop';
import { type Box, type Letterbox, letterboxParams, postprocess } from './yoloDecode';

// Fallback only. The real value comes from the loaded model, because the detector's
// input resolution is the single biggest lever on its accuracy: on the locked Einstein
// holdout this same model scores mAP@0.5 0.3402 at 640 and 0.7189 at 1280. Hard-coding
// 640 quietly pinned the on-device path to less than half the accuracy the server gets.
const DEFAULT_INPUT_SIZE = 640;
const DYNAMIC_BASE_SIZE = 896;

// ONNX Runtime has moved this metadata around between versions, so read it defensively
// and fall back rather than throwing -- a wrong guess here is a dimension error at the
// first inference, which is a miserable way to find out.
// A model exported with dynamic=True declares symbolic height/width; it can take a
// field-shaped crop instead of a square.
function hasDynamicInput(session: ort.InferenceSession, inputName: string): boolean {
  try {
    const meta = (session as unknown as {
      inputMetadata?: Record<string, { dimensions?: unknown[]; shape?: unknown[] }> | Array<{ name?: string; dimensions?: unknown[]; shape?: unknown[] }>;
    }).inputMetadata;
    if (!meta) return false;
    const entry = Array.isArray(meta)
      ? meta.find((m) => m?.name === inputName) ?? meta[0]
      : meta[inputName];
    const dims = entry?.dimensions ?? entry?.shape;
    return Array.isArray(dims) && dims.slice(-2).some((d) => typeof d !== 'number' || !(d > 0));
  } catch {
    return false;
  }
}

function readInputSize(session: ort.InferenceSession, inputName: string): number {
  try {
    const meta = (session as unknown as {
      inputMetadata?: Record<string, { dimensions?: number[]; shape?: number[] }> | Array<{ name?: string; dimensions?: number[]; shape?: number[] }>;
    }).inputMetadata;
    if (!meta) return DEFAULT_INPUT_SIZE;
    const entry = Array.isArray(meta)
      ? meta.find((m) => m?.name === inputName) ?? meta[0]
      : meta[inputName];
    const dims = entry?.dimensions ?? entry?.shape;
    // NCHW: the last dimension is width.
    const width = Array.isArray(dims) ? Number(dims[dims.length - 1]) : NaN;
    return Number.isFinite(width) && width > 0 ? width : DEFAULT_INPUT_SIZE;
  } catch {
    return DEFAULT_INPUT_SIZE;
  }
}

// The bundled models are chosen per engine (modelArtifact.ts); VITE_ONDEVICE_MODEL_URL
// swaps in one export for both, e.g. to try a new one against the benchmark.
const CONFIGURED_MODEL_URL = resolveModelUrl(import.meta.env.VITE_ONDEVICE_MODEL_URL);
// Named in "detector unavailable" messages.
export const ON_DEVICE_MODEL_URL = CONFIGURED_MODEL_URL ?? `${GPU_MODEL.url} or ${CPU_MODEL.url}`;
export const ON_DEVICE_MODEL_VERSION = String(
  import.meta.env.VITE_ONDEVICE_MODEL_VERSION || GPU_MODEL.version,
);

export type Detector = {
  session: ort.InferenceSession;
  inputName: string;
  outputName: string;
  executionProvider: string;
  inputSize: number; // square side the model was exported at
  dynamicInput: boolean; // accepts any input size (multiples of 32)
  modelVersion: string;
  confThreshold: number; // the loaded model's own cutoff
};

// WebAssembly inference uses several cores only on a cross-origin isolated page (the
// recorder's record.html). Measured on the field crop: 1 thread 498 ms, 2 threads 261 ms,
// 3 threads 185 ms, and 4+ slower again once efficiency cores join; phones have two
// performance cores plus efficiency cores, so this stays at 3 or fewer.
function wasmThreadCount(): number {
  if (!globalThis.crossOriginIsolated) return 1;
  const cores = globalThis.navigator?.hardwareConcurrency || 2;
  return Math.max(1, Math.min(3, Math.ceil(cores / 2)));
}

async function hasGpu(): Promise<boolean> {
  const gpu = (globalThis.navigator as { gpu?: { requestAdapter: () => Promise<unknown> } } | undefined)?.gpu;
  if (!gpu) return false;
  try {
    return Boolean(await gpu.requestAdapter());
  } catch {
    return false;
  }
}

// Which model this device will load, without loading it: what "get ready for offline"
// downloads ahead of an event.
export async function pickDeviceModel(): Promise<OnDeviceModel> {
  if (CONFIGURED_MODEL_URL) {
    return { url: CONFIGURED_MODEL_URL, version: ON_DEVICE_MODEL_VERSION, confThreshold: GPU_MODEL.confThreshold };
  }
  return (await hasGpu()) ? GPU_MODEL : CPU_MODEL;
}

// The detector this device should run: the full-size model on a GPU, the nano model on
// the CPU. The GPU is asked for before anything downloads, so a CPU-only phone never
// fetches the 38 MB model only to throw it away.
export async function createDeviceDetector(): Promise<Detector> {
  if (CONFIGURED_MODEL_URL) {
    return createDetector(await pickDeviceModel());
  }
  if (await hasGpu()) {
    try {
      return await createDetector(GPU_MODEL, ['webgpu']);
    } catch {
      // a GPU the runtime can't use (old drivers, missing features): fall through to the CPU
    }
  }
  return createDetector(CPU_MODEL, ['wasm']);
}

export async function createDetector(
  model: OnDeviceModel,
  executionProviders: string[] = ['webgpu', 'wasm'],
): Promise<Detector> {
  let lastError: unknown = null;
  ort.env.wasm.numThreads = wasmThreadCount();
  for (const executionProvider of executionProviders) {
    try {
      const session = await ort.InferenceSession.create(model.url, {
        executionProviders: [executionProvider],
        graphOptimizationLevel: 'all',
      });
      const inputName = session.inputNames[0];
      const dynamicInput = hasDynamicInput(session, inputName);
      return {
        session,
        inputName,
        outputName: session.outputNames[0],
        executionProvider,
        // A dynamic export has no fixed side; 896 is the square it was exported around.
        inputSize: dynamicInput ? DYNAMIC_BASE_SIZE : readInputSize(session, inputName),
        dynamicInput,
        modelVersion: model.version,
        confThreshold: model.confThreshold,
      };
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError instanceof Error
    ? lastError
    : new Error('No on-device inference provider is available');
}

// Draw `source` (a region of the frame) onto an inputW x inputH NCHW float32 tensor
// (RGB, 0..1) at `scale`, placed at (padX, padY) on a black canvas.
function toTensor(
  frame: CanvasImageSource,
  source: PixelRect,
  inputW: number,
  inputH: number,
  lb: Letterbox,
): ort.Tensor {
  const canvas = document.createElement('canvas');
  canvas.width = inputW;
  canvas.height = inputH;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('2d canvas context unavailable');
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, inputW, inputH);
  ctx.drawImage(
    frame,
    source.x, source.y, source.w, source.h,
    lb.padX, lb.padY, source.w * lb.scale, source.h * lb.scale,
  );
  const { data } = ctx.getImageData(0, 0, inputW, inputH);
  const area = inputW * inputH;
  const chw = new Float32Array(3 * area);
  for (let i = 0; i < area; i++) {
    chw[i] = data[i * 4] / 255; // R
    chw[area + i] = data[i * 4 + 1] / 255; // G
    chw[2 * area + i] = data[i * 4 + 2] / 255; // B
  }
  return new ort.Tensor('float32', chw, [1, 3, inputH, inputW]);
}

// Letterbox the whole frame into the model's square: the path for fixed-size models,
// or when there's no usable field region.
function preprocess(
  frame: CanvasImageSource,
  width: number,
  height: number,
  size: number,
): { tensor: ort.Tensor; lb: Letterbox } {
  const lb = letterboxParams(width, height, size);
  return { tensor: toTensor(frame, { x: 0, y: 0, w: width, h: height }, size, size, lb), lb };
}

// Crop to the field region at a fixed pixel budget (see fieldCrop.ts).
function preprocessCrop(frame: CanvasImageSource, roi: PixelRect): { tensor: ort.Tensor; lb: Letterbox } {
  const plan = cropPlan(roi);
  const lb: Letterbox = { scale: plan.scale, padX: 0, padY: 0, offsetX: roi.x, offsetY: roi.y };
  return { tensor: toTensor(frame, roi, plan.inputW, plan.inputH, lb), lb };
}

export async function detectRobots(
  detector: Detector,
  frame: CanvasImageSource,
  width: number,
  height: number,
  opts: { confThreshold?: number; iouThreshold?: number; roi?: PixelRect | null } = {},
): Promise<Box[]> {
  const { tensor, lb } = detector.dynamicInput && opts.roi
    ? preprocessCrop(frame, opts.roi)
    : preprocess(frame, width, height, detector.inputSize);
  const result = await detector.session.run({ [detector.inputName]: tensor });
  const output = result[detector.outputName];
  const numAnchors = output.dims[output.dims.length - 1]; // [1, 5, N]
  return postprocess(output.data as Float32Array, numAnchors, lb, opts);
}
