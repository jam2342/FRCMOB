import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { FieldCalibrationPage } from '../../pages/FieldCalibrationPage';
import { saveCalibration } from './offlineStore';
vi.mock('./offlineStore', () => ({openDb:vi.fn(async () => ({close:vi.fn()})),saveCalibration:vi.fn()}));
vi.mock('./CameraCapture', () => ({CameraCapture:() => null}));
vi.mock('../../components/PageViewBar', () => ({PageViewBar:() => null}));
vi.mock('./homography', async original => ({...(await original<typeof import('./homography')>()),calibrateFromTaps: () => ({homography:[[1,0,0],[0,1,0],[0,0,1]],inverse:[[1,0,0],[0,1,0],[0,0,1]],rmseM:0})}));
afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals();});
async function calibrate() {
  class LoadedImage {
    onload: (() => void)|null = null;
    naturalWidth=2;
    naturalHeight=2;
    set src(_value:string) {queueMicrotask(() => this.onload?.());}
  }
  vi.stubGlobal('Image',LoadedImage);
  Object.defineProperty(URL,'createObjectURL',{value:vi.fn(() => 'blob:photo'),configurable:true});
  Object.defineProperty(URL,'revokeObjectURL',{value:vi.fn(),configurable:true});
  const context = {drawImage:vi.fn(),beginPath:vi.fn(),moveTo:vi.fn(),lineTo:vi.fn(),stroke:vi.fn(),arc:vi.fn(),fill:vi.fn(),fillText:vi.fn(),getImageData: () => ({data:new Uint8ClampedArray(16)})};
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
  const view=render(<MemoryRouter><FieldCalibrationPage /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText(/Upload field photo/),{target:{files:[new File(['photo'],'photo.jpg',{type:'image/jpeg'})]}});
  await screen.findByText(/Tap corner 1/);
  const canvas=view.container.querySelector('canvas')!;
  vi.spyOn(canvas,'getBoundingClientRect').mockReturnValue({left:0,top:0,width:2,height:2} as DOMRect);
  for (const [clientX,clientY] of [[0,0],[2,0],[2,2],[0,2]]) fireEvent.click(canvas,{clientX,clientY});
  fireEvent.click(screen.getByText('Use this calibration'));
}
it('confirms a saved standalone calibration and offers the recorder', async () => {
  vi.mocked(saveCalibration).mockResolvedValue();
  await calibrate();
  await screen.findByText('Calibration saved on this device.');
  expect(screen.getByRole('link',{name:'Continue to recorder'})).toHaveAttribute('href','/scouting/record');
});
it('shows a failed standalone save and keeps calibration available for retry', async () => {
  vi.mocked(saveCalibration).mockRejectedValue(new Error('full'));
  await calibrate();
  await screen.findByText('Could not save this calibration on your device. Keep this page open and try again.');
  expect(screen.getByText('Use this calibration')).toBeEnabled();
});
