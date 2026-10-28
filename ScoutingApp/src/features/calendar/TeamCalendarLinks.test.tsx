import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const runtime = vi.hoisted(() => ({ native: false }));
vi.mock('../../platform/runtime', async (original) => ({ ...(await original<typeof import('../../platform/runtime')>()), isNativeApp: () => runtime.native }));

import { teamCalendarUrls } from './calendarUrls';
import { TeamCalendarLinks } from './TeamCalendarLinks';

afterEach(() => { runtime.native = false; });

describe('team calendar', () => {
  it('builds a webcal subscription and an https download for the same feed', () => {
    const { subscribe, download } = teamCalendarUrls('2026mndu', 'frc3100');
    expect(download).toMatch(/^https?:\/\/.+\/matches\/event\/2026mndu\/team\/frc3100\/calendar\.ics$/);
    expect(subscribe).toBe(download.replace(/^https?:/, 'webcal:'));
  });

  it('offers Subscribe and Download on the web', () => {
    render(<TeamCalendarLinks eventKey="2026mndu" teamNumber={3100} />);
    expect(screen.getByRole('link', { name: 'Subscribe' }).getAttribute('href')).toMatch(/^webcal:/);
    expect(screen.getByRole('link', { name: 'Download' })).toHaveAttribute('download', '3100-2026mndu.ics');
  });

  it('in the app, one https link the system browser can add', () => {
    runtime.native = true;
    render(<TeamCalendarLinks eventKey="2026mndu" teamNumber={3100} />);
    expect(screen.getByRole('link', { name: 'Add to calendar' }).getAttribute('href')).toMatch(/^https?:.*calendar\.ics$/);
    expect(screen.queryByRole('link', { name: 'Subscribe' })).not.toBeInTheDocument();
  });
});
