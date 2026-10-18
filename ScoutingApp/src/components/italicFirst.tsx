import { Fragment, type ReactNode } from 'react';

// FIRST's policy italicises its word mark wherever it can.
export function italicFirst(text: string): ReactNode {
  return text.split(/(FIRST)/).map((part, index) => (
    part === 'FIRST' ? <i key={index}>FIRST</i> : <Fragment key={index}>{part}</Fragment>
  ));
}
