import { describe, expect, it } from 'vitest';
import { isEventKeyShape } from './eventKey';
import { readStoredCenterContext } from '../layout/centerContext';
import { homeStartEvent } from '../pages/homeFeed';

describe('isEventKeyShape', () => {
  it('takes real event keys and nothing a mistyped search box produces', () => {
    for (const key of ['2026arc', '2026nmrc2', '2026CACC', '2025txhou']) expect(isEventKeyShape(key)).toBe(true);
    for (const typed of ['99999999', '5417', '1678asdf', '2026', '2026_qm1', 'arc', '']) expect(isEventKeyShape(typed)).toBe(false);
  });

  it('a junk event left in storage is not carried to the next page', () => {
    localStorage.setItem('scouting_center_event_key', '99999999');
    expect(readStoredCenterContext().eventKey).toBe('');
    expect(homeStartEvent('99999999', ['2026arc'])).toBe('2026arc');
    localStorage.removeItem('scouting_center_event_key');
  });
});
