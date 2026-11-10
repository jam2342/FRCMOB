import { useRef } from 'react';
import { pitPhotoDisplayUrl } from '../../api';
import type { PitScoutingEntry } from '../../api';
import { CloseIcon } from '../../components/ui/Icons';
import { teamNumber } from './pitTeams';
import './pit.css';

export type PitPhotosProps = {
  teamKey: string;
  entry: PitScoutingEntry | null;
  uploading: boolean;
  // Photos upload and delete against the server copy, so they wait for it to load.
  disabled: boolean;
  onAdd: (file: File | null) => Promise<void>;
  onDelete: (photoPath: string) => void;
};

export function PitPhotos({ teamKey, entry, uploading, disabled, onAdd, onDelete }: PitPhotosProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  return (
    <div className="pit-photos">
      {(entry?.photos ?? []).map((photo, index) => (
        <figure key={photo} className="pit-photo-item">
          <img src={entry ? pitPhotoDisplayUrl(entry, index) : ''} alt={`Robot ${teamNumber(teamKey)}`} loading="lazy" />
          <button
            type="button"
            className="pit-photo-delete"
            onClick={() => onDelete(photo)}
            aria-label="Delete photo"
            disabled={disabled}
          >
            <CloseIcon />
          </button>
        </figure>
      ))}
      <label className={`pit-photo-add ${uploading ? 'busy' : ''}`}>
        <input
          ref={fileInputRef}
          aria-label="Add robot photo"
          type="file"
          accept="image/*"
          capture="environment"
          onChange={(event) => {
            const file = event.target.files?.[0] ?? null;
            // Clearing lets the same shot be picked again after a failed upload.
            void onAdd(file).finally(() => {
              if (fileInputRef.current) fileInputRef.current.value = '';
            });
          }}
          disabled={uploading || disabled}
        />
        {uploading ? 'Uploading…' : '+ Photo'}
      </label>
    </div>
  );
}
