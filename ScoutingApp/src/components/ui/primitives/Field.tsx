import {
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';
import styles from './Field.module.css';
import { cx } from './cx';

type FieldShellProps = {
  label?: string;
  hint?: string;
  error?: string;
  required?: boolean;
  className?: string;
};

// One wiring routine for every variant: the label points at the control, the
// hint and error are announced through aria-describedby, and an error sets
// aria-invalid. Getting this once is the whole point of the primitive.
function useFieldWiring({ hint, error }: { hint?: string; error?: string }) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined;
  return { id, hintId, errorId, describedBy, invalid: Boolean(error) };
}

function FieldFrame({
  label,
  htmlFor,
  hint,
  hintId,
  error,
  errorId,
  required,
  badge,
  className,
  children,
}: FieldShellProps & {
  htmlFor?: string;
  hintId?: string;
  errorId?: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={cx(styles.field, className)}>
      {label ? (
        <span className={styles.labelRow}>
          <label className={styles.label} htmlFor={htmlFor}>
            {label}
            {required ? (
              <span className={styles.required} aria-hidden="true">
                *
              </span>
            ) : null}
          </label>
          {/* Provenance sits beside the label, the same way Stat carries its
              confidence — a machine-filled value must never appear without it.
              Outside the <label>, deliberately: anything inside becomes part of
              the control's accessible name. */}
          {badge ? <span className={styles.badge}>{badge}</span> : null}
        </span>
      ) : null}
      {children}
      {hint ? (
        <span className={styles.hint} id={hintId}>
          {hint}
        </span>
      ) : null}
      {error ? (
        <span className={styles.error} id={errorId} role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}

/* ── Text ───────────────────────────────────────────────────────────── */

export type FieldTextProps = FieldShellProps &
  Omit<InputHTMLAttributes<HTMLInputElement>, 'className' | 'required'>;

export function FieldText({ label, hint, error, required, className, ...rest }: FieldTextProps) {
  const { id, hintId, errorId, describedBy, invalid } = useFieldWiring({ hint, error });
  return (
    <FieldFrame
      label={label}
      htmlFor={id}
      hint={hint}
      hintId={hintId}
      error={error}
      errorId={errorId}
      required={required}
      className={className}
    >
      <input
        {...rest}
        id={id}
        className={styles.control}
        required={required}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
      />
    </FieldFrame>
  );
}

/* ── Select ─────────────────────────────────────────────────────────── */

export type FieldSelectProps = FieldShellProps &
  Omit<SelectHTMLAttributes<HTMLSelectElement>, 'className' | 'required'> & {
    children: ReactNode;
    badge?: ReactNode;
  };

export function FieldSelect({
  label,
  hint,
  error,
  required,
  badge,
  className,
  children,
  ...rest
}: FieldSelectProps) {
  const { id, hintId, errorId, describedBy, invalid } = useFieldWiring({ hint, error });
  return (
    <FieldFrame
      label={label}
      htmlFor={id}
      hint={hint}
      hintId={hintId}
      error={error}
      errorId={errorId}
      required={required}
      badge={badge}
      className={className}
    >
      <select
        {...rest}
        id={id}
        className={cx(styles.control, styles.select)}
        required={required}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
      >
        {children}
      </select>
    </FieldFrame>
  );
}

/* ── Textarea ───────────────────────────────────────────────────────── */

export type FieldTextareaProps = FieldShellProps &
  Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'className' | 'required'>;

export function FieldTextarea({
  label,
  hint,
  error,
  required,
  className,
  ...rest
}: FieldTextareaProps) {
  const { id, hintId, errorId, describedBy, invalid } = useFieldWiring({ hint, error });
  return (
    <FieldFrame
      label={label}
      htmlFor={id}
      hint={hint}
      hintId={hintId}
      error={error}
      errorId={errorId}
      required={required}
      className={className}
    >
      <textarea
        {...rest}
        id={id}
        className={cx(styles.control, styles.textarea)}
        required={required}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
      />
    </FieldFrame>
  );
}

/* ── Stepper ────────────────────────────────────────────────────────── */

export type FieldStepperProps = FieldShellProps & {
  /** Provenance marker rendered beside the label (auto-scout evidence badge). */
  badge?: ReactNode;
  value: number;
  onValueChange: (next: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  /** Used for the +/- button labels, e.g. "high goal" → "Increase high goal". */
  name?: string;
};

export function FieldStepper({
  label,
  hint,
  error,
  required,
  className,
  badge,
  value,
  onValueChange,
  min = 0,
  max = Number.MAX_SAFE_INTEGER,
  step = 1,
  disabled = false,
  name,
}: FieldStepperProps) {
  const { id, hintId, errorId, describedBy, invalid } = useFieldWiring({ hint, error });
  const subject = name ?? label ?? 'value';

  // Binary floating point makes 1 + 0.1 + 0.1 into 1.2000000000000002, which
  // then renders in full. Round to the precision the step itself implies, so a
  // step of 0.1 can only ever produce one decimal place.
  const decimals = (String(step).split('.')[1] ?? '').length;
  const clamp = (next: number) => {
    const bounded = Math.min(max, Math.max(min, next));
    return decimals === 0 ? bounded : Number(bounded.toFixed(decimals));
  };

  return (
    <FieldFrame
      label={label}
      htmlFor={id}
      hint={hint}
      hintId={hintId}
      error={error}
      errorId={errorId}
      required={required}
      badge={badge}
      className={className}
    >
      <div className={styles.stepper}>
        <button
          type="button"
          className={styles.stepperButton}
          onClick={() => onValueChange(clamp(value - step))}
          disabled={disabled || value <= min}
          aria-label={`Decrease ${subject}`}
        >
          −
        </button>
        <input
          id={id}
          type="number"
          inputMode="numeric"
          className={styles.stepperValue}
          value={value}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          aria-describedby={describedBy}
          aria-invalid={invalid || undefined}
          onChange={(event) => {
            const raw = event.target.value;
            // `Number('')` is 0, not NaN. Without this guard a scout clearing
            // the field to retype would silently zero the count they already
            // entered — and mid-match that number cannot be recovered.
            if (raw.trim() === '') return;
            const parsed = Number(raw);
            if (Number.isFinite(parsed)) onValueChange(clamp(parsed));
          }}
        />
        <button
          type="button"
          className={styles.stepperButton}
          onClick={() => onValueChange(clamp(value + step))}
          disabled={disabled || value >= max}
          aria-label={`Increase ${subject}`}
        >
          +
        </button>
      </div>
    </FieldFrame>
  );
}

/* ── Toggle ─────────────────────────────────────────────────────────── */

export type FieldToggleProps = {
  label: string;
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  hint?: string;
  badge?: ReactNode;
  disabled?: boolean;
  className?: string;
};

export function FieldToggle({
  label,
  checked,
  onCheckedChange,
  hint,
  badge,
  disabled = false,
  className,
}: FieldToggleProps) {
  const { id, hintId, describedBy } = useFieldWiring({ hint });
  return (
    <div className={cx(styles.field, className)}>
      <div className={styles.toggleRow}>
        <span className={styles.toggleLabel} id={`${id}-label`}>
          {label}
        </span>
        {badge ? <span className={styles.badge}>{badge}</span> : null}
        <button
          type="button"
          role="switch"
          className={styles.toggle}
          aria-checked={checked}
          aria-labelledby={`${id}-label`}
          aria-describedby={describedBy}
          disabled={disabled}
          onClick={() => onCheckedChange(!checked)}
        >
          <span className={styles.toggleKnob} />
        </button>
      </div>
      {hint ? (
        <span className={styles.hint} id={hintId}>
          {hint}
        </span>
      ) : null}
    </div>
  );
}

/* ── Checkbox ───────────────────────────────────────────────────────── */

export type FieldCheckboxProps = {
  label: ReactNode;
  className?: string;
} & Omit<InputHTMLAttributes<HTMLInputElement>, 'className' | 'type'>;

export function FieldCheckbox({ label, className, ...rest }: FieldCheckboxProps) {
  return (
    <label className={cx(styles.check, className)}>
      <input {...rest} type="checkbox" />
      <span>{label}</span>
    </label>
  );
}

/* ── RadioGroup ─────────────────────────────────────────────────────── */

export type RadioOption = {
  value: string;
  label: ReactNode;
  disabled?: boolean;
  /** This option is the absence of an answer, not one of the answers. Selected,
   *  it stays neutral instead of taking the accent — an unanswered 1-5 scale
   *  defaults to N/A, and painting that in the accent made the null answer the
   *  loudest control in the group. */
  muted?: boolean;
};

export type FieldRadioGroupProps = {
  legend: string;
  name: string;
  value: string;
  onValueChange: (next: string) => void;
  options: RadioOption[];
  inline?: boolean;
  /**
   * Render the options as a row of segments rather than a radio list. Used for
   * the 1–5 scales a scout taps mid-match: same real radios underneath, so the
   * group is announced as a group and arrow keys move between options, which a
   * row of plain buttons never gave.
   */
  segmented?: boolean;
  hint?: string;
  badge?: ReactNode;
  className?: string;
};

export function FieldRadioGroup({
  legend,
  name,
  value,
  onValueChange,
  options,
  inline = false,
  segmented = false,
  hint,
  badge,
  className,
}: FieldRadioGroupProps) {
  const { id, hintId, describedBy } = useFieldWiring({ hint });
  return (
    <fieldset className={cx(styles.fieldset, className)} aria-describedby={describedBy} id={id}>
      <legend className={cx(styles.legend, segmented && styles.legendRow)}>
        {legend}
        {badge ? <span className={styles.badge}>{badge}</span> : null}
      </legend>
      <div
        className={cx(
          segmented ? styles.segmentGroup : styles.radioGroup,
          !segmented && inline && styles.radioGroupInline,
        )}
      >
        {options.map((option) => (
          <label
            className={cx(
              segmented ? styles.segment : styles.check,
              option.muted && styles.segmentMuted,
            )}
            key={option.value}
            data-checked={value === option.value || undefined}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              disabled={option.disabled}
              onChange={() => onValueChange(option.value)}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
      {hint ? (
        <span className={styles.hint} id={hintId}>
          {hint}
        </span>
      ) : null}
    </fieldset>
  );
}
