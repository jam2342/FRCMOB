import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

vi.mock('../../components/PageViewBar', () => ({ PageViewBar: () => null }));
vi.mock('../../components/EventPicker', () => ({ EventPicker: () => null }));
vi.mock('../workspace/WorkspaceGate', () => ({ WorkspaceGate: ({ children }: { children: React.ReactNode }) => children }));
vi.mock('../workspace/useWorkspace', () => ({ useWorkspace: () => ({ workspace: { id: 1 }, me: { role: 'member', display_name: 'Priya' } }) }));
vi.mock('../../utils/offlineQueue', async (original) => ({
  ...(await original<typeof import('../../utils/offlineQueue')>()),
  listQueuedMutations: vi.fn(async () => []),
}));
vi.mock('./pitPhotoFiles', async (original) => ({
  ...(await original<typeof import('./pitPhotoFiles')>()),
  compressPhoto: vi.fn(async () => new Blob(['jpeg'], { type: 'image/jpeg' })),
  uploadPhotoBlob: vi.fn(),
}));
vi.mock('../../api', async (original) => ({
  ...(await original<typeof import('../../api')>()),
  getEventTeamsIntel: vi.fn(async () => ({ teams: [{ team_key: 'frc100', team_number: 100, nickname: 'Alpha' }] })),
  listPitEntries: vi.fn(async () => ({ entries: [] })),
  upsertPitEntry: vi.fn(),
}));
import { PitScoutingPage } from '../../pages/PitScoutingPage';
import { upsertPitEntry } from '../../api';
import { uploadPhotoBlob } from './pitPhotoFiles';
import { PIT_FORM_SECTIONS } from '../../config/gameFields';
import { PIT_STEPS, sectionsForStep } from './pitStepConfig';
import { createMemoryPitPhotoStore, listQueuedPitPhotos, setPitPhotoStore } from '../offline/pitPhotoQueue';

const setPhone = (phone: boolean) => {
  window.matchMedia = ((query: string) => ({ matches: phone && query.includes('max-width'), media: query, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false })) as never;
};

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  setPitPhotoStore(createMemoryPitPhotoStore());
  URL.createObjectURL = vi.fn(() => 'blob:thumb');
  URL.revokeObjectURL = vi.fn();
  vi.mocked(upsertPitEntry).mockImplementation(async (body) => ({ ok: true, entry: { id: 1, event_key: body.event_key, team_key: body.team_key, scout_profile: null, payload: body.payload, photos: [], created_at: null, updated_at: null } }));
});
afterEach(() => { cleanup(); localStorage.clear(); vi.clearAllMocks(); Object.defineProperty(navigator, 'onLine', { value: true, configurable: true }); });

const renderPage = async () => {
  render(<MemoryRouter initialEntries={['/?event=2026old']}><PitScoutingPage /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: /Team 100/ }));
};

it('puts every question in exactly one step', () => {
  const placed = PIT_STEPS.flatMap((_, index) => sectionsForStep(index).flatMap((section) => section.fields.map((field) => field.key)));
  const all = PIT_FORM_SECTIONS.flatMap((section) => section.fields.map((field) => field.key));
  expect([...placed].sort()).toEqual([...all].sort());
});

