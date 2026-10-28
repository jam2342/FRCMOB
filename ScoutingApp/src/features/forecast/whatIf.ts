export type Choice = 'win' | 'model' | 'loss';

// The what-if request's lists, in a stable order so the same choices ask the same question.
export function whatIfLists(choices: Record<string, Choice>): { wins: string[]; losses: string[] } {
  const entries = Object.entries(choices).sort(([a], [b]) => a.localeCompare(b));
  return {
    wins: entries.filter(([, choice]) => choice === 'win').map(([key]) => key),
    losses: entries.filter(([, choice]) => choice === 'loss').map(([key]) => key),
  };
}
