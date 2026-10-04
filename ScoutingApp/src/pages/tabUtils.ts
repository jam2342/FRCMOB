export function resolveTab<T extends string>(
  value: string | null,
  validTabs: readonly T[],
  fallback: T,
): T {
  return value !== null && validTabs.includes(value as T) ? value as T : fallback;
}
