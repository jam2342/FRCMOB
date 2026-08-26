import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import {
  Button,
  CardBody,
  CardEmpty,
  CardGrid,
  CardRow,
  Chip,
  FieldCheckbox,
  FieldRadioGroup,
  FieldSelect,
  FieldStepper,
  FieldText,
  FieldTextarea,
  FieldToggle,
  Modal,
  Stat,
  Table,
  type SortDirection,
  type TableColumn,
} from '../components/ui/primitives';
import styles from './PrimitivesPage.module.css';

// The primitives gallery. This is not decoration — it is how Phase 2's exit
// criteria are actually checked. Guards 3 and 4 sweep routes, so every
// primitive in every variant and state has to be *on* a route or the guards
// cannot see it. Every state below is rendered at once, deliberately.

type Ranking = {
  team: string;
  rank: number;
  rp: number;
  avgScore: number;
  record: string;
};

const RANKINGS: Ranking[] = [
  { team: 'frc1114', rank: 1, rp: 34, avgScore: 88.4, record: '11-1-0' },
  { team: 'frc254', rank: 2, rp: 32, avgScore: 91.2, record: '10-2-0' },
  { team: 'frc1678', rank: 3, rp: 31, avgScore: 84.7, record: '10-2-0' },
  { team: 'frc2056', rank: 4, rp: 29, avgScore: 79.1, record: '9-3-0' },
];

const COLUMNS: TableColumn<Ranking>[] = [
  { key: 'team', label: 'Team', sortable: true },
  { key: 'rank', label: 'Rank', numeric: true, sortable: true, width: '80px' },
  { key: 'rp', label: 'RP', numeric: true, sortable: true, width: '80px' },
  { key: 'avgScore', label: 'Avg score', numeric: true, sortable: true, width: '110px' },
  { key: 'record', label: 'Record', align: 'center', width: '110px' },
];

function ClockIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.6V8l2.2 1.6" />
    </svg>
  );
}

