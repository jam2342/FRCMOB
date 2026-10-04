// Sample an uploaded match straight from the file with WebCodecs instead of playing it.
//
// Playback is the ceiling once detection runs on the GPU: past 2x a browser stops
// presenting every frame (see SPEEDUP_MAX_RATE), so a 160 s match took at least 80 s
// however fast the detector was. Decoding the file directly runs as fast as the
// hardware decoder, and the sampler takes exactly the frames it wants.
//
// Anything this path can't handle -- no WebCodecs, a codec the device can't decode, a
// rotated phone video (the <video> element applies the rotation; a decoder doesn't) --
// throws WebCodecsUnsupported before the first frame, and the caller plays the video
// instead.

import { createFile, DataStream, Endianness, MP4BoxBuffer, type Sample } from 'mp4box';

import { advanceSampleTarget } from './samplingRate';

export class WebCodecsUnsupported extends Error {}

const READ_CHUNK_BYTES = 8 * 1024 * 1024;
const MAX_DECODE_QUEUE = 12; // chunks in flight inside the decoder
const MAX_READY_FRAMES = 3; // decoded samples waiting for the detector (GPU memory)
const LOW_WATER_CHUNKS = 240; // read more of the file when fewer encoded chunks wait

export type WebCodecsSampleOptions = {
  startSec: number;
  endSec: number;
  intervalSec: number;
  signal?: AbortSignal;
  // Called once per sampled frame, one at a time; the frame is closed afterwards.
  onFrame: (frame: VideoFrame, timeSec: number) => Promise<void>;
};

type Box = { write: (stream: DataStream) => void };
type SampleEntry = { avcC?: Box; hvcC?: Box; vpcC?: Box; av1C?: Box };

const IDENTITY_MATRIX = [65536, 0, 0, 0, 65536, 0, 0, 0, 1073741824];

function isIdentity(matrix: ArrayLike<number> | undefined): boolean {
  if (!matrix) return true;
  return IDENTITY_MATRIX.every((v, i) => matrix[i] === v);
}

// The codec's configuration record (avcC/hvcC/...) without its 8-byte box header: the
// `description` a VideoDecoder needs for AVC/HEVC.
function codecDescription(entries: SampleEntry[]): Uint8Array | undefined {
  for (const entry of entries) {
    const box = entry.avcC ?? entry.hvcC ?? entry.vpcC ?? entry.av1C;
    if (box) {
      const stream = new DataStream(undefined, 0, Endianness.BIG_ENDIAN);
      box.write(stream);
      return new Uint8Array(stream.buffer, 8);
    }
  }
  return undefined;
}

export function webCodecsAvailable(): boolean {
  return typeof VideoDecoder === 'function' && typeof EncodedVideoChunk === 'function';
}

