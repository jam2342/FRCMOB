import type { PredictedScheduleMatch } from '../../api';
import { matchWinProbability } from './winProbability';
import styles from './MatchPredictionNote.module.css';

type Props = {
  match: PredictedScheduleMatch | null | undefined;
  completed: boolean;
  winner: 'red' | 'blue' | 'tie' | null;
};

// The match's own page says who the model favoured, from the same prediction the
// Predictions page shows; after the match, whether it called it.
export function MatchPredictionNote({ match, completed, winner }: Props) {
  const win = matchWinProbability(match);
  if (!win) return null;
  const favoured = win.red >= win.blue ? 'red' : 'blue';
  const chance = Math.round(Math.max(win.red, win.blue) * 100);
  const even = chance <= 52;
  const side = favoured === 'red' ? 'Red' : 'Blue';
  let text: string;
  if (!completed || !winner) {
    text = even ? 'Even match: about 50/50' : `${side} favoured · ${chance}% to win`;
  } else if (winner === 'tie' || even) {
    text = even ? 'We called it about 50/50' : `We gave ${side} ${chance}%`;
  } else {
    text = `We gave ${side} ${chance}% · ${winner === favoured ? 'called it' : 'upset'}`;
  }
  return (
    <span className={`${styles.note} ${even ? '' : styles[favoured]}`.trim()} data-testid="match-prediction-note">
      {text}
    </span>
  );
}
