import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { FieldRadioGroup } from '../../components/ui/primitives';
import { PIT_FORM_SECTIONS } from '../../config/gameFields';
import type { PitFieldDef } from '../../config/gameFields';
import { hapticTap } from '../../utils/haptics';
import { teamNumber } from './pitTeams';
import './pit.css';

export type PitFormProps = {
  teamKey: string;
  nickname: string | null;
  values: Record<string, unknown>;
  // `undefined` removes the answer, so an unanswered question stays missing instead of saving as No.
  onChange: (key: string, value: unknown) => void;
  // The photo strip, rendered above the questions.
  photos: ReactNode;
};

const YES_NO = [
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
];

export function PitForm({ teamKey, nickname, values, onChange, photos }: PitFormProps) {
  const cardRef = useRef<HTMLDivElement | null>(null);
  const previousTeam = useRef(teamKey);

  // Moving to another team (Save & next) should land on the top of its form, not mid-page.
  useEffect(() => {
    if (previousTeam.current === teamKey) return;
    previousTeam.current = teamKey;
    cardRef.current?.scrollIntoView?.({ block: 'start' });
  }, [teamKey]);

  function renderField(field: PitFieldDef) {
    const value = values[field.key];
    switch (field.type) {
      case 'select':
        return (
          <select
            className="center-input"
            value={typeof value === 'string' ? value : ''}
            onChange={(event) => onChange(field.key, event.target.value)}
          >
            <option value="">—</option>
            {(field.options ?? []).map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        );
      case 'multiselect': {
        const selected = Array.isArray(value) ? (value as string[]) : [];
        return (
          <div className="pit-multiselect">
            {(field.options ?? []).map((option) => {
              const active = selected.includes(option);
              return (
                <button
                  key={option}
                  type="button"
                  aria-pressed={active}
                  className={`center-chip clickable ${active ? 'pit-chip-active' : ''}`}
                  onClick={() => {
                    hapticTap();
                    onChange(field.key, active ? selected.filter((item) => item !== option) : [...selected, option]);
                  }}
                >
                  {option}
                </button>
              );
            })}
          </div>
        );
      }
      case 'number':
        return (
          <div className="pit-number-wrap">
            <input
              className="center-input"
              type="number"
              min={0}
              inputMode="decimal"
              value={typeof value === 'number' ? value : typeof value === 'string' ? value : ''}
              onChange={(event) => {
                const parsed = event.target.value === '' ? null : Number(event.target.value);
                onChange(field.key, parsed != null && Number.isFinite(parsed) ? parsed : null);
              }}
            />
            {field.unit ? <span className="pit-unit">{field.unit}</span> : null}
          </div>
        );
      case 'textarea':
        return (
          <textarea
            className="center-input pit-textarea"
            rows={3}
            placeholder={field.placeholder}
            value={typeof value === 'string' ? value : ''}
            onChange={(event) => onChange(field.key, event.target.value)}
          />
        );
      default:
        return (
          <input
            className="center-input"
            type="text"
            placeholder={field.placeholder}
            value={typeof value === 'string' ? value : ''}
            onChange={(event) => onChange(field.key, event.target.value)}
          />
        );
    }
  }

  return (
    <div ref={cardRef}>
      <SurfaceCard
        tourId="pit-form"
        title={`Team ${teamNumber(teamKey)}`}
        subtitle={nickname ?? 'Pit scouting entry'}
        mobileCollapsible={false}
      >
        {photos}
        {PIT_FORM_SECTIONS.map((section) => (
          <fieldset key={section.title} className="pit-section">
            <legend>{section.title}</legend>
            <div className="pit-fields">
              {section.fields.map((field) =>
                field.type === 'toggle' ? (
                  <FieldRadioGroup
                    key={field.key}
                    className="pit-field pit-field--yesno"
                    legend={field.label}
                    name={`pit-${teamKey}-${field.key}`}
                    value={values[field.key] === true ? 'yes' : values[field.key] === false ? 'no' : ''}
                    options={YES_NO}
                    segmented
                    allowClear
                    onValueChange={(answer) => {
                      hapticTap();
                      onChange(field.key, answer ? answer === 'yes' : undefined);
                    }}
                  />
                ) : (
                  <label key={field.key} className="pit-field">
                    <span className="pit-field-label">{field.label}</span>
                    {renderField(field)}
                  </label>
                ),
              )}
            </div>
          </fieldset>
        ))}
      </SurfaceCard>
    </div>
  );
}
