import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { SegmentedTabs } from '../../components/ui/SegmentedTabs';
import { useMobileLayout } from '../../hooks/useMobileLayout';
import type { BracketMatch, BracketSide, DoubleElimBracket } from './bracketModel';
import styles from './KnockoutBracket.module.css';

// The double-elimination tree. On a desktop it's one picture: upper bracket on top, lower below,
// the finals top right above the lower final that feeds it. On a phone the two halves take turns
// under a tray and scroll sideways round by round.

const CARD_H = 64;
const FINALS_H = 96;
const DESKTOP_GAP = 24;
const DESKTOP_MIN_CARD = 136;
const DESKTOP_MAX_CARD = 200;
const PHONE_CARD = 136;
const PHONE_GAP = 28;
// The lower bracket's band starts here on a desktop, below the upper bracket.
const LOWER_TOP = 456;

// Each set's column and vertical centre within its half, 96px apart so a match's label clears
// the card above. Pairs meet halfway between them.
type Spot = { set: number; col: number; cy: number };
const UPPER: Spot[] = [
  { set: 1, col: 0, cy: 60 }, { set: 2, col: 0, cy: 156 }, { set: 3, col: 0, cy: 252 }, { set: 4, col: 0, cy: 348 },
  { set: 7, col: 1, cy: 108 }, { set: 8, col: 1, cy: 300 }, { set: 11, col: 2, cy: 204 },
];
const LOWER: Spot[] = [
  { set: 5, col: 0, cy: 60 }, { set: 6, col: 0, cy: 156 }, { set: 10, col: 1, cy: 60 }, { set: 9, col: 1, cy: 156 },
  { set: 12, col: 2, cy: 108 }, { set: 13, col: 3, cy: 108 },
];
const UPPER_JOINS: [number, number, number][] = [[1, 2, 7], [3, 4, 8], [7, 8, 11]];
const LOWER_JOINS: [number, number, number][] = [[10, 9, 12]];
const LOWER_STRAIGHT: [number, number][] = [[5, 10], [6, 9], [12, 13]];

type Box = { left: number; top: number; cy: number };
type Line = { kind: 'h' | 'v' | 'join'; style: CSSProperties };
type Layout = {
  width: number;
  height: number;
  card: number;
  boxes: Map<number, Box>;
  finals: Box;
  lines: Line[];
  heads: { left: number; top: number; text: string }[];
  sections: { top: number; text: string; note?: string }[];
};

function joinLines(boxes: Map<number, Box>, card: number, gap: number, joins: [number, number, number][], straights: [number, number][]): Line[] {
  const lines: Line[] = [];
  const half = gap / 2;
  for (const [a, b, to] of joins) {
    const A = boxes.get(a)!;
    const B = boxes.get(b)!;
    const T = boxes.get(to)!;
    const x = A.left + card;
    const top = Math.min(A.cy, B.cy);
    lines.push({ kind: 'join', style: { left: x, top, width: half, height: Math.abs(A.cy - B.cy) } });
    lines.push({ kind: 'h', style: { left: x + half, top: (A.cy + B.cy) / 2, width: T.left - x - half } });
  }
  for (const [from, to] of straights) {
    const A = boxes.get(from)!;
    const T = boxes.get(to)!;
    lines.push({ kind: 'h', style: { left: A.left + card, top: A.cy, width: T.left - A.left - card } });
  }
  return lines;
}

function desktopLayout(card: number): Layout {
  const gap = DESKTOP_GAP;
  const col = (c: number) => c * (card + gap);
  const boxes = new Map<number, Box>();
  for (const s of UPPER) boxes.set(s.set, { left: col(s.col), top: s.cy + 8, cy: s.cy + 40 });
  // The lower bracket starts a round later, under the upper bracket's round 2.
  for (const s of LOWER) boxes.set(s.set, { left: col(s.col + 1), top: LOWER_TOP + s.cy + 8, cy: LOWER_TOP + s.cy + 40 });
  const upperFinal = boxes.get(11)!;
  const finals: Box = { left: col(4), top: upperFinal.cy - FINALS_H / 2, cy: upperFinal.cy };
  const lowerFinal = boxes.get(13)!;
  const lines = [
    ...joinLines(boxes, card, gap, UPPER_JOINS, []),
    ...joinLines(boxes, card, gap, LOWER_JOINS, LOWER_STRAIGHT),
    { kind: 'h' as const, style: { left: upperFinal.left + card, top: upperFinal.cy, width: finals.left - upperFinal.left - card } },
    // The lower final feeds the finals from below, beside the column heading rather than through it.
    { kind: 'v' as const, style: { left: finals.left + card - 20, top: finals.top + FINALS_H, height: lowerFinal.top - finals.top - FINALS_H } },
  ];
  const heads = [
    [0, 'Round 1'], [1, 'Round 2'], [2, 'Upper final'], [4, 'Finals'],
  ].map(([c, text]) => ({ left: col(c as number), top: 26, text: text as string }))
    .concat([[1, 'Round 2'], [2, 'Round 3'], [3, 'Round 4'], [4, 'Lower final']]
      .map(([c, text]) => ({ left: col(c as number), top: LOWER_TOP + 26, text: text as string })));
  return {
    width: col(4) + card,
    height: LOWER_TOP + 156 + 40 + 40,
    card,
    boxes,
    finals,
    lines,
    heads,
    sections: [
      { top: 0, text: 'Upper bracket' },
      { top: LOWER_TOP, text: 'Lower bracket', note: 'Lose in the upper bracket and you drop in here. Lose here and you\'re out.' },
    ],
  };
}