it('on a phone shows one step at a time and walks them with Next step and Back', async () => {
  setPhone(true);
  await renderPage();
  expect(screen.getByRole('button', { name: '1 of 3 · Robot' })).toHaveAttribute('aria-current', 'step');
  expect(screen.getByRole('radiogroup', { name: 'Drivetrain' })).toBeInTheDocument();
  expect(screen.queryByRole('radiogroup', { name: 'Claimed climb' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next step' }));
  expect(screen.getByRole('radiogroup', { name: 'Claimed climb' })).toBeInTheDocument();
  expect(screen.queryByRole('radiogroup', { name: 'Drivetrain' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Go to step 3, Notes' }));
  expect(screen.getByRole('button', { name: 'Save & next' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Next step' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Back to the previous step' }));
  expect(screen.getByRole('button', { name: '2 of 3 · Capabilities' })).toBeInTheDocument();
});

const answerEverything = (next: () => void) => {
  fireEvent.click(within(screen.getByRole('radiogroup', { name: 'Drivetrain' })).getByRole('radio', { name: 'Swerve' }));
  fireEvent.click(screen.getByRole('button', { name: 'Increase weight by 5' }));
  next();
  fireEvent.click(screen.getByRole('button', { name: 'Mobility' }));
  fireEvent.click(within(screen.getByRole('radiogroup', { name: 'Claimed climb' })).getByRole('radio', { name: 'Level 2' }));
  fireEvent.click(within(screen.getByRole('group', { name: 'Claims they can cross bump' })).getByRole('radio', { name: 'No' }));
};

it('saves one payload whichever step Save is pressed on, with the same value shapes', async () => {
  setPhone(true);
  await renderPage();
  answerEverything(() => fireEvent.click(screen.getByRole('button', { name: 'Next step' })));
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await act(async () => {});
  const midway = vi.mocked(upsertPitEntry).mock.calls[0][0].payload;
  expect(midway).toEqual({ drivetrain: 'Swerve', weight_lbs: 120, auto_capabilities: ['Mobility'], claimed_climb: 'Level 2', can_cross_bump: false });

  // The same answers on the desktop layout save the identical payload.
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
  setPhone(false);
  await renderPage();
  answerEverything(() => {});
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await act(async () => {});
  expect(vi.mocked(upsertPitEntry).mock.calls[0][0].payload).toEqual(midway);
});

it('keeps a blank number null and lets a choice be unanswered with a second tap', async () => {
  setPhone(false);
  await renderPage();
  const weight = screen.getByRole('spinbutton', { name: 'Weight' });
  fireEvent.change(weight, { target: { value: '90' } });
  fireEvent.change(weight, { target: { value: '' } });
  const swerve = within(screen.getByRole('radiogroup', { name: 'Drivetrain' })).getByRole('radio', { name: 'Swerve' });
  fireEvent.click(swerve);
  fireEvent.click(swerve);
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await act(async () => {});
  const payload = vi.mocked(upsertPitEntry).mock.calls[0][0].payload;
  expect(payload.weight_lbs).toBeNull();
  expect(payload).not.toHaveProperty('drivetrain');
});

it('offline, a photo waits on this phone, shows as waiting, and can be deleted', async () => {
  setPhone(true);
  Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
  await renderPage();
  const file = new File(['raw'], 'shot.jpg', { type: 'image/jpeg' });
  await act(async () => { fireEvent.change(screen.getByLabelText('Take robot photo'), { target: { files: [file] } }); });
  expect(uploadPhotoBlob).not.toHaveBeenCalled();
  expect(await listQueuedPitPhotos()).toHaveLength(1);
  expect(await screen.findByText('Waiting to upload')).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('1 photo waiting to upload');
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete photo waiting to upload' })); });
  expect(await listQueuedPitPhotos()).toEqual([]);
  expect(screen.queryByText('Waiting to upload')).not.toBeInTheDocument();
});

it('queues the photo when an upload fails on the network, but reports a server refusal', async () => {
  setPhone(true);
  await renderPage();
  const file = new File(['raw'], 'shot.jpg', { type: 'image/jpeg' });
  vi.mocked(uploadPhotoBlob).mockRejectedValueOnce(new TypeError('Failed to fetch'));
  await act(async () => { fireEvent.change(screen.getByLabelText('Take robot photo'), { target: { files: [file] } }); });
  expect(await listQueuedPitPhotos()).toHaveLength(1);
  vi.mocked(uploadPhotoBlob).mockRejectedValueOnce(new Error('Photo is too large'));
  await act(async () => { fireEvent.change(screen.getByLabelText('Choose robot photo from library'), { target: { files: [file] } }); });
  expect(await listQueuedPitPhotos()).toHaveLength(1);
  expect(screen.getByRole('alert')).toHaveTextContent('Photo is too large');
});

it('says plainly when the phone cannot hold the photo, instead of pretending', async () => {
  setPhone(true);
  setPitPhotoStore({ getAll: () => Promise.reject(new Error('blocked')), put: () => Promise.reject(new Error('blocked')), remove: () => Promise.reject(new Error('blocked')) });
  Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
  await renderPage();
  const file = new File(['raw'], 'shot.jpg', { type: 'image/jpeg' });
  await act(async () => { fireEvent.change(screen.getByLabelText('Take robot photo'), { target: { files: [file] } }); });
  expect(screen.getByRole('alert')).toHaveTextContent("can't keep photos while offline");
});
