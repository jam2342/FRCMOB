import { describe, expect, it } from 'vitest';
import { teamsFromParam } from './allianceBuilder';

describe('alliance builder helpers', () => {
  it("reads Compare's hand-off, keeping three distinct team keys", () => {
    expect(teamsFromParam('frc254,FRC1678,frc254,frc971,frc118')).toEqual(['frc254', 'frc1678', 'frc971']);
    expect(teamsFromParam('frc254,bogus')).toEqual(['frc254', '', '']);
    expect(teamsFromParam(null)).toEqual(['', '', '']);
  });
});
