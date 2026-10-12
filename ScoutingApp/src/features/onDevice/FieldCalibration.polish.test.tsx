import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { FieldCalibrationPage } from '../../pages/FieldCalibrationPage';
vi.mock('./offlineStore', () => ({openDb:vi.fn(async () => ({close:vi.fn()})),saveCalibration:vi.fn()}));
vi.mock('./CameraCapture', () => ({CameraCapture:() => null}));
vi.mock('../../components/PageViewBar', () => ({PageViewBar:() => null}));
vi.mock('./homography', async original => ({...(await original<typeof import('./homography')>()),calibrateFromTaps: () => { throw new Error('singular matrix'); }}));
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

}

it('shows a reset instruction after invalid corners and returns to the first corner', async () => {
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  await calibrate();
  expect(screen.getByText('Reset taps and try again.')).toBeInTheDocument();
  expect(screen.queryByText(/corner 5|undefined|singular matrix/)).not.toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('Tap four different field corners');
  fireEvent.click(screen.getByText('Reset taps'));
  expect(screen.getByText(/Tap corner 1 of 4/)).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