function phoneLayout(half: 'upper' | 'lower'): Layout {
  const card = PHONE_CARD;
  const gap = PHONE_GAP;
  const col = (c: number) => c * (card + gap);
  const spots = half === 'upper' ? UPPER : LOWER;
  const boxes = new Map<number, Box>();
  for (const s of spots) boxes.set(s.set, { left: col(s.col), top: s.cy - 12, cy: s.cy + 20 });
  const feeder = boxes.get(half === 'upper' ? 11 : 13)!;
  const finalsCol = half === 'upper' ? 3 : 4;
  const finals: Box = { left: col(finalsCol), top: feeder.cy - FINALS_H / 2, cy: feeder.cy };
  const lines = [
    ...(half === 'upper'
      ? joinLines(boxes, card, gap, UPPER_JOINS, [])
      : joinLines(boxes, card, gap, LOWER_JOINS, LOWER_STRAIGHT)),
    { kind: 'h' as const, style: { left: feeder.left + card, top: feeder.cy, width: finals.left - feeder.left - card } },
  ];
  const names = half === 'upper'
    ? ['Round 1', 'Round 2', 'Upper final', 'Finals']
    : ['Round 2', 'Round 3', 'Round 4', 'Lower final', 'Finals'];
  const bottom = Math.max(...[...boxes.values()].map((b) => b.top + CARD_H), finals.top + FINALS_H);
  return {
    width: col(finalsCol) + card,
    height: bottom + 8,
    card,
    boxes,
    finals,
    lines,
    heads: names.map((text, i) => ({ left: col(i), top: 6, text })),
    sections: [],
  };
}

function SideRow({ side, color }: { side: BracketSide; color: 'red' | 'blue' }) {
  const classes = [styles.row, side.won ? styles.won : '', side.placeholder ? styles.placeholder : ''].filter(Boolean).join(' ');
  return (
    <span className={classes}>
      <span className={side.placeholder ? styles.bar : color === 'red' ? styles.barRed : styles.barBlue} aria-hidden="true" />
      <span className={styles.seed}>{side.seed ?? ''}</span>
      <span className={styles.name} title={side.teams || undefined}>{side.name}</span>
      <span className={styles.score}>{side.score ?? ''}</span>
    </span>
  );
}

function describeMatch(match: { red: BracketSide; blue: BracketSide }): string {
  const part = (s: BracketSide) => `${s.seed ? `Alliance ${s.seed} ` : ''}${s.name}${s.score !== null ? ` ${s.score}` : ''}${s.won ? ', winner' : ''}`;
  return `${part(match.red)} versus ${part(match.blue)}`;
}

function MatchCard({ match, onOpen }: { match: BracketMatch; onOpen: (key: string) => void }) {
  const body = (
    <>
      <SideRow side={match.red} color="red" />
      <SideRow side={match.blue} color="blue" />
    </>
  );
  if (!match.matchKey) return <div className={styles.card}>{body}</div>;
  const key = match.matchKey;
  return (
    <button type="button" className={styles.card} onClick={() => onOpen(key)} aria-label={`${match.label}: ${describeMatch(match)}`}>
      {body}
    </button>
  );
}