export function PrimitivesPage() {
  const [sortBy, setSortBy] = useState<{ key: string; direction: SortDirection }>({
    key: 'rank',
    direction: 'asc',
  });
  // Which modal is open lives in the URL, not in component state. Two reasons.
  // Guards 3 and 4 sweep routes and cannot click, so an overlay they cannot
  // address is an overlay they never measure — and an unmeasured modal is
  // exactly where a contrast bug hides. And moving between /primitives and
  // /primitives?state=modal changes only the fragment, so the browser does not
  // reload and this component never remounts; state seeded from the URL once
  // would stay stale for the rest of the sweep.
  //
  // This is also the shape EventsPage needs in wave 2, where eleven overlays
  // are really navigation.
  const [params, setParams] = useSearchParams();
  const state = params.get('state');
  const modal = state === 'modal' ? 'plain' : state === 'confirm' ? 'confirm' : null;
  const setModal = (next: null | 'plain' | 'confirm') => {
    const query = new URLSearchParams(params);
    if (next === null) query.delete('state');
    else query.set('state', next === 'plain' ? 'modal' : 'confirm');
    setParams(query, { replace: true });
  };
  const [count, setCount] = useState(3);
  const [toggle, setToggle] = useState(true);
  const [radio, setRadio] = useState('teleop');

  const sorted = [...RANKINGS].sort((a, b) => {
    const key = sortBy.key as keyof Ranking;
    const left = a[key];
    const right = b[key];
    const order = typeof left === 'number' && typeof right === 'number'
      ? left - right
      : String(left).localeCompare(String(right));
    return sortBy.direction === 'asc' ? order : -order;
  });

  return (
    <div className="center-page-container">
      <SurfaceCardGroup>
        <SurfaceCard
          title="Button"
          subtitle="Four variants, two sizes, every state. Replaces 52 button classes."
          expandable={false}
        >
          <CardBody>
            <div className={styles.row}>
              <Button variant="primary">Primary</Button>
              <Button>Default</Button>
              <Button variant="quiet">Quiet</Button>
              <Button variant="danger">Danger</Button>
            </div>
            <div className={styles.row}>
              <Button variant="primary" size="sm">Primary small</Button>
              <Button size="sm">Default small</Button>
              <Button variant="quiet" size="sm">Quiet small</Button>
              <Button variant="danger" size="sm">Danger small</Button>
            </div>
            <div className={styles.row}>
              <Button variant="primary" disabled>Disabled</Button>
              <Button loading>Loading</Button>
              <Button pressed>Pressed</Button>
              <Button icon={<ClockIcon />}>With icon</Button>
              <Button iconOnly icon={<ClockIcon />} aria-label="Match timer" />
              <Button as="a" href="#/settings" variant="quiet">Link button</Button>
            </div>
            <Button variant="primary" fullWidth>Full width</Button>
          </CardBody>
        </SurfaceCard>

        <SurfaceCard
          title="Chip"
          subtitle="Six tones. Red and blue are alliance-only; the dot is the second, non-colour channel."
          expandable={false}
        >
          <CardBody>
            <div className={styles.row}>
              <Chip>Neutral</Chip>
              <Chip tone="accent">Accent</Chip>
              <Chip tone="warn">Warning</Chip>
              <Chip tone="danger">Danger</Chip>
              <Chip tone="red">Red alliance</Chip>
              <Chip tone="blue">Blue alliance</Chip>
            </div>
            <div className={styles.row}>
              <Chip dot tone="accent">With dot</Chip>
              <Chip icon={<ClockIcon />} tone="warn">With icon</Chip>
              <Chip size="sm">Small</Chip>
              <Chip tone="accent" onRemove={() => undefined}>Removable</Chip>
            </div>
          </CardBody>
        </SurfaceCard>

        <SurfaceCard
          title="Stat"
          subtitle="Always tabular figures. Confidence is a first-class slot, never hidden."
          expandable={false}
        >
          <CardBody>
            <CardGrid>
              <Stat label="Avg score" value="88.4" />
              <Stat label="Cycle time" value="14.2" unit="s" trend={-1.8} trendSuffix="s" />
              <Stat label="Climb rate" value="92" unit="%" tone="success" trend={4} trendSuffix="%" />
              <Stat label="Fouls" value="3.1" tone="danger" trend={0} />
              <Stat label="Offense" value="4/5" tone="accent" confidence={0.82} sub="from 11 matches" />
              <Stat label="Defense" value="2/5" tone="warning" confidence={0.34} sub="low sample" />
            </CardGrid>
          </CardBody>
        </SurfaceCard>

        <SurfaceCard
          title="Card insides"
          subtitle="Body, row, grid and empty — the 84 classes that were reimplementing SurfaceCard's contents."
          expandable={false}
        >
          <CardBody>
            <div>
              <CardRow label="Drivetrain" value="Swerve" />
              <CardRow label="Weight" value="118 lb" />
              <CardRow label="Preferred start" value="Left" />
              <CardRow label="Open match log" value="12 matches" onClick={() => undefined} />
            </div>
            <CardEmpty title="No pit data yet" icon={<ClockIcon />}>
              This team has not been pit scouted at this event.
            </CardEmpty>
          </CardBody>
        </SurfaceCard>

        <SurfaceCard
          title="Field"
          subtitle="Seven variants over one wiring routine: label, hint, error and aria-describedby."
          expandable={false}
        >
          <CardBody>
            <FieldText label="Scout name" placeholder="Who is scouting" hint="Shown on every entry you save." />
            <FieldText label="Team number" required defaultValue="notanumber" error="Enter a team number." />
            <FieldSelect label="Event" hint="Only events you have joined appear here.">
              <option>2026 Arizona Valley</option>
              <option>2026 CA Sacramento</option>
            </FieldSelect>
            <FieldTextarea label="Notes" placeholder="What did this robot actually do?" rows={3} />
            <FieldStepper
              label="High goal"
              name="high goal"
              value={count}
              onValueChange={setCount}
              max={99}
              hint="Tapped during the match — the biggest targets in the system."
            />
            <FieldToggle
              label="Auto-advance to next match"
              checked={toggle}
              onCheckedChange={setToggle}
              hint="Moves to the next assigned match after you save."
            />
            <FieldCheckbox label="Robot was disabled at some point" defaultChecked />
            <FieldRadioGroup
              legend="Match phase"
              name="phase"
              value={radio}
              onValueChange={setRadio}
              options={[
                { value: 'auto', label: 'Auto' },
                { value: 'teleop', label: 'Teleop' },
                { value: 'endgame', label: 'Endgame' },
              ]}
              inline
            />
            <FieldText label="Locked field" defaultValue="Read only" disabled />
          </CardBody>
        </SurfaceCard>

        <SurfaceCard
          title="Modal"
          subtitle="Centred dialog above 560px, bottom sheet below. Focus trapped, focus restored, scroll locked."
          expandable={false}
        >
          <CardBody>
            <div className={styles.row}>
              <Button variant="primary" onClick={() => setModal('plain')}>Open dialog</Button>
              <Button variant="danger" onClick={() => setModal('confirm')}>Open confirm</Button>
            </div>
          </CardBody>
        </SurfaceCard>

        <SurfaceCard
          title="Table"
          subtitle="Numeric columns get tabular figures and right alignment. Below 560px each row becomes a card."
          expandable={false}
        >
          <CardBody>
            <Table
              columns={COLUMNS}
              rows={sorted}
              rowKey={(row) => row.team}
              sortBy={sortBy}
              onSort={(key, direction) => setSortBy({ key, direction })}
              stickyHeader
              stickyFirstCol
            />
            <Table columns={COLUMNS} rows={[]} rowKey={(row) => row.team} empty="No rankings published yet." />
          </CardBody>
        </SurfaceCard>
      </SurfaceCardGroup>

      <Modal
        open={modal === 'plain'}
        onClose={() => setModal(null)}
        title="Share this event"
        footer={
          <>
            <Button variant="quiet" onClick={() => setModal(null)}>Cancel</Button>
            <Button variant="primary" onClick={() => setModal(null)}>Copy link</Button>
          </>
        }
      >
        <CardBody>
          <p>Anyone with the link can view this event's scouting data. They cannot edit it.</p>
          <FieldText label="Link" defaultValue="https://frcmob.app/#/events" readOnly />
        </CardBody>
      </Modal>

      <Modal
        open={modal === 'confirm'}
        onClose={() => setModal(null)}
        title="Delete 12 scouting entries?"
        size="sm"
        dismissible={false}
        footer={
          <>
            <Button variant="quiet" onClick={() => setModal(null)}>Keep them</Button>
            <Button variant="danger" onClick={() => setModal(null)}>Delete</Button>
          </>
        }
      >
        <p>
          This cannot be undone, and match data collected during a competition cannot be re-collected.
        </p>
      </Modal>
    </div>
  );
}
