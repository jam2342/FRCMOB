import { fireEvent, render } from '@testing-library/react';
import { useRef } from 'react';
import { expect, it } from 'vitest';
import { useFocusTrap } from './useFocusTrap';

function Overlay({ name, children }: { name: string; children?: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, true);
  return (
    <div ref={ref} data-testid={name}>
      <button type="button">{name} first</button>
      <button type="button">{name} middle</button>
      <button type="button">{name} last</button>
      {children}
    </div>
  );
}

it('lets only the topmost of two stacked overlays handle Tab', () => {
  const { getByText } = render(
    <>
      <Overlay name="outer" />
      <Overlay name="inner" />
    </>,
  );
  const middle = getByText('inner middle');
  middle.focus();
  // Inside the inner overlay, a plain Tab is not intercepted by either trap.
  const notPrevented = fireEvent.keyDown(document.activeElement!, { key: 'Tab' });
  expect(notPrevented).toBe(true);
  getByText('inner last').focus();
  fireEvent.keyDown(document.activeElement!, { key: 'Tab' });
  expect(document.activeElement).toBe(getByText('inner first'));
});
