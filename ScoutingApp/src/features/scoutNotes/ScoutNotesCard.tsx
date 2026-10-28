import { useEffect, useMemo, useState } from 'react';
import { getTeamNotes, type TeamNotesResponse } from '../../api';
import { SurfaceCard } from '../../components/ui/SurfaceCard';
import { Button, Chip, FieldText } from '../../components/ui/primitives';
import { matchLabelFromKey } from '../../utils/matchLabel';
import { eventDisplayName } from '../../utils/eventSearchIndex';
import { filterTeamNotes, isScoutNoteTag, noteTagLabel, WARNING_NOTE_TAGS } from './noteTags';
import styles from './ScoutNotes.module.css';

const FIRST_PAGE = 5;
// Below this many notes a search box is more chrome than help.
const SEARCH_FROM = 6;

type Props = { teamKey: string; eventKey?: string };

// Free-text notes and quick tags from your own team's scouts, read back where the team is
// being judged. Workspace-only on the server, and the card isn't drawn at all until there is
// something to say: an empty "no notes" card on every team was a screen of nothing.
export function ScoutNotesCard({ teamKey, eventKey }: Props) {
  const scope = `${teamKey}|${eventKey ?? ''}`;
  const [loaded, setLoaded] = useState<{ scope: string; data: TeamNotesResponse } | null>(null);
  const [tag, setTag] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let live = true;
    setTag(null);
    setQuery('');
    setShowAll(false);
    getTeamNotes(teamKey, eventKey)
      .then((data) => { if (live) setLoaded({ scope, data }); })
      .catch(() => { if (live) setLoaded(null); });
    return () => { live = false; };
  }, [teamKey, eventKey, scope]);

  const data = loaded?.scope === scope ? loaded.data : null;
  const filtered = useMemo(() => (data ? filterTeamNotes(data.notes, tag, query) : []), [data, tag, query]);
  if (!data || data.notes.length === 0) return null;

  const withText = data.notes.filter((note) => note.notes).length;
  const visible = showAll ? filtered : filtered.slice(0, FIRST_PAGE);
  const seasonScope = !eventKey;
  const subtitle = `${data.reports} report${data.reports === 1 ? '' : 's'} from your team`
    + (withText ? ` · ${withText} with notes` : '');

  return (
    <SurfaceCard title="What scouts said" subtitle={subtitle} compactable>
      {data.tag_counts.length > 0 ? (
        <div className={styles.filters} role="group" aria-label="Filter by tag">
          {data.tag_counts.filter((row) => isScoutNoteTag(row.tag)).map((row) => (
            <Button
              key={row.tag}
              size="sm"
              variant="quiet"
              pressed={tag === row.tag}
              className={styles.chip}
              onClick={() => { setTag((current) => (current === row.tag ? null : row.tag)); setShowAll(false); }}
            >
              {noteTagLabel(row.tag)} · {row.count}
            </Button>
          ))}
        </div>
      ) : null}
      {data.notes.length >= SEARCH_FROM ? (
        <FieldText
          label="Search notes"
          type="search"
          value={query}
          onChange={(event) => { setQuery(event.target.value); setShowAll(false); }}
          placeholder="pin, intake, climb…"
          className={styles.search}
        />
      ) : null}
      {filtered.length === 0 ? (
        <p className="center-callout muted">No notes match.</p>
      ) : (
        <ul className={styles.list}>
          {visible.map((note) => (
            <li key={note.id} className={styles.note}>
              <p className={styles.meta}>
                <strong>{note.match_key ? matchLabelFromKey(note.match_key) ?? note.match_key : 'Match'}</strong>
                {seasonScope && note.event_key ? <span> · {eventDisplayName(note.event_key)}</span> : null}
                <span> · {note.scout_profile}</span>
              </p>
              {note.tags.length > 0 ? (
                <div className={styles.tags}>
                  {note.tags.filter(isScoutNoteTag).map((value) => (
                    <Chip key={value} size="sm" tone={WARNING_NOTE_TAGS.has(value) ? 'warn' : 'neutral'}>
                      {noteTagLabel(value)}
                    </Chip>
                  ))}
                </div>
              ) : null}
              {note.notes ? <p className={styles.text}>{note.notes}</p> : null}
            </li>
          ))}
        </ul>
      )}
      {filtered.length > FIRST_PAGE ? (
        <Button size="sm" variant="quiet" onClick={() => setShowAll((value) => !value)}>
          {showAll ? 'Show fewer' : `Show all ${filtered.length}`}
        </Button>
      ) : null}
    </SurfaceCard>
  );
}
