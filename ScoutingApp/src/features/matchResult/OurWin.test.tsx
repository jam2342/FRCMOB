import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const haptics = vi.hoisted(() => ({ hapticSuccess: vi.fn() }));
vi.mock('../../utils/haptics', () => haptics);

import { OurWin } from './OurWin';
import { ourWinningTeam } from './ourWinningTeam';

const match = { red: [{ team_key: 'frc3100' }, { team_key: 'frc2052' }], blue: [{ team_key: 'FRC254' }] };

afterEach(() => { vi.useRealTimers(); haptics.hapticSuccess.mockReset(); });

describe('our win', () => {
  it('is our team only when it was on the side that won', () => {
    expect(ourWinningTeam(match, 'red', 3100)).toBe(3100);
    expect(ourWinningTeam(match, 'blue', 254)).toBe(254);
    expect(ourWinningTeam(match, 'blue', 3100)).toBeNull();
    expect(ourWinningTeam(match, 'tie', 3100)).toBeNull();
    expect(ourWinningTeam(match, null, 3100)).toBeNull();
    expect(ourWinningTeam(match, 'red', null)).toBeNull();
    expect(ourWinningTeam(null, 'red', 3100)).toBeNull();
  });

  it('says so, and buzzes once after the score has rolled in', () => {
    vi.useFakeTimers();
    const { unmount } = render(<OurWin teamNumber={3100} />);
    expect(screen.getByRole('status')).toHaveTextContent('Team 3100 wins!');
    expect(haptics.hapticSuccess).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1200);
    expect(haptics.hapticSuccess).toHaveBeenCalledTimes(1);
    unmount();
  });
});
