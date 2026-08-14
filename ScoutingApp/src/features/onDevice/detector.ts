// In-browser FRC robot detector via onnxruntime-web. Loads the exported YOLO model
// (frc_robot_detector_v2.onnx), runs inference on a captured frame, and returns
// field-ready boxes. WebGPU when available (fast), WASM fallback (works everywhere).
// The heavy decode/NMS math lives in yoloDecode.ts so it can be unit-tested without ort.

import * as ort from 'onnxruntime-web';

import { type Box, type Letterbox, letterboxParams, postprocess } from './yoloDecode';

const INPUT_SIZE = 640;

export type Detector = {
  session: ort.InferenceSession;
  inputName: string;
  outputName: string;
};

export async function createDetector(
  modelUrl: string,
  executionProviders: string[] = ['webgpu', 'wasm'],
): Promise<Detector> {
  const session = await ort.InferenceSession.create(modelUrl, {
    executionProviders,
    graphOptimizationLevel: 'all',
  });
  return { session, inputName: session.inputNames[0], outputName: session.outputNames[0] };
}

// Letterbox a frame onto a 640x640 NCHW float32 tensor (RGB, 0..1). Accepts anything
// drawImage takes (video/image/canvas/bitmap). Returns the tensor + letterbox geometry.
function preprocess(
  frame: CanvasImageSource,
  width: number,
  height: number,
): { tensor: ort.Tensor; lb: Letterbox } {
  const lb = letterboxParams(width, height, INPUT_SIZE);
  const canvas = document.createElement('canvas');
  canvas.width = INPUT_SIZE;
  canvas.height = INPUT_SIZE;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('2d canvas context unavailable');
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, INPUT_SIZE, INPUT_SIZE);
  ctx.drawImage(frame, lb.padX, lb.padY, width * lb.scale, height * lb.scale);
  const { data } = ctx.getImageData(0, 0, INPUT_SIZE, INPUT_SIZE);
  const area = INPUT_SIZE * INPUT_SIZE;
  const chw = new Float32Array(3 * area);
  for (let i = 0; i < area; i++) {
    chw[i] = data[i * 4] / 255; // R
    chw[area + i] = data[i * 4 + 1] / 255; // G
    chw[2 * area + i] = data[i * 4 + 2] / 255; // B
  }
  return { tensor: new ort.Tensor('float32', chw, [1, 3, INPUT_SIZE, INPUT_SIZE]), lb };
}

export async function detectRobots(
  detector: Detector,
  frame: CanvasImageSource,
  width: number,
  height: number,
  opts: { confThreshold?: number; iouThreshold?: number } = {},
): Promise<Box[]> {
  const { tensor, lb } = preprocess(frame, width, height);
  const result = await detector.session.run({ [detector.inputName]: tensor });
  const output = result[detector.outputName];
  const numAnchors = output.dims[output.dims.length - 1]; // [1, 5, N]
  return postprocess(output.data as Float32Array, numAnchors, lb, opts);
}
