// The primitives' only shared helper. Kept separate so each primitive imports
// one thing rather than reaching into a sibling component's module.
export function cx(...args: (string | false | null | undefined | 0)[]): string {
  return args.filter(Boolean).join(' ');
}
