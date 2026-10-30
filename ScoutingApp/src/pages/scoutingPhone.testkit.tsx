// Setup shared by the scouting.phone.*.test.tsx files (mocks: scoutingPage.mocks.testkit.ts).
import { cleanup, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, vi } from 'vitest';
import { ScoutingPage } from './ScoutingPage';
import { getEventSchedule } from '../api';
import { signInTestWorkspace } from '../test/workspace';
import { room } from './scoutingPage.mocks.testkit';

export { room };

export const match = (number: number) => ({
  match_key: `2026week0_qm${number}`, display_name: `Qual ${number}`, comp_level: 'qm', set_number: 1, match_number: number,
  is_completed: false, scheduled_time: null,
  red: [{team_key:'frc118',team_number:118,station:'r1'}, {team_key:'frc254',team_number:254,station:'r2'}], blue: [],
});

export const mount = (path = '/scouting?event=2026week0') => render(<MemoryRouter initialEntries={[path]}><ScoutingPage /></MemoryRouter>);

export function setUpPhoneScouting() {
  beforeEach(() => {
    localStorage.clear(); sessionStorage.clear();
    room.retry.mockClear();
    signInTestWorkspace('member');
    vi.stubGlobal('matchMedia', () => ({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
    Element.prototype.scrollIntoView = vi.fn();
    room.schedule = [match(1),match(2)];
    room.snapshot = {room_key:'room-test', me:{member_id:1}, assignments:[{match_key:'2026week0_qm2',team_key:'frc254',assigned_member_id:1,covered_by_me:false}]};
    vi.mocked(getEventSchedule).mockResolvedValue({matches:room.schedule,event_name:'Week 0'} as never);
  });
  afterEach(() => {cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});
}
