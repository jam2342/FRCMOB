import { isNativeApp } from '../../platform/runtime';

export type ShareOutcome = 'shared' | 'saved' | 'cancelled';

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '');
    reader.onerror = () => reject(reader.error ?? new Error('Could not read the picture.'));
    reader.readAsDataURL(blob);
  });
}

// The phone's share sheet with the picture and the link; where that can't take a file, the
// picture is saved instead (and the caller says so).
export async function sharePicture(blob: Blob, filename: string, text: string, url: string): Promise<ShareOutcome> {
  if (isNativeApp()) {
    const [{ Filesystem, Directory }, { Share }] = await Promise.all([import('@capacitor/filesystem'), import('@capacitor/share')]);
    const file = await Filesystem.writeFile({ path: `share/${Date.now()}-${filename}`, directory: Directory.Cache, data: await blobToBase64(blob), recursive: true });
    try {
      await Share.share({ title: text, text, url, files: [file.uri], dialogTitle: 'Share this result' });
      return 'shared';
    } catch (error) {
      if (error instanceof Error && /cancel/i.test(error.message)) return 'cancelled';
      throw error;
    }
  }
  const file = new File([blob], filename, { type: 'image/png' });
  const nav = navigator as Navigator & { canShare?: (data: ShareData) => boolean };
  if (typeof nav.share === 'function' && nav.canShare?.({ files: [file] })) {
    try {
      await nav.share({ files: [file], text, url });
      return 'shared';
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return 'cancelled';
      throw error;
    }
  }
  savePicture(blob, filename);
  return 'saved';
}

export function savePicture(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a');
    link.href = href;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(href), 2000);
  }
}
