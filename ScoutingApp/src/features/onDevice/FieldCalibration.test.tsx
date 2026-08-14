import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { FieldCalibration } from './FieldCalibration';

function stubObjectUrls(url = 'blob:field-photo') {
  const createObjectURL = vi.fn(() => url);
  const revokeObjectURL = vi.fn();
  Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true });
  Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true });
  return { createObjectURL, revokeObjectURL };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('FieldCalibration lifecycle', () => {
  it('revokes uploaded photo object URLs once the image loads', async () => {
    const { createObjectURL, revokeObjectURL } = stubObjectUrls();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);

    class LoadedImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      naturalWidth = 100;
      naturalHeight = 50;

      set src(_value: string) {
        queueMicrotask(() => this.onload?.());
      }
    }
    Object.defineProperty(globalThis, 'Image', { value: LoadedImage, configurable: true });

    render(<FieldCalibration />);
    const input = screen.getByLabelText(/upload field photo/i) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['field'], 'field.jpg', { type: 'image/jpeg' })] },
    });

    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith('blob:field-photo'));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
  });

  it('revokes a pending uploaded photo object URL on unmount', () => {
    const { revokeObjectURL } = stubObjectUrls();

    class PendingImage {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;

      set src(_value: string) {}
    }
    Object.defineProperty(globalThis, 'Image', { value: PendingImage, configurable: true });

    const { unmount } = render(<FieldCalibration />);
    const input = screen.getByLabelText(/upload field photo/i) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['field'], 'field.jpg', { type: 'image/jpeg' })] },
    });

    unmount();

    expect(revokeObjectURL).toHaveBeenCalledWith('blob:field-photo');
  });
});
