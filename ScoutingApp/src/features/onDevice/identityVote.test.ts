import { describe, expect, it } from 'vitest';

import { similarityRatio, voteTrackIdentity } from './identityVote';

const CANDIDATES = ['frc1234', 'frc5678', 'frc1812', 'frc254', 'frc118', 'frc2056'];
const r = (text: string, confidence = 1) => ({ text, confidence });

describe('identity voting', () => {
  it('similarityRatio matches difflib semantics on digit strings', () => {
    expect(similarityRatio('1234', '1234')).toBeCloseTo(1, 6);
    expect(similarityRatio('12', '1234')).toBeCloseTo(2 / 3, 6);
    expect(similarityRatio('1234', '5678')).toBe(0);
  });

  it('clean reads resolve to the correct team', () => {
    const v = voteTrackIdentity([r('1234'), r('1234'), r('1234')], CANDIDATES);
    expect(v.resolved).toBe(true);
    expect(v.teamKey).toBe('frc1234');
    expect(Object.values(v.scores).reduce((s, x) => s + x, 0)).toBeCloseTo(1, 2);
  });

  it('noisy partial reads resolve via temporal voting', () => {
    const v = voteTrackIdentity([r('12'), r('123'), r('234'), r('1234'), r('1z34')], CANDIDATES);
    expect(v.resolved).toBe(true);
    expect(v.teamKey).toBe('frc1234');
  });

  it('ambiguous reads defer to tap-ID', () => {
    const v = voteTrackIdentity([r('123'), r('12')], ['frc1234', 'frc1235']);
    expect(v.resolved).toBe(false);
    expect(v.teamKey).toBeNull();
  });

  it('empty reads are unresolved', () => {
    expect(voteTrackIdentity([], CANDIDATES).resolved).toBe(false);
    expect(voteTrackIdentity([r('')], CANDIDATES).resolved).toBe(false);
  });

  it('out-of-set reads are rejected (closed set)', () => {
    const v = voteTrackIdentity([r('9999'), r('9999'), r('9999')], ['frc1234', 'frc5678']);
    expect(v.resolved).toBe(false);
    expect(v.teamKey).toBeNull();
  });

  it('confident reads outweigh noisy wrong reads', () => {
    const reads = [r('5678', 0.1), r('5678', 0.1), r('5678', 0.1), r('1234', 1), r('1234', 1), r('1234', 1)];
    const v = voteTrackIdentity(reads, CANDIDATES);
    expect(v.resolved).toBe(true);
    expect(v.teamKey).toBe('frc1234');
  });
});
