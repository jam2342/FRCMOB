import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { OnDeviceRun } from './OnDeviceRun';
import type { CalibrationCapture } from './FieldCalibration';
import type { CapturedFrame } from './MatchRecorder';
vi.mock('../../api', () => ({ getEventSchedule: vi.fn(async () => ({matches:[{match_key:'2026test_qm1',red:[{team_key:'frc1'}],blue:[{team_key:'frc2'}]}]})), syncOnDeviceSession: vi.fn() }));
vi.mock('./FieldCalibration', () => ({ FieldCalibration: ({onCalibrated}: {onCalibrated:(cal:CalibrationCapture)=>void}) => <button onClick={() => onCalibrated({homography:[[1,0,0],[0,1,0],[0,0,1]],inverse:[[1,0,0],[0,1,0],[0,0,1]],rmseM:0,referenceFrame:{width:1,height:1,data:new Float32Array(1)}})}>Calibrate</button> }));
vi.mock('./MatchRecorder', () => ({ MatchRecorder: ({onFrame,onRecordingChange,onBeforeCapture}: {onFrame:(frame:CapturedFrame)=>void; onRecordingChange:(busy:boolean)=>void; onBeforeCapture:()=>boolean}) => <>
  <button onClick={() => onFrame({timeSec:0,detections:[],homography:[[1,0,0],[0,1,0],[0,0,1]]})}>Capture frame</button>
  <button onClick={() => { if (onBeforeCapture()) onRecordingChange(true); }}>Start capture</button>
  <button onClick={() => onRecordingChange(false)}>Stop capture</button>
</> }));
vi.mock('./VideoFileProcessor', () => ({VideoFileProcessor: () => <p>Video source</p>}));
vi.mock('./useSavedRuns', () => ({useSavedRuns: () => ({sessions:[]})}));
vi.mock('../offline/RecorderOfflineStatus', () => ({RecorderOfflineStatus: () => null}));
vi.mock('./RunResults', () => ({RunResults: () => null}));
vi.mock('./robotPaths', () => ({buildRobotPaths: () => [{pathId:1,alliance:'red',thumb:null,points:[{timeSec:0,zoneKey:null},{timeSec:1,zoneKey:null},{timeSec:2,zoneKey:null}]}]}));
vi.mock('./trackProduction', () => ({assemblePointsByTeam: () => ({frc1:[]})}));
vi.mock('./TrackIdentityList', () => ({TrackIdentityList: ({onAssign}: {onAssign:(id:number,team:string)=>void}) => <button onClick={() => onAssign(1,'frc1')}>Assign robot</button>}));
vi.mock('./offlineStore', () => ({openDb:vi.fn(async () => ({close:vi.fn()})),getCalibration:vi.fn(async () => null),saveSession:vi.fn(async () => {})}));
afterEach(() => {cleanup(); localStorage.clear(); window.location.hash=''; vi.restoreAllMocks();});
async function capture() {
  window.location.hash='#/scouting/record?event=2026test&match=2026test_qm1';
  render(<OnDeviceRun />);
  fireEvent.change(await screen.findByLabelText('Match'), { target: { value: '2026test_qm1' } });
  fireEvent.click(await screen.findByText('Continue to calibration'));
  fireEvent.click(screen.getByText('Calibrate'));
}

it('confirms a replacement after Back, preserves a canceled capture, and clears old frames', async () => {
  await capture();
  fireEvent.click(screen.getByText('Capture frame'));
  fireEvent.click(screen.getByText('Identify robots (1 frames)'));
  fireEvent.click(screen.getByText('Assign robot'));
  fireEvent.click(screen.getByRole('button', { name: 'Back' }));
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  fireEvent.click(screen.getByText('Start capture'));
  expect(confirm).toHaveBeenCalledWith('Discard the previous capture and start a new one?');
  expect(screen.getByText('Identify robots (1 frames)')).toBeEnabled();
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByText('Start capture'));
  expect(screen.getByText('Identify robots')).toBeDisabled();
  fireEvent.click(screen.getByText('Capture frame'));
  fireEvent.click(screen.getByText('Stop capture'));
  expect(screen.getByText('Identify robots (1 frames)')).toBeEnabled();
  fireEvent.click(screen.getByText('Identify robots (1 frames)'));
  expect(screen.getByRole('button', {name:'Save & view results'})).toBeDisabled();
});
it('explains a missing saved calibration', async () => {
  window.location.hash='#/scouting/record?event=2026test&match=2026test_qm1';
  render(<OnDeviceRun />);
  fireEvent.change(await screen.findByLabelText('Match'), { target: { value: '2026test_qm1' } });
  fireEvent.click(await screen.findByText('Continue to calibration'));
  fireEvent.click(screen.getByText('Use saved calibration'));
  await screen.findByText('No calibration saved on this device. Capture or upload a field photo first.');
});
