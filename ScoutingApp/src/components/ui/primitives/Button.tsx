import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from 'react';
import styles from './Button.module.css';
import { cx } from './cx';

export type ButtonVariant = 'primary' | 'default' | 'quiet' | 'danger';
export type ButtonSize = 'sm' | 'md';

type CommonProps = {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  fullWidth?: boolean;
  loading?: boolean;
  pressed?: boolean;
  className?: string;
};

// An icon-only button has no accessible name unless one is supplied, and that
// is the single easiest a11y regression to ship. The type makes it impossible:
// `iconOnly` requires both an icon and an aria-label, and forbids children.
type IconOnlyProps = {
  iconOnly: true;
  icon: ReactNode;
  'aria-label': string;
  children?: never;
};

type LabelledProps = {
  iconOnly?: false;
  children: ReactNode;
};

type ButtonElementProps = CommonProps &
  (IconOnlyProps | LabelledProps) & {
    as?: 'button';
  } & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className' | 'children'>;

type AnchorElementProps = CommonProps &
  (IconOnlyProps | LabelledProps) & {
    as: 'a';
  } & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'className' | 'children'>;

export type ButtonProps = ButtonElementProps | AnchorElementProps;

export function Button(props: ButtonProps) {
  const {
    variant = 'default',
    size = 'md',
    icon,
    iconOnly = false,
    fullWidth = false,
    loading = false,
    pressed,
    className,
    children,
    as = 'button',
    ...rest
  } = props as CommonProps & {
    iconOnly?: boolean;
    children?: ReactNode;
    as?: 'button' | 'a';
  } & Record<string, unknown>;

  const classes = cx(
    styles.button,
    styles[variant],
    size === 'sm' && styles.sm,
    fullWidth && styles.fullWidth,
    iconOnly && styles.iconOnly,
    className,
  );

  // While loading the button keeps its width and stays in the tab order, but
  // swaps the icon for a spinner and stops accepting activation.
  const leading = loading ? (
    <span className={styles.spinner} aria-hidden="true" />
  ) : icon ? (
    <span className={styles.icon} aria-hidden="true">
      {icon}
    </span>
  ) : null;

  const body = (
    <>
      {leading}
      {iconOnly ? null : children}
    </>
  );

  if (as === 'a') {
    const anchorProps = rest as AnchorHTMLAttributes<HTMLAnchorElement>;
    const inert = loading || anchorProps['aria-disabled'] === true;
    return (
      <a
        {...anchorProps}
        className={classes}
        aria-busy={loading || undefined}
        aria-pressed={pressed}
        aria-disabled={inert || undefined}
        // An anchor cannot be `disabled`, so remove it from the tab order and
        // drop its destination rather than leaving a live link that looks dead.
        href={inert ? undefined : anchorProps.href}
        tabIndex={inert ? -1 : anchorProps.tabIndex}
      >
        {body}
      </a>
    );
  }

  const buttonProps = rest as ButtonHTMLAttributes<HTMLButtonElement>;
  return (
    <button
      type="button"
      {...buttonProps}
      className={classes}
      disabled={buttonProps.disabled || loading}
      aria-busy={loading || undefined}
      aria-pressed={pressed}
    >
      {body}
    </button>
  );
}
