import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { OnDeviceRun } from './OnDeviceRun';
import type { CalibrationCapture } from './FieldCalibration';
import type { CapturedFrame } from './MatchRecorder';
vi.mock('../../api', () => ({ getEventSchedule: vi.fn(async () => ({matches:[{match_key:'2026test_qm1',red:[{team_key:'frc1'}],blue:[{team_key:'frc2'}]}]})), syncOnDeviceSession: vi.fn() }));
vi.mock('./FieldCalibration', () => ({ FieldCalibration: ({onCalibrated}: {onCalibrated:(cal:CalibrationCapture)=>void}) => <button onClick={() => onCalibrated({homography:[[1,0,0],[0,1,0],[0,0,1]],inverse:[[1,0,0],[0,1,0],[0,0,1]],rmseM:0,referenceFrame:{width:1,height:1,data:new Float32Array(1)}})}>Calibrate</button> }));
vi.mock('./MatchRecorder', () => ({ MatchRecorder: ({onFrame,onRecordingChange}: {onFrame:(frame:CapturedFrame)=>void; onRecordingChange:(busy:boolean)=>void}) => <>
  <button onClick={() => onFrame({timeSec:0,detections:[],homography:[[1,0,0],[0,1,0],[0,0,1]]})}>Capture frame</button>
  <button onClick={() => onRecordingChange(true)}>Start capture</button>
  <button onClick={() => onRecordingChange(false)}>Stop capture</button>
</> }));
vi.mock('./VideoFileProcessor', () => ({VideoFileProcessor: () => <p>Video source</p>}));
vi.mock('./useSavedRuns', () => ({useSavedRuns: () => ({sessions:[]})}));
vi.mock('../offline/RecorderOfflineStatus', () => ({RecorderOfflineStatus: () => null}));
vi.mock('./RunResults', () => ({RunResults: () => null}));
vi.mock('./robotPaths', () => ({buildRobotPaths: () => [{pathId:1,alliance:'red',thumb:null,points:[{timeSec:0,zoneKey:null},{timeSec:1,zoneKey:null},{timeSec:2,zoneKey:null}]}]}));
vi.mock('./trackProduction', () => ({assemblePointsByTeam: () => ({frc1:[]})}));
vi.mock('./TrackIdentityList', () => ({TrackIdentityList: ({onAssign}: {onAssign:(id:number,team:string)=>void}) => <button onClick={() => onAssign(1,'frc1')}>Assign robot</button>}));
vi.mock('./offlineStore', () => ({openDb:vi.fn(async () => ({close:vi.fn()})),saveSession:vi.fn(async () => {})}));
afterEach(() => {cleanup(); localStorage.clear(); window.location.hash=''; vi.restoreAllMocks();});
async function capture() {
  window.location.hash='#/scouting/record?event=2026test&match=2026test_qm1';
  render(<OnDeviceRun />);
  fireEvent.click(await screen.findByText('Continue to calibration'));
  fireEvent.click(screen.getByText('Calibrate'));
}
it('preserves frames on active-source clicks, disables switching during capture, and confirms discards', async () => {
  await capture();
  fireEvent.click(screen.getByText('Capture frame'));
  fireEvent.click(screen.getByRole('tab',{name:'Record'}));
  expect(screen.getByText('Identify robots (1 frames)')).toBeEnabled();
  fireEvent.click(screen.getByText('Start capture'));
  expect(screen.getByRole('tab',{name:'Upload video'})).toBeDisabled();
  fireEvent.click(screen.getByText('Stop capture'));
  const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
  fireEvent.click(screen.getByRole('tab',{name:'Upload video'}));
  expect(confirm).toHaveBeenCalledWith('Discard these captured frames and switch source?');
  expect(screen.getByText('Identify robots (1 frames)')).toBeEnabled();
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole('tab',{name:'Upload video'}));
  expect(screen.getByText('Video source')).toBeInTheDocument();
  expect(screen.getByText('Identify robots')).toBeDisabled();
});
it('returns Record another match to match setup', async () => {
  await capture();
  fireEvent.click(screen.getByText('Capture frame'));
  fireEvent.click(screen.getByText('Identify robots (1 frames)'));
  fireEvent.click(screen.getByText('Assign robot'));
  fireEvent.click(screen.getByText('Save & view results (1)'));
  fireEvent.click(await screen.findByText('Record another match'));
  expect(screen.getByText(/Pick the match you're filming/)).toBeInTheDocument();
  expect(screen.queryByRole('tab',{name:'Upload video'})).not.toBeInTheDocument();
});
