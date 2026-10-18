import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useRef, useState } from 'react';
import { expect, it, vi } from 'vitest';
import { TeamStickyBar } from './TeamStickyBar';

function Harness({ onTab }: { onTab: (tab: string) => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [tab, setTab] = useState('overview');
  return (
    <>
      <TeamStickyBar
        watchRef={ref}
        phone
        teamNumber={254}
        nickname="The Cheesy Poofs"
        rating="88.4"
        tab={tab}
        onTab={(next) => { setTab(next); onTab(next); }}
        tabs={[{ value: 'overview', label: 'Overview' }, { value: 'events', label: 'Event history' }]}
      />
      <div ref={ref}>header tabs</div>
    </>
  );
}

it('stays hidden while the header is on screen and shows once its tabs scroll under it', async () => {
  const onTab = vi.fn();
  const where = { headerBottom: 600 };
  const original = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function rect(this: HTMLElement) {
    return { top: 0, bottom: this.textContent === 'header tabs' ? where.headerBottom : 0 } as DOMRect;
  };
  const { container } = render(<Harness onTab={onTab} />);
  const bar = container.querySelector('[aria-hidden]') as HTMLElement;
  expect(bar).toHaveAttribute('aria-hidden', 'true');
  where.headerBottom = -40;
  act(() => { window.dispatchEvent(new Event('scroll')); });
  try {
    await waitFor(() => expect(bar).toHaveAttribute('aria-hidden', 'false'));
    fireEvent.click(screen.getByRole('tab', { name: 'Event history' }));
    expect(onTab).toHaveBeenCalledWith('events');
    expect(screen.getByRole('button', { name: 'Team 254, back to the top' })).toBeInTheDocument();
  } finally {
    HTMLElement.prototype.getBoundingClientRect = original;
  }
});
