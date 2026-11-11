import { FieldRadioGroup } from '../../components/ui/primitives';
import type { PitFieldDef } from '../../config/gameFields';
import { hapticTap } from '../../utils/haptics';
import { PIT_NUMBER_STEPS } from './pitStepConfig';
import styles from './PitForm.module.css';

type OnChange = (key: string, value: unknown) => void;

const YES_NO = [
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
];

export function PitToggleField({ field, teamKey, value, onChange }: { field: PitFieldDef; teamKey: string; value: unknown; onChange: OnChange }) {
  return (
    <FieldRadioGroup
      className="pit-field pit-field--yesno"
      legend={field.label}
      name={`pit-${teamKey}-${field.key}`}
      value={value === true ? 'yes' : value === false ? 'no' : ''}
      options={YES_NO}
      segmented
      allowClear
      onValueChange={(answer) => {
        hapticTap();
        onChange(field.key, answer ? answer === 'yes' : undefined);
      }}
    />
  );
}

// A short list of answers is one tap each, not a dropdown. Tapping the chosen one again unanswers it.
function ChoiceField({ field, value, onChange }: { field: PitFieldDef; value: unknown; onChange: OnChange }) {
  const chosen = typeof value === 'string' ? value : '';
  return (
    <div className="pit-field" role="radiogroup" aria-label={field.label}>
      <span className="pit-field-label" aria-hidden="true">{field.label}</span>
      <div className="pit-multiselect">
        {(field.options ?? []).map((option) => {
          const active = chosen === option;
          return (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={active}
              className={`${styles.choice} ${active ? styles.choiceOn : ''}`}
              onClick={() => {
                hapticTap();
                onChange(field.key, active ? undefined : option);
              }}
            >
              {option}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function MultiField({ field, value, onChange }: { field: PitFieldDef; value: unknown; onChange: OnChange }) {
  const selected = Array.isArray(value) ? (value as string[]) : [];
  return (
    <div className="pit-field" role="group" aria-label={field.label}>
      <span className="pit-field-label" aria-hidden="true">{field.label}</span>
      <div className="pit-multiselect">
        {(field.options ?? []).map((option) => {
          const active = selected.includes(option);
          return (
            <button
              key={option}
              type="button"
              aria-pressed={active}
              className={`${styles.choice} ${active ? styles.choiceOn : ''}`}
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
    </div>
  );
}

// Typing still works; the buttons are for a thumb. A blank answer stays null, never 0.
function NumberField({ field, value, onChange }: { field: PitFieldDef; value: unknown; onChange: OnChange }) {
  const current = typeof value === 'number' && Number.isFinite(value) ? value : typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
  const { step, start } = PIT_NUMBER_STEPS[field.key] ?? { step: 1, start: 0 };
  const set = (next: number) => { hapticTap(); onChange(field.key, Math.max(0, Math.round(next * 100) / 100)); };
  return (
    <div className="pit-field">
      <span className="pit-field-label" id={`pit-num-${field.key}`}>{field.label}</span>
      <div className={styles.number}>
        <button
          type="button"
          className={styles.numberButton}
          aria-label={`Decrease ${field.label.toLowerCase()} by ${step}`}
          disabled={current === null || current <= 0}
          onClick={() => set((current ?? 0) - step)}
        >
          −
        </button>
        <input
          className={`center-input ${styles.numberValue}`}
          type="number"
          min={0}
          inputMode="decimal"
          aria-labelledby={`pit-num-${field.key}`}
          placeholder="—"
          value={current ?? (typeof value === 'string' ? value : '')}
          onChange={(event) => {
            const parsed = event.target.value === '' ? null : Number(event.target.value);
            onChange(field.key, parsed != null && Number.isFinite(parsed) ? parsed : null);
          }}
        />
        <button
          type="button"
          className={styles.numberButton}
          aria-label={`Increase ${field.label.toLowerCase()} by ${step}`}
          onClick={() => set(current === null ? start : current + step)}
        >
          +
        </button>
        {field.unit ? <span className="pit-unit">{field.unit}</span> : null}
      </div>
    </div>
  );
}

export function PitValueField({ field, value, onChange }: { field: PitFieldDef; value: unknown; onChange: OnChange }) {
  switch (field.type) {
    case 'select':
      return <ChoiceField field={field} value={value} onChange={onChange} />;
    case 'multiselect':
      return <MultiField field={field} value={value} onChange={onChange} />;
    case 'number':
      return <NumberField field={field} value={value} onChange={onChange} />;
    case 'textarea':
      return (
        <label className="pit-field">
          <span className="pit-field-label">{field.label}</span>
          <textarea
            className="center-input pit-textarea"
            rows={3}
            placeholder={field.placeholder}
            value={typeof value === 'string' ? value : ''}
            onChange={(event) => onChange(field.key, event.target.value)}
          />
        </label>
      );
    default:
      return (
        <label className="pit-field">
          <span className="pit-field-label">{field.label}</span>
          <input
            className="center-input"
            type="text"
            placeholder={field.placeholder}
            value={typeof value === 'string' ? value : ''}
            onChange={(event) => onChange(field.key, event.target.value)}
          />
        </label>
      );
  }
}
