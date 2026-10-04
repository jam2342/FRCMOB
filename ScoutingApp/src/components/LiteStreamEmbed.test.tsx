import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LiteStreamEmbed } from './LiteStreamEmbed';

describe('LiteStreamEmbed', () => {
  it('shows the thumbnail first and loads the player only on tap', () => {
    const { container } = render(
      <LiteStreamEmbed title="Live stream for 2026arc" src="https://www.youtube.com/embed/mO3eq5qJfJk" />,
    );
    expect(container.querySelector('iframe')).toBeNull();
    const play = screen.getByRole('button', { name: 'Play Live stream for 2026arc' });
    expect(play.getAttribute('style')).toContain('i.ytimg.com/vi/mO3eq5qJfJk/hqdefault.jpg');

    fireEvent.click(play);
    const iframe = container.querySelector('iframe');
    expect(iframe?.getAttribute('src')).toBe('https://www.youtube.com/embed/mO3eq5qJfJk?autoplay=1');
  });

  it('still offers a play button for streams without a thumbnail', () => {
    render(<LiteStreamEmbed title="Match broadcast" src="https://player.twitch.tv/?channel=firstinspires&parent=x" />);
    expect(screen.getByRole('button', { name: 'Play Match broadcast' }).getAttribute('style')).toBeNull();
  });
});
