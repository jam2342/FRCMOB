import type { SeasonCalibrationBin, SeasonModelNote } from '../api';

/* "Model updated 9 Mar · tuned on 6,214 matches" for a self-tuning season, or the preseason line
   before its first weekly update. */
export function modelNoteText(note: SeasonModelNote, locale?: string): string {
  if (note.provisional) {
    return 'Preseason model, tuned on earlier seasons. It starts tuning itself once this season has enough results.';
  }
  const updated = note.updated_at ? new Date(note.updated_at) : null;
  const when = updated && !Number.isNaN(updated.getTime())
    ? updated.toLocaleDateString(locale, { day: 'numeric', month: 'short', timeZone: 'UTC' })
    : null;
  const matches = `tuned on ${note.matches.toLocaleString(locale)} match${note.matches === 1 ? '' : 'es'}`;
  return when ? `Model updated ${when} · ${matches}` : `Model ${matches}`;
}

/* "60–70%": a calibration bin's range of the favourite's predicted chance. */
export function favouriteRangeLabel(bin: SeasonCalibrationBin): string {
  const from = Math.round(bin.favourite_from * 100);
  const to = Math.round(Math.min(bin.favourite_to, 1) * 100);
  return `${from}–${to}%`;
}
