import { describe, expect, it } from 'vitest';
import { DEFAULT_WEIGHTS, normalizedWeights, teamsFromParam } from './allianceBuilder';

describe('alliance builder helpers', () => {
  it('keeps the 50/30/20 split the builder always used by default', () => {
    expect(normalizedWeights(DEFAULT_WEIGHTS)).toEqual({ compatibility: 0.5, pros: 0.3, cons: 0.2 });
  });

  it('balances any weights to 100% and survives all zeros', () => {
    const shares = normalizedWeights({ compatibility: 60, pros: 25, cons: 15 });
    expect(shares.compatibility + shares.pros + shares.cons).toBeCloseTo(1);
    expect(normalizedWeights({ compatibility: 0, pros: 0, cons: 0 })).toEqual({ compatibility: 0.5, pros: 0.3, cons: 0.2 });
  });

  it("reads Compare's hand-off, keeping three distinct team keys", () => {
    expect(teamsFromParam('frc254,FRC1678,frc254,frc971,frc118')).toEqual(['frc254', 'frc1678', 'frc971']);
    expect(teamsFromParam('frc254,bogus')).toEqual(['frc254', '', '']);
    expect(teamsFromParam(null)).toEqual(['', '', '']);
  });
});
