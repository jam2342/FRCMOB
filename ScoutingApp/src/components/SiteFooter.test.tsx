import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { SiteFooter } from './SiteFooter';

describe('SiteFooter', () => {
  // These lines are required by FIRST's API terms, The Blue Alliance's
  // guidelines, YouTube's developer policies and the AGPL. Don't drop them.
  it('carries every required credit and link', () => {
    render(<MemoryRouter><SiteFooter /></MemoryRouter>);
    const href = (name: RegExp) => screen.getByRole('link', { name }).getAttribute('href');
    expect(href(/^Event Data provided by FIRST$/)).toBe('https://frc-events.firstinspires.org/services/API');
    expect(href(/^Powered by The Blue Alliance$/)).toBe('https://www.thebluealliance.com');
    expect(href(/^YouTube Terms$/)).toBe('https://www.youtube.com/t/terms');
    expect(href(/^Source code$/)).toBe('https://github.com/jam2342/FRCMOB');
    expect(href(/^Licenses$/)).toBe('/third-party-licenses.txt');
    expect(href(/^Privacy$/)).toBe('/privacy');
    expect(href(/^Terms$/)).toBe('/terms');
    expect(screen.getByText(/which is not overseeing, involved with, or responsible for this activity, product, or service/)).toBeInTheDocument();
    expect(document.body.textContent).toContain('For Inspiration and Recognition of Science and Technology (FIRST®)');
    expect(document.body.textContent).toContain('FRC®');
    expect(document.body.textContent).toContain('REBUILT™');
  });
});
