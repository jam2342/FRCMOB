import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { RoomQrModal } from './QrShareModals';
import { copyToClipboard } from './scoutingPage.helpers';
vi.mock('qrcode', () => ({ default: { toCanvas: vi.fn(async () => {}) } }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function blockClipboard(fallback: () => boolean) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn(async () => { throw new Error('blocked'); }) } });
  Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn(fallback) });
}
it('honors the fallback boolean and removes its temporary textarea even when it throws', async () => {
  blockClipboard(() => false);
  expect(await copyToClipboard('room')).toBe(false);
  expect(document.querySelector('textarea')).toBeNull();
  blockClipboard(() => { throw new Error('blocked'); });
  expect(await copyToClipboard('room')).toBe(false);
  expect(document.querySelector('textarea')).toBeNull();
  blockClipboard(() => true);
  expect(await copyToClipboard('room')).toBe(true);
});
it('offers selectable text instead of claiming a blocked copy succeeded', async () => {
  blockClipboard(() => false);
  render(<RoomQrModal roomKey="room-test" onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Copy QR Text' }));
  await screen.findByRole('alert');
  expect(screen.queryByText('Copied!')).not.toBeInTheDocument();
  const text = screen.getByLabelText('Room code text') as HTMLTextAreaElement;
  expect(text.value).toContain('room-test');
  fireEvent.focus(text);
  expect(text.selectionEnd).toBe(text.value.length);
});

it('clears an earlier Copied flag when a subsequent copy fails', async () => {
  blockClipboard(() => true);
  render(<RoomQrModal roomKey="room-test" onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', {name:'Copy QR Text'}));
  await screen.findByRole('button', {name:'Copied!'});
  blockClipboard(() => false);
  fireEvent.click(screen.getByRole('button', {name:'Copied!'}));
  await screen.findByRole('alert');
  expect(screen.queryByText('Copied!')).not.toBeInTheDocument();
});
