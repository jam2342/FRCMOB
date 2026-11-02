import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CompactAllianceTeams } from './CompactAllianceTeams';

describe('CompactAllianceTeams', () => {
  it('lists every robot as its own span, B teams by key', () => {
    const { container } = render(
      <CompactAllianceTeams
        side="red"
        teams={[
          { team_key: 'frc841', team_number: 841 },
          { team_key: 'frc100B', team_number: 0 },
          { team_key: 'frc670', team_number: 670 },
        ] as never}
      />,
    );
    const side = container.querySelector('.home-match-compact-side.red');
    expect([...container.querySelectorAll('.home-match-compact-team')].map((el) => el.textContent)).toEqual(['#841', '#100B', '#670']);
    expect(side?.getAttribute('title')).toBe('#841 · #100B · #670');
  });

  it('says TBD before the lineup is out', () => {
    const { container } = render(<CompactAllianceTeams side="blue" teams={[]} />);
    expect(container.textContent).toBe('TBD');
  });
});
