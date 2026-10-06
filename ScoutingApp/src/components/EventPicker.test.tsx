import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EventSearchItem } from '../api';

const catalog: EventSearchItem[] = [
  { event_key: '2026arc', name: 'Archimedes Division', year: 2026, city: 'Houston', state_prov: 'TX', country: 'USA' } as EventSearchItem,
  { event_key: '2026mndu', name: 'Lake Superior Regional', year: 2026, city: 'Duluth', state_prov: 'MN', country: 'USA' } as EventSearchItem,
];

vi.mock('../features/events/eventCatalog', () => ({
  loadSeasonEventCatalog: vi.fn(async () => catalog),
}));
vi.mock('../utils/eventSearch', () => ({
  smartSearchEvents: vi.fn(async (query: string) => ({
    events: catalog.filter((event) => `${event.event_key} ${event.name}`.toLowerCase().includes(query.toLowerCase())),
  })),
}));

import { EventPicker } from './EventPicker';

function Harness({ onSelect, onSubmit }: { onSelect: (key: string) => void; onSubmit: () => void }) {
  const [value, setValue] = useState('2026arc');
  const [input, setInput] = useState('2026arc');
  return (
    <EventPicker
      value={value}
      inputValue={input}
      onInputChange={setInput}
      onSelect={(key) => {
        setValue(key);
        onSelect(key);
      }}
      onSubmit={onSubmit}
    />
  );
}

describe('EventPicker', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows the chosen event by name, with no load button', async () => {
    render(<Harness onSelect={vi.fn()} onSubmit={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByLabelText('Search events')).toHaveValue('Archimedes Division');
    expect(screen.queryByRole('button', { name: /load event/i })).toBeNull();
  });

  it('Enter right after typing picks the result for that text, not a raw key', async () => {
    const onSelect = vi.fn();
    const onSubmit = vi.fn();
    render(<Harness onSelect={onSelect} onSubmit={onSubmit} />);
    const input = screen.getByLabelText('Search events');
    fireEvent.change(input, { target: { value: 'superior' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onSelect).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(250);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(onSelect).toHaveBeenCalledWith('2026mndu');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('in add mode, picking adds the event and empties the box', async () => {
    const added: string[] = [];
    function AddHarness() {
      const [input, setInput] = useState('');
      return (
        <EventPicker value="" inputValue={input} onInputChange={setInput} onSelect={(key) => added.push(key)} actionLabel="Add event" clearOnSelect />
      );
    }
    render(<AddHarness />);
    const input = screen.getByLabelText('Search events');
    fireEvent.change(input, { target: { value: 'superior' } });
    expect(screen.getByRole('button', { name: 'Add event' })).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Enter' });
    await act(async () => {
      vi.advanceTimersByTime(250);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(added).toEqual(['2026mndu']);
    expect(input).toHaveValue('');
  });
});
