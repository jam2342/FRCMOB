import { Button } from '../ui/primitives';
import './TourPrompt.css';

// Tours used to open on their own as a modal on every section, so a first
// visit meant dismissing one popup per page. Now there is one small offer,
// shown once, that never blocks the page.
export function TourPrompt({ onStart, onDismiss }: { onStart: () => void; onDismiss: () => void }) {
  return (
    <aside className="tour-prompt" aria-label="Page tour">
      <p className="tour-prompt__text">
        <strong>New here?</strong> Take a one-minute tour of this page.
      </p>
      <div className="tour-prompt__actions">
        <Button size="sm" variant="primary" onClick={onStart}>Show me</Button>
        <Button size="sm" variant="quiet" onClick={onDismiss}>No thanks</Button>
      </div>
    </aside>
  );
}
