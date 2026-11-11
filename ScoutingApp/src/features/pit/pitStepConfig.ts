import { useEffect, useState } from 'react';
import { PIT_FORM_SECTIONS } from '../../config/gameFields';
import type { PitFormSection } from '../../config/gameFields';
import { useMobileLayout } from '../../hooks/useMobileLayout';

// Phones show the form a step at a time; the sections themselves stay in config/gameFields.ts.
export const PIT_STEPS = [
  { id: 'robot', label: 'Robot', sections: ['Robot'] },
  { id: 'capabilities', label: 'Capabilities', sections: ['Claimed Capabilities'] },
  { id: 'notes', label: 'Notes', sections: ['Strategy'] },
] as const;

export function sectionsForStep(index: number, sections: PitFormSection[] = PIT_FORM_SECTIONS): PitFormSection[] {
  const last = PIT_STEPS.length - 1;
  const known = new Set<string>(PIT_STEPS.flatMap((step) => step.sections));
  return sections.filter((section) =>
    PIT_STEPS[index].sections.some((title) => title === section.title) || (index === last && !known.has(section.title)),
  );
}

// Where the number buttons start from when the question is still blank, and how far each tap moves.
export const PIT_NUMBER_STEPS: Record<string, { step: number; start: number }> = {
  weight_lbs: { step: 5, start: 120 },
  claimed_cycle_time_sec: { step: 1, start: 10 },
};

export type PitStepper = {
  // Phones step through the form; desktop shows every step on one page.
  stepped: boolean;
  index: number;
  count: number;
  setIndex: (next: number) => void;
};

export function usePitStepper(teamKey: string): PitStepper {
  const stepped = useMobileLayout();
  const [index, setIndexState] = useState(0);
  // Each team starts at the first step.
  useEffect(() => { setIndexState(0); }, [teamKey]);
  const setIndex = (next: number) => setIndexState(Math.min(PIT_STEPS.length - 1, Math.max(0, next)));
  return { stepped, index, count: PIT_STEPS.length, setIndex };
}
