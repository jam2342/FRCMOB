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
