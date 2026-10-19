import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MobileViewActions } from './MobileViewActions';

describe('MobileViewActions', () => {
  it('shows plain actions and runs them', () => {
    const change = vi.fn();
    const back = vi.fn();
    render(
      <MobileViewActions
        label="Event views"
        actions={[{ label: 'Back to Archimedes', onClick: back, back: true }, { label: 'Change event', onClick: change }]}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Change event' }));
    fireEvent.click(screen.getByRole('button', { name: /Back to Archimedes/ }));
    expect(change).toHaveBeenCalledOnce();
    expect(back).toHaveBeenCalledOnce();
  });

  it('renders nothing with no actions', () => {
    const { container } = render(<MobileViewActions label="x" actions={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('inline: a short label still names what it changes', () => {
    const change = vi.fn();
    render(<MobileViewActions inline label="Event views" actions={[{ label: 'Change', ariaLabel: 'Change event', onClick: change }]} />);
    const button = screen.getByRole('button', { name: 'Change event' });
    expect(button).toHaveTextContent('Change');
    fireEvent.click(button);
    expect(change).toHaveBeenCalledOnce();
  });
});
