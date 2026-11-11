import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { pitPhotoDisplayUrl, type PitScoutingEntry } from '../../api';
import { MediaImage } from '../../components/ui/MediaImage';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { relativeFromTimestamp } from '../../pages/centerUtils';
import { PIT_KEY_FIELDS, pitAnswerFor, pitAnswerSections, pitPhotoCount, pitTeamCenterPath } from './pitAnswers';
import styles from './PitSummary.module.css';
import { usePitEntries } from './usePitEntries';

interface PitSummaryProps {
  teamKey: string;
  eventKey: string;
  // The compact one is for small places: a few answers, a photo count and a way to the rest.
  variant?: 'full' | 'compact';
}

function scoutedLine(entry: PitScoutingEntry): string | null {
  const when = entry.updated_at ? Date.parse(entry.updated_at) : NaN;
  const ago = Number.isFinite(when) ? relativeFromTimestamp(when) : '';
  const who = (entry.scout_profile || '').trim();
  if (who && ago) return `Scouted by ${who} · ${ago}`;
  if (who) return `Scouted by ${who}`;
  return ago ? `Scouted ${ago}` : null;
}

function PhotoViewer({ entry, start, onClose }: { entry: PitScoutingEntry; start: number; onClose: () => void }) {
  const [index, setIndex] = useState(start);
  const ref = useRef<HTMLDivElement | null>(null);
  const count = pitPhotoCount(entry);
  useFocusTrap(ref, true);

  const step = useCallback((delta: number) => setIndex((current) => (current + delta + count) % count), [count]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      else if (count > 1 && event.key === 'ArrowLeft') step(-1);
      else if (count > 1 && event.key === 'ArrowRight') step(1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [count, onClose, step]);

  return createPortal(
    <div className={styles.viewer} role="dialog" aria-modal="true" aria-label={`Pit photo ${index + 1} of ${count}`} ref={ref} tabIndex={-1}>
      <button type="button" className={styles.viewerScrim} onClick={onClose} aria-label="Close photo" tabIndex={-1} />
      <figure className={styles.viewerFigure}>
        <MediaImage
          src={pitPhotoDisplayUrl(entry, index)}
          alt={`Pit photo ${index + 1} of ${count}`}
          className={styles.viewerImage}
          fallback={<p className={styles.viewerMissing}>This photo couldn&apos;t be loaded.</p>}
        />
        <figcaption className={styles.viewerBar}>
          {count > 1 ? (
            <button type="button" className={styles.viewerButton} onClick={() => step(-1)}>Previous</button>
          ) : null}
          <span>{index + 1} / {count}</span>
          {count > 1 ? (
            <button type="button" className={styles.viewerButton} onClick={() => step(1)}>Next</button>
          ) : null}
          <button type="button" className={styles.viewerButton} onClick={onClose}>Close</button>
        </figcaption>
      </figure>
    </div>,
    document.body,
  );
}

function PhotoStrip({ entry }: { entry: PitScoutingEntry }) {
  const [open, setOpen] = useState<number | null>(null);
  const count = pitPhotoCount(entry);
  const close = useCallback(() => setOpen(null), []);
  if (count === 0) return null;
  return (
    <>
      <ul className={styles.photos} aria-label="Pit photos">
        {Array.from({ length: count }, (_, index) => (
          <li key={index}>
            <button type="button" className={styles.thumb} onClick={() => setOpen(index)} aria-label={`Open photo ${index + 1} of ${count}`}>
              <MediaImage
                src={pitPhotoDisplayUrl(entry, index)}
                alt=""
                loading="lazy"
                decoding="async"
                fallback={<span className={styles.thumbMissing}>—</span>}
              />
            </button>
          </li>
        ))}
      </ul>
      {open !== null ? <PhotoViewer entry={entry} start={open} onClose={close} /> : null}
    </>
  );
}

export function PitSummary({ teamKey, eventKey, variant = 'full' }: PitSummaryProps) {
  const { inWorkspace, loading, entries } = usePitEntries(eventKey);
  // Pit notes belong to a workspace. Outside one there is nothing to say, not even "none".
  if (!inWorkspace || !eventKey || loading) return null;
  const entry = entries.get(teamKey.toLowerCase()) ?? null;

  if (!entry) {
    return (
      <p className={styles.empty}>
        <span>Not pit scouted yet</span>
        <Link className={styles.link} to={`/scouting/pit?${new URLSearchParams({ event: eventKey, team: teamKey })}`}>Pit scout →</Link>
      </p>
    );
  }

  const meta = scoutedLine(entry);
  const photos = pitPhotoCount(entry);

  if (variant === 'compact') {
    const answers = PIT_KEY_FIELDS.flatMap((key) => {
      const answer = pitAnswerFor(entry, key);
      return answer ? [answer] : [];
    });
    return (
      <div className={styles.root} data-pit-summary="compact">
        {answers.length > 0 ? (
          <dl className={styles.list}>
            {answers.map(({ field, text }) => (
              <div key={field.key} className={styles.row}>
                <dt>{field.label}</dt>
                <dd>{text}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className={styles.meta}>No key answers yet.</p>
        )}
        <p className={styles.footer}>
          <span className={styles.meta}>{[photos > 0 ? `${photos} ${photos === 1 ? 'photo' : 'photos'}` : null, meta].filter(Boolean).join(' · ')}</span>
          <Link className={styles.link} to={pitTeamCenterPath(teamKey, eventKey)}>Pit notes →</Link>
        </p>
      </div>
    );
  }

  const sections = pitAnswerSections(entry);
  return (
    <div className={styles.root} data-pit-summary="full">
      {sections.map((section) => (
        <section key={section.title} className={styles.section} aria-label={section.title}>
          <h4 className={styles.eyebrow}>{section.title}</h4>
          <dl className={styles.list}>
            {section.answers.map(({ field, text }) => (
              <div key={field.key} className={styles.row}>
                <dt>{field.label}</dt>
                <dd>{text}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
      {sections.length === 0 ? <p className={styles.meta}>Visited, but no answers recorded yet.</p> : null}
      <PhotoStrip entry={entry} />
      {meta ? <p className={styles.meta}>{meta}</p> : null}
    </div>
  );
}
