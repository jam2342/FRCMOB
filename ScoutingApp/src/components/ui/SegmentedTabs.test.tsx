import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { SegmentedTabs } from './SegmentedTabs';

function Harness() {
  const [value, setValue] = useState<'overview' | 'teams'>('overview');

  return (
    <SegmentedTabs
      className="center-tabs"
      itemClassName="center-tab-btn"
      ariaLabel="Harness tabs"
      value={value}
      onChange={setValue}
      items={[
        { value: 'overview', label: 'Overview' },
        { value: 'teams', label: 'Teams' },
      ]}
    />
  );
}

describe('SegmentedTabs', () => {
  it('renders tab semantics and keeps shared control classes', () => {
    render(<Harness />);

    const tablist = screen.getByRole('tablist', { name: 'Harness tabs' });
    const overviewTab = screen.getByRole('tab', { name: 'Overview' });
    const teamsTab = screen.getByRole('tab', { name: 'Teams' });

    expect(tablist).toBeInTheDocument();
    expect(overviewTab).toHaveAttribute('aria-selected', 'true');
    expect(overviewTab).toHaveClass('segmented-tabs__item', 'center-tab-btn', 'active');
    expect(teamsTab).toHaveAttribute('aria-selected', 'false');

    fireEvent.click(teamsTab);

    expect(teamsTab).toHaveAttribute('aria-selected', 'true');
    expect(teamsTab).toHaveClass('segmented-tabs__item', 'center-tab-btn', 'active');
    expect(overviewTab).toHaveAttribute('aria-selected', 'false');
  });

  it('switches tabs with arrow keys', () => {
    render(<Harness />);

    const overviewTab = screen.getByRole('tab', { name: 'Overview' });
    const teamsTab = screen.getByRole('tab', { name: 'Teams' });

    fireEvent.keyDown(overviewTab, { key: 'ArrowRight' });

    expect(teamsTab).toHaveAttribute('aria-selected', 'true');
    expect(overviewTab).toHaveAttribute('aria-selected', 'false');
  });
});

describe('SegmentedTabs with no matching value', () => {
  it('keeps the first enabled tab reachable with Tab', () => {
    render(
      <SegmentedTabs
        ariaLabel="Phases"
        value="notes"
        onChange={() => undefined}
        items={[{ value: 'auto', label: 'Auto', disabled: true }, { value: 'teleop', label: 'Teleop' }, { value: 'endgame', label: 'Endgame' }]}
      />,
    );
    expect(screen.getByRole('tab', { name: 'Teleop' })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('tab', { name: 'Endgame' })).toHaveAttribute('tabindex', '-1');
  });
});

describe('SegmentedTabs swipe', () => {
  function SwipeHarness() {
    const [value, setValue] = useState<'a' | 'b' | 'c'>('a');
    return (
      <main className="ps-content">
        <SegmentedTabs
          swipe
          ink
          ariaLabel="Swipe tabs"
          value={value}
          onChange={setValue}
          items={[
            { value: 'a', label: 'A' },
            { value: 'b', label: 'B', disabled: true },
            { value: 'c', label: 'C' },
          ]}
        />
        <p>Panel {value}</p>
        <input aria-label="Field" />
      </main>
    );
  }

  function swipe(target: Element, fromX: number, toX: number) {
    fireEvent.touchStart(target, { touches: [{ clientX: fromX, clientY: 300 }] });
    fireEvent.touchEnd(target, { changedTouches: [{ clientX: toX, clientY: 305 }] });
  }

  it('moves to the next usable tab on a left swipe and back on a right swipe, on touch screens', () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({ matches: query.includes('coarse'), addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
    render(<SwipeHarness />);
    const panel = screen.getByText('Panel a');
    swipe(panel, 300, 120);
    expect(screen.getByRole('tab', { name: 'C' })).toHaveAttribute('aria-selected', 'true');
    swipe(screen.getByText('Panel c'), 100, 300);
    expect(screen.getByRole('tab', { name: 'A' })).toHaveAttribute('aria-selected', 'true');
    // Short drags, and drags that start in a field, are not tab swipes.
    swipe(screen.getByText('Panel a'), 300, 260);
    swipe(screen.getByLabelText('Field'), 300, 100);
    expect(screen.getByRole('tab', { name: 'A' })).toHaveAttribute('aria-selected', 'true');
    window.matchMedia = original;
  });
});
