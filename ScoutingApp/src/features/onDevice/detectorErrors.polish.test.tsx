import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MatchRecorder } from './MatchRecorder';
import { VideoFileProcessor } from './VideoFileProcessor';
import { createDeviceDetector } from './detector';
vi.mock('./detector', () => ({ createDeviceDetector: vi.fn(), detectRobots: vi.fn() }));
const pose = () => [[1,0,0],[0,1,0],[0,0,1]] as [[number,number,number],[number,number,number],[number,number,number]];
beforeEach(() => {
  vi.mocked(createDeviceDetector).mockRejectedValue(new Error('model at /models/private.onnx is missing'));
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('gives a scout-facing recorder error and retries without showing model paths', async () => {
  render(<MatchRecorder resolvePose={pose} onFrame={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', {name:'Start recording'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('The robot detector could not load.');
  expect(screen.queryByText(/private.onnx|VITE_ONDEVICE_MODEL_URL/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name:'Start recording'}));
  await screen.findByRole('alert');
  expect(createDeviceDetector).toHaveBeenCalledTimes(2);
  expect(console.warn).toHaveBeenCalledWith('Robot detector could not load', expect.any(Error));
});
it('gives the same actionable failure for uploaded video', async () => {
  render(<VideoFileProcessor resolvePose={pose} onFrame={vi.fn()} onComplete={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Choose a video'), {target:{files:[new File(['clip'],'match.mp4',{type:'video/mp4'})]}});
  await screen.findByText(/The robot detector could not load/);
  expect(screen.queryByText(/private.onnx|VITE_ONDEVICE_MODEL_URL/)).not.toBeInTheDocument();
});
it('checks replacement approval before opening either capture source', async () => {
  const view = render(<MatchRecorder resolvePose={pose} onFrame={vi.fn()} onBeforeCapture={() => false} />);
  fireEvent.click(screen.getByRole('button', {name:'Start recording'}));
  expect(createDeviceDetector).not.toHaveBeenCalled();
  view.unmount();
  render(<VideoFileProcessor resolvePose={pose} onFrame={vi.fn()} onComplete={vi.fn()} onBeforeCapture={() => false} />);
  fireEvent.change(screen.getByLabelText('Choose a video'), {target:{files:[new File(['clip'],'match.mp4',{type:'video/mp4'})]}});
  expect(createDeviceDetector).not.toHaveBeenCalled();
});
