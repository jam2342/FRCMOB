import { uploadPitPhoto } from '../../api';
import type { QueuedPitPhoto } from '../offline/pitPhotoQueue';

// Photos get downscaled client-side so uploads stay small on venue WiFi.
const PHOTO_MAX_DIMENSION = 1280;
const PHOTO_JPEG_QUALITY = 0.82;

export async function compressPhoto(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, PHOTO_MAX_DIMENSION / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('Could not encode photo'))), 'image/jpeg', PHOTO_JPEG_QUALITY);
    });
  } finally {
    bitmap.close();
  }
}

export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error('Could not read photo'));
    reader.readAsDataURL(blob);
  });
}

export async function uploadPhotoBlob(photo: { blob: Blob; eventKey: string; teamKey: string; scoutProfile?: string }) {
  return uploadPitPhoto({
    event_key: photo.eventKey,
    team_key: photo.teamKey,
    scout_profile: photo.scoutProfile || undefined,
    image_base64: await blobToDataUrl(photo.blob),
  });
}

// Returns the entry the server sent back, so an open page can show the photo as saved.
export async function uploadQueuedPitPhoto(item: QueuedPitPhoto) {
  const result = await uploadPhotoBlob({
    blob: new Blob([item.data], { type: item.type }),
    eventKey: item.eventKey,
    teamKey: item.teamKey,
    scoutProfile: item.scoutProfile,
  });
  return result.entry;
}

// Whether a failed upload was the connection, as opposed to the server refusing the photo.
export function isPhotoNetworkFailure(err: unknown): boolean {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return true;
  return err instanceof TypeError || /failed to fetch|load failed|network|timed out/i.test((err as Error)?.message || '');
}
