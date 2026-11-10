import { describe, expect, it } from 'vitest';
import { pickMobileDock, placeDesktopPanel } from './panelPlacement';

const viewport = { width: 1440, height: 900 };
const box = (left: number, top: number, width: number, height: number) => ({ left, top, width, height, right: left + width, bottom: top + height });
const px = (value: unknown) => Number(String(value).replace('px', ''));

function overlaps(target: ReturnType<typeof box>, style: ReturnType<typeof placeDesktopPanel>, height: number) {
  const left = px(style.left);
  const top = px(style.top);
  const right = left + px(style.width);
  const bottom = top + height;
  return left < target.right && right > target.left && top < target.bottom && bottom > target.top;
}

describe('tour panel placement on desktop', () => {
  it('goes on the preferred side when the whole panel fits there', () => {
    const target = box(220, 300, 300, 120);
    const style = placeDesktopPanel(target, 'right', 280, viewport);
    expect(px(style.left)).toBe(target.right + 16);
    expect(overlaps(target, style, 280)).toBe(false);
  });

  it('moves to another side instead of covering a tall card it was told to sit above', () => {
    // Favorites' event card: "top" was preferred, there was no room above, and the panel sat on it.
    const target = box(264, 96, 224, 88);
    const style = placeDesktopPanel(target, 'top', 280, viewport);
    expect(overlaps(target, style, 280)).toBe(false);
  });

  it('never covers a wide form with room to its right', () => {
    // My Team's join form, 64% covered before.
    const target = box(843, 150, 294, 240);
    const style = placeDesktopPanel(target, 'bottom', 300, viewport);
    expect(overlaps(target, style, 300)).toBe(false);
  });

  it('stays on screen and as tall as its content allows', () => {
    const target = box(1200, 820, 200, 60);
    const style = placeDesktopPanel(target, 'auto', 280, viewport);
    expect(px(style.left)).toBeGreaterThanOrEqual(12);
    expect(px(style.left) + px(style.width)).toBeLessThanOrEqual(viewport.width - 12);
    expect(px(style.top)).toBeGreaterThanOrEqual(12);
    expect(style.height).toBeUndefined();
  });
});

describe('tour card dock on phones', () => {
  it('docks at the bottom when the target heading stays above the card', () => {
    expect(pickMobileDock({ top: 80, height: 300 }, 220, 844)).toBe('bottom');
  });

  it('docks at the top when the bottom card would cover the heading', () => {
    // My Team's members card, low on the screen: the old rule covered its heading.
    expect(pickMobileDock({ top: 640, height: 160 }, 217, 844)).toBe('top');
  });

  it('picks the edge that shows more of the target when neither keeps the heading clear', () => {
    expect(pickMobileDock({ top: 100, height: 600 }, 300, 450)).toBe('top');
  });
});
