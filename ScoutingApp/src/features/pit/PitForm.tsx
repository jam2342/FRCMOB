import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { PIT_FORM_SECTIONS } from '../../config/gameFields';
import { PitToggleField, PitValueField } from './PitFields';
import { PitSteps } from './PitSteps';
import { sectionsForStep } from './pitStepConfig';
import type { PitStepper } from './pitStepConfig';
import { teamNumber } from './pitTeams';
import styles from './PitForm.module.css';
import './pit.css';

export type PitFormProps = {
  teamKey: string;
  nickname: string | null;
  values: Record<string, unknown>;
  // `undefined` removes the answer, so an unanswered question stays missing instead of saving as No.
  onChange: (key: string, value: unknown) => void;
  // The photo strip, rendered above the questions.
  photos: ReactNode;
  // Phones show one step at a time; leave it out to show everything.
  stepper?: PitStepper;
};

export function PitForm({ teamKey, nickname, values, onChange, photos, stepper }: PitFormProps) {
  const cardRef = useRef<HTMLDivElement | null>(null);
  const previousTeam = useRef(teamKey);
  const previousStep = useRef(stepper?.index ?? 0);
  const stepped = Boolean(stepper?.stepped);
  const stepIndex = stepper?.index ?? 0;

  // Moving to another team (Save & next) should land on the top of its form, not mid-page.
  useEffect(() => {
    if (previousTeam.current === teamKey) return;
    previousTeam.current = teamKey;
    cardRef.current?.scrollIntoView?.({ block: 'start' });
  }, [teamKey]);

  // A new step starts at the top too, or Next step would leave you at the bottom of a shorter page.
  useEffect(() => {
    if (previousStep.current === stepIndex) return;
    previousStep.current = stepIndex;
    cardRef.current?.scrollIntoView?.({ block: 'start' });
  }, [stepIndex]);

  const sections = stepped ? sectionsForStep(stepIndex) : PIT_FORM_SECTIONS;

  return (
    <div ref={cardRef} className={styles.card}>
      <SurfaceCard
        tourId="pit-form"
        title={`Team ${teamNumber(teamKey)}`}
        subtitle={nickname ?? 'Pit scouting entry'}
        mobileCollapsible={false}
        // The team strip pinned above the form already names the team on a phone.
        hideTitleOnPhone
      >
        {stepped && stepper ? <PitSteps index={stepIndex} onSelect={stepper.setIndex} /> : null}
        {!stepped || stepIndex === 0 ? photos : null}
        {sections.map((section) => (
          <fieldset key={section.title} className={`pit-section ${stepped ? styles.flat : ''}`}>
            <legend>{section.title}</legend>
            <div className="pit-fields">
              {section.fields.map((field) =>
                field.type === 'toggle' ? (
                  <PitToggleField key={field.key} field={field} teamKey={teamKey} value={values[field.key]} onChange={onChange} />
                ) : (
                  <PitValueField key={field.key} field={field} value={values[field.key]} onChange={onChange} />
                ),
              )}
            </div>
          </fieldset>
        ))}
      </SurfaceCard>
    </div>
  );
}