export async function sampleFramesWithWebCodecs(
  file: Blob,
  opts: WebCodecsSampleOptions,
): Promise<{ sampled: number; lastSampleSec: number }> {
  if (!webCodecsAvailable()) throw new WebCodecsUnsupported('WebCodecs is not available');
  const { startSec, endSec, intervalSec, signal, onFrame } = opts;

  const mp4 = createFile();
  const chunks: EncodedVideoChunk[] = [];
  const ready: { frame: VideoFrame; t: number }[] = [];
  let readOffset = 0;
  let eof = false;
  let config: VideoDecoderConfig | null = null;
  let setupError: Error | null = null;
  let decodeError: Error | null = null;
  let nextTarget = -Infinity;
  let pastEnd = false;
  let sampled = 0;
  let lastSampleSec = -Infinity;
  let trackId = -1;

  mp4.onError = (_module: string, message: string) => {
    setupError = new WebCodecsUnsupported(`could not read the video container: ${message}`);
  };
  mp4.onReady = (info) => {
    const track = info.videoTracks[0];
    if (!track?.video) {
      setupError = new WebCodecsUnsupported('no video track');
      return;
    }
    if (!isIdentity(track.matrix as ArrayLike<number> | undefined)) {
      setupError = new WebCodecsUnsupported('rotated video');
      return;
    }
    const trak = mp4.getTrackById(track.id) as unknown as {
      mdia: { minf: { stbl: { stsd: { entries: SampleEntry[] } } } };
    };
    config = {
      codec: track.codec.startsWith('vp08') ? 'vp8' : track.codec,
      codedWidth: track.video.width,
      codedHeight: track.video.height,
      description: codecDescription(trak.mdia.minf.stbl.stsd.entries),
      optimizeForLatency: false,
    };
    trackId = track.id;
    mp4.setExtractionOptions(track.id, null, { nbSamples: 120 });
    mp4.start();
  };
  mp4.onSamples = (_id: number, _user: unknown, samples: Sample[]) => {
    for (const s of samples) {
      if (!s.data) continue;
      chunks.push(
        new EncodedVideoChunk({
          type: s.is_sync ? 'key' : 'delta',
          timestamp: Math.round((s.cts * 1e6) / s.timescale),
          duration: Math.round((s.duration * 1e6) / s.timescale),
          data: s.data,
        }),
      );
    }
    const last = samples[samples.length - 1];
    if (last) mp4.releaseUsedSamples(trackId, last.number + 1);
  };

  const readMore = async () => {
    if (eof) return;
    const end = Math.min(file.size, readOffset + READ_CHUNK_BYTES);
    const data = await file.slice(readOffset, end).arrayBuffer();
    const buf = MP4BoxBuffer.fromArrayBuffer(data, readOffset);
    readOffset = end;
    const next = mp4.appendBuffer(buf, end >= file.size);
    if (typeof next === 'number' && next > readOffset && next <= file.size) readOffset = next;
    if (end >= file.size) {
      eof = true;
      mp4.flush();
    }
  };

  // Parse until the decoder can be configured (moov may sit at the end of a phone file).
  while (!config && !setupError && !eof) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    await readMore();
  }
  // set inside the demuxer callbacks, which TypeScript can't follow
  const failure = setupError as Error | null;
  const decoderConfig = config as VideoDecoderConfig | null;
  if (failure) throw failure;
  if (!decoderConfig) throw new WebCodecsUnsupported('no decodable video track');
  const support = await VideoDecoder.isConfigSupported(decoderConfig);
  if (!support.supported) throw new WebCodecsUnsupported(`this device can't decode ${decoderConfig.codec}`);

  const halfFrameSec = 0.5 / 60;
  const decoder = new VideoDecoder({
    output: (frame) => {
      const t = frame.timestamp / 1e6;
      if (pastEnd || t < startSec - halfFrameSec) {
        frame.close();
        return;
      }
      if (t > endSec) {
        pastEnd = true;
        frame.close();
        return;
      }
      if (t + halfFrameSec >= nextTarget) {
        nextTarget = advanceSampleTarget(Math.max(nextTarget, startSec), t, intervalSec);
        ready.push({ frame, t });
      } else {
        frame.close();
      }
    },
    error: (e) => {
      decodeError = e instanceof Error ? e : new Error(String(e));
    },
  });
  decoder.configure(decoderConfig);

  const closeAll = () => {
    for (const r of ready.splice(0)) r.frame.close();
  };
  try {
    let flushed = false;
    for (;;) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      if (decodeError) throw decodeError as Error;
      const next = ready.shift();
      if (next) {
        try {
          await onFrame(next.frame, next.t);
        } finally {
          next.frame.close();
        }
        sampled += 1;
        lastSampleSec = next.t;
        continue;
      }
      if (pastEnd || flushed) break;
      if (chunks.length < LOW_WATER_CHUNKS && !eof) {
        await readMore();
        continue;
      }
      if (chunks.length === 0 && eof) {
        await decoder.flush();
        flushed = true;
        continue;
      }
      while (chunks.length && decoder.decodeQueueSize < MAX_DECODE_QUEUE && ready.length < MAX_READY_FRAMES) {
        decoder.decode(chunks.shift()!);
      }
      // Let the decoder emit before looking again.
      await new Promise<void>((resolve) => {
        const done = () => {
          decoder.removeEventListener('dequeue', done);
          resolve();
        };
        decoder.addEventListener('dequeue', done);
        setTimeout(done, 20);
      });
    }
  } finally {
    closeAll();
    if (decoder.state !== 'closed') decoder.close();
    mp4.stop();
  }
  return { sampled, lastSampleSec };
}
