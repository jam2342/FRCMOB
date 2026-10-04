const DISMISSED_KEY = 'frcmob_tour_prompt_dismissed_v1';

export function tourPromptDismissed(): boolean {
  try {
    return window.localStorage.getItem(DISMISSED_KEY) === '1';
  } catch {
    return true;
  }
}

export function dismissTourPrompt() {
  try {
    window.localStorage.setItem(DISMISSED_KEY, '1');
  } catch {
    // Private mode: the prompt just comes back next visit.
  }
}
