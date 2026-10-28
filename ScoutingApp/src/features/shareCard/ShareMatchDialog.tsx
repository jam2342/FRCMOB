import { renderShareCard, winnerLine, type ShareCardMatch } from './renderShareCard';
import { SharePictureDialog } from './SharePictureDialog';

type ShareMatchDialogProps = {
  open: boolean;
  onClose: () => void;
  match: Omit<ShareCardMatch, 'siteLabel'>;
  // The app route the picture links back to.
  route: string;
  filename: string;
};

export function ShareMatchDialog({ open, onClose, match, route, filename }: ShareMatchDialogProps) {
  const text = `${match.matchLabel} at ${match.eventName}: ${match.redScore}–${match.blueScore}. ${winnerLine(match)}.`;
  return (
    <SharePictureDialog
      open={open}
      onClose={onClose}
      title="Share this result"
      render={(siteLabel) => renderShareCard({ ...match, siteLabel })}
      cardKey={JSON.stringify(match)}
      text={text}
      route={route}
      filename={filename}
    />
  );
}
