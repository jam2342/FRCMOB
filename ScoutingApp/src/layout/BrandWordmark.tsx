// The hand-drawn FRCMOB wordmark: strokes on a 100-unit cap height. FRC takes the
// surrounding text colour and MOB the accent, so both themes stay in step.
const LETTERS = [
  { w: 58, d: 'M9 100 V9 H58 M9 52 H48' },
  { w: 68, d: 'M9 100 V9 H40 A23 23 0 0 1 40 55 H9 M36 55 L64 100' },
  { w: 82, d: 'M78 24 A41 41 0 1 0 78 76' },
  { w: 94, d: 'M9 100 V9 L47 62 L85 9 V100' },
  { w: 90, d: 'M45 9 A41 41 0 1 1 44.9 9 Z' },
  { w: 70, d: 'M9 100 V9 H40 A21.5 21.5 0 0 1 40 52 H9 M9 52 H44 A19.5 19.5 0 0 1 44 91 H9' },
];
const GAP = 15;
const OFFSETS = LETTERS.map((_, i) => LETTERS.slice(0, i).reduce((x, letter) => x + letter.w + GAP, 0));

export function BrandWordmark({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="-6 0 552 100" role="img" aria-label="FRCMOB">
      {LETTERS.map((letter, i) => (
        <path
          key={letter.d}
          transform={`translate(${OFFSETS[i]} 0)`}
          d={letter.d}
          fill="none"
          stroke={i < 3 ? 'currentColor' : 'var(--color-accent-text)'}
          strokeWidth={18}
          strokeMiterlimit={8}
        />
      ))}
    </svg>
  );
}
