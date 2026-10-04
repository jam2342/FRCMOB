import { describe, expect, it } from 'vitest';
import { shouldPrefetchRoutes } from './routePrefetch';

describe('route prefetch', () => {
  it('respects data saver and very slow connections', () => {
    expect(shouldPrefetchRoutes(undefined)).toBe(true);
    expect(shouldPrefetchRoutes({ effectiveType: '4g' })).toBe(true);
    expect(shouldPrefetchRoutes({ effectiveType: '3g' })).toBe(true);
    expect(shouldPrefetchRoutes({ saveData: true, effectiveType: '4g' })).toBe(false);
    expect(shouldPrefetchRoutes({ effectiveType: '2g' })).toBe(false);
    expect(shouldPrefetchRoutes({ effectiveType: 'slow-2g' })).toBe(false);
  });
});
