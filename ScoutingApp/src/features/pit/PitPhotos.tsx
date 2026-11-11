import { useRef } from 'react';
import type { ChangeEvent, RefObject } from 'react';
import { pitPhotoDisplayUrl } from '../../api';
import type { PitScoutingEntry } from '../../api';
import { Button } from '../../components/ui/primitives';
import { AlertTriangleIcon, CameraIcon, CloseIcon, CloudSyncIcon, ImageIcon } from '../../components/ui/Icons';
import { teamNumber } from './pitTeams';
import type { WaitingPhoto } from './usePitPhotoQueue';
import styles from './PitForm.module.css';
import './pit.css';

export type PitPhotosProps = {
  teamKey: string;
  entry: PitScoutingEntry | null;
  uploading: boolean;
  // Deleting uploaded photos works on the server copy, so it waits for it to load.
  disabled: boolean;
  onAdd: (file: File | null) => Promise<void>;
  onDelete: (photoPath: string) => void;
  // Shots still on this phone, waiting for signal.
  waiting?: WaitingPhoto[];
  onDeleteWaiting?: (id: string) => void;
  onRetryWaiting?: (ids: string[]) => void;
};

export function PitPhotos({ teamKey, entry, uploading, disabled, onAdd, onDelete, waiting = [], onDeleteWaiting, onRetryWaiting }: PitPhotosProps) {
  const cameraRef = useRef<HTMLInputElement | null>(null);
  const libraryRef = useRef<HTMLInputElement | null>(null);
  const uploaded = entry?.photos ?? [];
  const mine = waiting.filter((photo) => photo.teamKey === teamKey);
  const refused = mine.filter((photo) => photo.refused);
  const reasons = [...new Set(refused.map((photo) => photo.error || "The server didn't accept it."))];

  const pick = (input: RefObject<HTMLInputElement | null>) => (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0] ?? null;
    // Clearing lets the same shot be picked again after a failed upload.
    void onAdd(file).finally(() => { if (input.current) input.current.value = ''; });
  };

  return (
    <div className={styles.photos}>
      <Button
        variant="default"
        fullWidth
        className={styles.takePhoto}
        icon={<CameraIcon />}
        loading={uploading}
        disabled={uploading}
        onClick={() => cameraRef.current?.click()}
      >
        {uploading ? 'Saving photo…' : 'Take photo'}
      </Button>
      <Button variant="quiet" size="sm" fullWidth icon={<ImageIcon />} disabled={uploading} onClick={() => libraryRef.current?.click()}>
        Choose from library
      </Button>
      <input
        ref={cameraRef}
        className={styles.hiddenInput}
        aria-label="Take robot photo"
        type="file"
        accept="image/*"
        capture="environment"
        tabIndex={-1}
        onChange={pick(cameraRef)}
      />
      <input
        ref={libraryRef}
        className={styles.hiddenInput}
        aria-label="Choose robot photo from library"
        type="file"
        accept="image/*"
        tabIndex={-1}
        onChange={pick(libraryRef)}
      />

      {uploaded.length + mine.length > 0 ? (
        <ul className={styles.strip} aria-label={`Photos of team ${teamNumber(teamKey)}`}>
          {mine.map((photo) => (
            <li key={photo.id} className={styles.thumb}>
              <img src={photo.url} alt={`Robot ${teamNumber(teamKey)}, ${photo.refused ? 'not uploaded' : 'waiting to upload'}`} />
              {photo.refused ? (
                <span className={styles.waitingTag} data-tone="warn" title={photo.error ?? undefined}>
                  <AlertTriangleIcon />
                  Not uploaded
                </span>
              ) : (
                <span className={styles.waitingTag} title={photo.error ? `Saved on this phone. Last try: ${photo.error}` : 'Saved on this phone. Uploads when you have signal.'}>
                  <CloudSyncIcon />
                  Waiting to upload
                </span>
              )}
              {onDeleteWaiting ? (
                <button type="button" className={styles.thumbDelete} aria-label="Delete photo waiting to upload" onClick={() => onDeleteWaiting(photo.id)}>
                  <CloseIcon />
                </button>
              ) : null}
            </li>
          ))}
          {uploaded.map((photo, index) => (
            <li key={photo} className={styles.thumb}>
              <img src={entry ? pitPhotoDisplayUrl(entry, index) : ''} alt={`Robot ${teamNumber(teamKey)}`} loading="lazy" />
              <button type="button" className={styles.thumbDelete} aria-label="Delete photo" disabled={disabled} onClick={() => onDelete(photo)}>
                <CloseIcon />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {refused.length > 0 ? (
        <p className="center-callout warning" role="alert">
          {refused.length === 1 ? "A photo on this phone wasn't accepted" : `${refused.length} photos on this phone weren't accepted`}: {reasons.join(' ')}
          {onRetryWaiting ? (
            <>
              {' '}
              <Button variant="quiet" size="sm" onClick={() => onRetryWaiting(refused.map((photo) => photo.id))}>Try again</Button>
            </>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}
