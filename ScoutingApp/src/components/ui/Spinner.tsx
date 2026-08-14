import './Spinner.css';

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      className="ui-spinner"
      style={{ width: size, height: size }}
    >
      <circle
        cx="12"
        cy="12"
        r="9"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeDasharray="40"
        strokeDashoffset="10"
        strokeLinecap="round"
      />
    </svg>
  );
}
