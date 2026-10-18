import { describe, expect, it } from 'vitest';
import { embeddableUrl } from './embedHosts';

describe('embeddableUrl', () => {
  it('keeps the players the site may frame', () => {
    expect(embeddableUrl('https://www.youtube.com/embed/mO3eq5qJfJk')).toBe('https://www.youtube.com/embed/mO3eq5qJfJk');
    expect(embeddableUrl('https://player.twitch.tv/?channel=firstinspires')).toBe('https://player.twitch.tv/?channel=firstinspires');
  });

  it('drops other hosts and junk so the page shows a Watch link instead of a blocked frame', () => {
    expect(embeddableUrl('https://custom-player.example/live')).toBeNull();
    expect(embeddableUrl('https://www.youtube.com.evil.example/embed/x')).toBeNull();
    expect(embeddableUrl('javascript:alert(1)')).toBeNull();
    expect(embeddableUrl('not a url')).toBeNull();
    expect(embeddableUrl(null)).toBeNull();
  });
});