export function KnockoutBracket({ bracket, onOpenMatch }: { bracket: DoubleElimBracket; onOpenMatch: (matchKey: string) => void }) {
  const phone = useMobileLayout();
  const [half, setHalf] = useState<'upper' | 'lower'>('upper');
  const [width, setWidth] = useState(0);
  const [moreEnd, setMoreEnd] = useState(false);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    const frame = frameRef.current;
    if (!frame || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(() => setWidth(frame.clientWidth));
    observer.observe(frame);
    setWidth(frame.clientWidth);
    return () => observer.disconnect();
  }, []);

  const card = Math.max(DESKTOP_MIN_CARD, Math.min(DESKTOP_MAX_CARD, Math.floor((width - 4 * DESKTOP_GAP) / 5)));
  const layout = phone ? phoneLayout(half) : desktopLayout(width ? card : DESKTOP_MIN_CARD);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return undefined;
    const measure = () => setMoreEnd(scroller.scrollLeft + scroller.clientWidth < scroller.scrollWidth - 2);
    measure();
    scroller.addEventListener('scroll', measure, { passive: true });
    return () => scroller.removeEventListener('scroll', measure);
  }, [layout.width, width, half]);

  const { champion, finals } = bracket;
  const finalsStarted = finals.games.length > 0;
  const spots = phone ? (half === 'upper' ? UPPER : LOWER) : [...UPPER, ...LOWER];

  return (
    <div className={styles.bracket}>
      {finalsStarted ? (
        <section className={styles.champion} aria-label="Finals result">
          <div className={styles.championText}>
            {champion ? (
              <>
                <span className={styles.championLabel}>Champions</span>
                <span className={styles.championName}>{champion.name}</span>
                <span className={styles.championMeta}>
                  {champion.seed ? `Alliance ${champion.seed} · ` : ''}{champion.teams}
                </span>
              </>
            ) : (
              <>
                <span className={styles.eyebrow}>Finals · best of 3</span>
                <span className={styles.championName}>{finals.red.name} {finals.redWins}–{finals.blueWins} {finals.blue.name}</span>
              </>
            )}
          </div>
          <div className={styles.games}>
            {finals.games.map((game) => (
              <div key={game.label} className={styles.game}>
                <span className={styles.gameMeta}>{game.label}</span>
                <span className={styles.gameScore}>{game.red}–{game.blue}</span>
                <span className={styles.gameMeta}>
                  {game.winner === 'red' ? finals.red.name : game.winner === 'blue' ? finals.blue.name : 'Tie'}
                </span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {phone ? (
        <>
          <SegmentedTabs
            ariaLabel="Bracket half"
            value={half}
            onChange={setHalf}
            items={[
              { value: 'upper', label: 'Upper bracket' },
              { value: 'lower', label: 'Lower bracket' },
            ]}
          />
          <span className={styles.hint}>
            {half === 'upper'
              ? 'Swipe sideways for later rounds. Losers drop to the lower bracket.'
              : 'One loss here and an alliance is out. Swipe for the lower final.'}
          </span>
        </>
      ) : null}

      <div ref={frameRef} className={`${styles.scrollFrame} ${moreEnd ? styles.moreEnd : ''}`.trim()}>
        <div ref={scrollerRef} className={styles.scroller} data-no-tab-swipe="">
          <div className={styles.tree} style={{ width: layout.width, height: layout.height }}>
            {layout.sections.map((section) => (
              <span key={section.text}>
                <span className={styles.section} style={{ top: section.top }}>{section.text}</span>
                {section.note ? (
                  <span className={styles.sectionNote} style={{ top: section.top + 1, left: 120 }}>{section.note}</span>
                ) : null}
              </span>
            ))}
            {layout.heads.map((head) => (
              <span key={`${head.top}-${head.text}`} className={styles.head} style={{ left: head.left, top: head.top, width: layout.card }}>
                {head.text}
              </span>
            ))}
            {layout.lines.map((line, i) => (
              <span
                key={i}
                aria-hidden="true"
                className={line.kind === 'h' ? styles.hline : line.kind === 'v' ? styles.vline : styles.join}
                style={line.style}
              />
            ))}
            {spots.map((spot) => {
              const box = layout.boxes.get(spot.set)!;
              const match = bracket.matches[spot.set];
              return (
                <div key={spot.set} className={styles.slot} style={{ left: box.left, top: box.top, width: layout.card }}>
                  <span className={styles.matchId}>{match.label}</span>
                  <MatchCard match={match} onOpen={onOpenMatch} />
                </div>
              );
            })}
            <div className={styles.slot} style={{ left: layout.finals.left, top: layout.finals.top, width: layout.card }}>
              {finals.matchKey ? (
                <button
                  type="button"
                  className={styles.finalsCard}
                  onClick={() => onOpenMatch(finals.matchKey!)}
                  aria-label={`Finals: ${describeMatch(finals)}`}
                >
                  <span className={styles.finalsHead}>Best of 3</span>
                  <SideRow side={finals.red} color="red" />
                  <SideRow side={finals.blue} color="blue" />
                </button>
              ) : (
                <div className={styles.finalsCard}>
                  <span className={styles.finalsHead}>Best of 3</span>
                  <SideRow side={finals.red} color="red" />
                  <SideRow side={finals.blue} color="blue" />
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
      <span className={styles.hint}>Tap a match for its score breakdown.</span>
    </div>
  );
}
