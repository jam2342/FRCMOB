import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { NextAssignment } from './NextAssignment';
it('does not claim played assignments were scouted', () => {
  render(<NextAssignment eventKey="2026test" list={[{match_key:'2026test_qm1',team_key:'frc254',match_label:'Qual 1',team_number:'254',alliance:'red',start_time:null,done:true}]} />);
  expect(screen.getByText('No upcoming assignments remain.')).toBeInTheDocument();
  expect(screen.queryByText(/scouted every/)).not.toBeInTheDocument();
});
