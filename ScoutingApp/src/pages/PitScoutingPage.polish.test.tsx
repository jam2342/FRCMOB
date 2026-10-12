import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
vi.mock('../components/PageViewBar', () => ({ PageViewBar: () => null }));
vi.mock('../components/EventPicker', () => ({ EventPicker: ({ onSelect, value }: { onSelect: (key: string) => void; value: string }) => <>
  <button onClick={() => onSelect('2026new')}>New event</button>
  <button onClick={() => onSelect(value)}>Load event</button>
</> }));
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
vi.mock('../features/workspace/useWorkspace', () => ({ useWorkspace: () => ({ workspace: { id: 1 }, me: { role: 'leader' } }) }));
vi.mock('../features/workspace/WorkspaceGate', () => ({ WorkspaceGate: ({ children }: { children: React.ReactNode }) => children }));
import { PitScoutingPage } from './PitScoutingPage';
import { upsertPitEntry } from '../api';
import { readPitDraft } from '../features/offline/pitDrafts';
const queue = vi.hoisted(() => ({ rows: [] as Array<{id: string; url: string; body: string; queuedAt: string; failure?: {reason: string}}> }));
vi.mock('../utils/offlineQueue', () => ({ listQueuedMutations: vi.fn(async () => queue.rows) }));
vi.mock('../api', async original => ({ ...(await original<typeof import('../api')>()), getEventTeamsIntel: vi.fn(async () => ({ teams: [{team_key:'frc254', team_number:254}] })), listPitEntries: vi.fn(async () => ({entries:[]})), upsertPitEntry: vi.fn() }));

it('keeps unanswered capabilities missing and saves explicit No separately', async () => {
  Element.prototype.scrollIntoView = vi.fn();
  vi.mocked(upsertPitEntry).mockRejectedValue(new Error('save stopped for inspection'));
  render(<MemoryRouter initialEntries={['/?event=2026old']}><PitScoutingPage /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: /254/ }));
  const { PIT_FORM_SECTIONS } = await import('../config/gameFields');
  const fields = PIT_FORM_SECTIONS.flatMap((section) => section.fields).filter((field) => field.type === 'toggle');
  for (const field of fields) expect(screen.getByLabelText(field.label)).toHaveValue('');
  fireEvent.click(screen.getAllByRole('button', { name: 'Save entry' })[0]);
  await screen.findByText('save stopped for inspection');
  for (const field of fields) expect(vi.mocked(upsertPitEntry).mock.calls[0][0].payload).not.toHaveProperty(field.key);
  const field = fields[0];
  fireEvent.change(screen.getByLabelText(field.label), { target: { value: 'no' } });
  fireEvent.click(screen.getAllByRole('button', { name: 'Save entry' })[0]);
  await act(async () => {});
  expect(vi.mocked(upsertPitEntry).mock.calls.at(-1)![0].payload[field.key]).toBe(false);
  fireEvent.change(screen.getByLabelText(field.label), { target: { value: 'yes' } });
  expect(readPitDraft(1, '2026old', 'frc254')?.[field.key]).toBe(true);
  fireEvent.change(screen.getByLabelText(field.label), { target: { value: '' } });
  expect(readPitDraft(1, '2026old', 'frc254')).not.toHaveProperty(field.key);
  const photo = screen.getByLabelText('Add robot photo');
  expect(photo).toHaveAttribute('type', 'file');
  expect(photo).not.toHaveAttribute('tabindex', '-1');
});
