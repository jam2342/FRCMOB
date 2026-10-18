import { useEffect, useState } from 'react';
import { Button } from '../../components/ui/primitives/Button';
import { Modal } from '../../components/ui/primitives/Modal';
import { showToast } from '../../components/ui/toast';
import { copyTextToClipboard } from '../../pages/centerUtils';
import { isNativeApp, shareableAppUrl } from '../../platform/runtime';
import { renderShareCard, winnerLine, type ShareCardMatch } from './renderShareCard';
import { savePicture, sharePicture } from './shareMatch';
import styles from './ShareMatchDialog.module.css';

type ShareMatchDialogProps = {
  open: boolean;
  onClose: () => void;
  match: Omit<ShareCardMatch, 'siteLabel'>;
  // The app route the picture links back to.
  route: string;
  filename: string;
};

export function ShareMatchDialog({ open, onClose, match, route, filename }: ShareMatchDialogProps) {
  const [picture, setPicture] = useState<{ blob: Blob; url: string } | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const link = shareableAppUrl(route);
  const siteLabel = new URL(link).host;
  const cardKey = JSON.stringify(match);

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    let made: string | null = null;
    setPicture(null);
    setFailed(false);
    renderShareCard({ ...match, siteLabel })
      .then((blob) => {
        if (cancelled) return;
        made = URL.createObjectURL(blob);
        setPicture({ blob, url: made });
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
    // cardKey stands for the match's contents.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, cardKey, siteLabel]);

  const text = `${match.matchLabel} at ${match.eventName}: ${match.redScore}–${match.blueScore}. ${winnerLine(match)}.`;

  const share = async () => {
    if (!picture) return;
    setBusy(true);
    try {
      const outcome = await sharePicture(picture.blob, filename, text, link);
      if (outcome === 'saved') showToast({ message: 'Sharing isn’t available here, so the picture was saved instead', tone: 'info' });
      if (outcome !== 'cancelled') onClose();
    } catch {
      showToast({ message: 'Could not share the picture. Try Save picture.', tone: 'warning' });
    } finally {
      setBusy(false);
    }
  };

  const copyLink = async () => {
    const copied = await copyTextToClipboard(link);
    showToast({ message: copied ? 'Link copied' : 'Could not copy the link on this device', tone: copied ? 'success' : 'warning' });
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Share this result"
      size="sm"
      footer={(
        <div className={styles.actions}>
          <Button variant="primary" onClick={() => void share()} disabled={!picture || busy}>
            {busy ? 'Opening…' : 'Share picture'}
          </Button>
          {!isNativeApp() ? (
            <Button onClick={() => picture && savePicture(picture.blob, filename)} disabled={!picture}>Save picture</Button>
          ) : null}
          <Button variant="quiet" onClick={() => void copyLink()}>Copy link</Button>
        </div>
      )}
    >
      <div className={styles.preview}>
        {picture ? (
          <img src={picture.url} alt={`Picture of ${text}`} className={styles.image} />
        ) : failed ? (
          <p className={styles.message}>This device couldn’t make the picture. Copy link still works.</p>
        ) : (
          <div className={styles.placeholder} aria-label="Making the picture" role="img" />
        )}
      </div>
    </Modal>
  );
}
