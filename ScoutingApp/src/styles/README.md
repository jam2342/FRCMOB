# Visual decisions

Three decisions that the codemods and the guards both enforce. They are written
down because the remodel that came before this one normalised a composition
nobody had decided, and produced a very tidy version of the same screen.

Measured on `main` at `b4f7f64`, before any of this: the largest text on Home
was 15px against a 12px median — a dynamic range of 1.25x, where a page with a
working hierarchy runs 3-5x. 546 raw `font-size` literals against 112 token
references, in 59 distinct spellings. 177 `SurfaceCard`s. Every failure passed
all five existing guards, because flat passes contrast.

---

## 1. The type ladder

Each step has a job. If a size does not have one of these jobs, it does not get
a size. Defined in `tokens.css`.

| Token | Size | Job |
|---|---|---|
| `--font-size-display` | 36px | **The one value per screen.** Never a label. |
| `--font-size-3xl` | 28px | Hero. The display step on mobile. |
| `--font-size-2xl` | 23px | Page titles. |
| `--font-size-xl` | 19px | Card and section titles. |
| `--font-size-lg` | 17px | Sub-headings. |
| `--font-size-md` | 15px | Emphasised body. |
| `--font-size-base` | 14px | Reading text. Anything in sentences. |
| `--font-size-sm` | 13px | Table cells, chips, metadata. |
| `--font-size-2xs` | 12px | Uppercase eyebrows and labels. Never a sentence. |

`--font-size-xs` is an alias of `sm`; both are 13px. Kept so existing call sites
do not have to churn.

**`--font-size-legacy-10` and `--font-size-legacy-11` are not part of the
ladder.** They exist only so the codemod could move 546 literals with a provable
ceiling of 1px per element — the app had grown text down to 8.96px through
compounding `em`, and there was nowhere on the ladder to put it. `guard-type`
counts their uses and the count only goes down. Do not add a third.

### Weight is not a substitute for size

Between a quarter and three-quarters of every screen was set at weight 700 — 72%
on Picklist. Bold has two steps, so it cannot build a hierarchy; when most of a
page is bold, the 400-weight text reads as disabled rather than as body copy.
Reach for the ladder first.

---

## 2. Three container levels

`SurfaceCard` takes a `level`:

- **`plain`** — content sits on the page, grouped by space. No border, no fill,
  no radius.
- **`section`** — a heading and a hairline rule. Still not a box.
- **`card`** — an actual bordered surface.

The rule that decides between them:

> **A box means "this is a thing", not "these are related".**
> Related is what space is for.

A card is for something you can act on, or that stands for a real entity: a
match, a team, an assignment. Not for "these four numbers are both about
scouting". Events rendered 170 boxes before this rule; it should render about as
many boxes as it has events.

Nested boxes are the clearest violation — an empty state inside a card is a box
inside a box.

---

## 2b. Subtitles

`SurfaceCard` takes a `subtitle`. 110 of the 133 in the app were a noun phrase
naming what the card contained — "Scouting pipeline metrics." under *FRC
Scouting Metrics*, "Registered events." under *Competitions* — which is the most
recognisable generated-UI copy pattern there is.

**A subtitle earns its place only if it says something the title cannot:**

- a unit — "Average seconds per zone"
- a scope limit — "Teleop + endgame + offense/defense (auto excluded)"
- a behaviour or caveat — "edits sync automatically", "Ratings appear after save"
- a legend — "Green = completed, camera = has photos"
- what to do next, in an empty state — "Pick an event in the Finder"

Anything else is a caption. 70 were removed on that rule; where a card really
needs context, make it data (`34 matches · last 22 Mar`) rather than prose.

---

## 3. One hero per route

Each route names the single value that gets `--font-size-display`. A screen is
allowed to have no hero. It is not allowed to have four.

| Route | Hero | Note |
|---|---|---|
| `/home` | Time to the next match | Only when a match has a published start time |
| `/events` | — | A list. The list is the hero. |
| `/events/dashboard` | Top rating in the field | Read against the field average. A name is a label, not a hero |
| `/team-center` | Team rating | Was 17.6px in a bordered box |
| `/match-center` | Final or predicted score | Already correct at 32px |
| `/match-center/predictions` | Model accuracy | Baselined by matches called right |
| `/match-center/strategy` | — | Briefing prose |
| `/scouting` | Match clock | The one number a scout looks up for |
| `/scouting/pit` | — | A form |
| `/scouting/assignments` | — | A grid |
| `/scouting/coverage` | Coverage percentage | Baselined by slots covered |
| `/scouting/calibrate` | — | A canvas |
| `/scouting/record` | Match clock | |
| `/compare` | — | Two columns; a hero would pick a side |
| `/compare/picklist` | — | A ranked list. The order is the hero. |
| `/compare/alliance-advisor` | Alliance score | Only once an alliance is built |
| `/favorites` | — | A list |
| `/settings` | — | A form |
| `/privacy`, `/terms` | — | Documents |
| `/primitives` | — | A gallery |

---

## 3b. Spacing has roles, not just sizes

The numeric scale (`--space-1` … `--space-16`) says *how big*. These say *what
for*, and they are what you reach for:

| Token | Role |
|---|---|
| `--card-stack-gap` | Between sibling cards |
| `--card-padding` | Inside a card |
| `--card-section-gap` | Between blocks within a card |

A page that reaches for a number picks whatever looked right that day. The gap
between two stacked cards was 16px on nineteen routes, 12px on three, and 10px,
11px and 14px on Live Scouting — `.center-main` alone was declared in three
files with three different values, one of which was 11. `guard:consistency`
asserts every card stack in the app resolves to the same gap.

Only 15% of spacing declarations use a token today (226 against 1,305 raw
values, in 53 distinct spellings). The role tokens are the ones that matter;
the rest is a ratchet for later.

---

## 4. Pages in a family agree

The other rules are about one screen at a time. This one is about two screens
disagreeing, which is where most of the shell's bugs have come from:

- **Spacing under the view bar belongs to the bar.** It used to belong to each
  page's own wrapper, so Live Scouting had no gap and Pit Scouting had 16px
  purely because they used different containers.
- **A nested scroll container inside `.ps-content` is almost always an
  accident.** `overflow-x: hidden` computes the *other* axis to `auto`; use
  `clip` when you only mean to clip. Live Scouting's workspace column inherited
  the sticky, height-capped, independently-scrolling treatment written for a
  *finder*, and the page stopped scrolling.
- **A control names its own region.** Four panel toggles existed and three read
  "Collapse": the nav rail, the context strip, the Finder and the Scouting setup
  header. They are Menu, Context, Finder and Setup now.
- **One destination, one name.** The same page was "Compare" and "Open Builder";
  "Events" and "Event Center"; "Match Center" and "Match Hub". Three kinds of
  label are legitimate and distinct — a *section* name in the nav, a *page* name
  in the view bar, and a *contextual action* on a cross-link ("This event") —
  but two nouns for one place is not.

`guard:consistency` enforces all three. It was written after those fixes and
immediately found two more: Auto Paths and the Data Dashboard render the view
bar *inside* the page container rather than beside it, so the container's grid
gap landed on top of the bar's margin and both sat 32px down while five
siblings sat at 16.

---

## 5. One look per kind of thing

Rule 4 is about sibling pages. This one is about every copy of a thing, anywhere. A sweep of
every route at 390px and 1366px on 2026-10-18 found each of these broken in several places,
always the same way: a page built its own copy of a shared piece, and nothing compared the
copies. Each law names the one piece that owns the look, and what enforces it.
`consistency.test.ts` reads the source (it runs with `npm test`); `guard:uniformity` renders
every route at phone and desktop size and compares what came out.

| Law | The one piece | Was |
|---|---|---|
| **One text box.** Every text box, search box and select has the same height, corner, border, fill, padding and text size. | `styles/uniform.css` + the `--control-*` tokens | Set in six places: Favorites' boxes had 16px text beside 13px ones, the phone search was white with an 11px corner, the header search a pill, two boxes showed Chrome's blue ×. |
| **One event search.** Searching for an event is `EventPicker`, with one results list and one "no results" message. | `components/EventPicker.tsx` | Home had its own search with its own dropdown, rows, hover and "No events found". |
| **One tab look.** Switching views is `SegmentedTabs`, which picks the look itself: an underline strip on phones, a pill group on desktop. A strip with more tabs past an edge fades at that edge. `variant="board"` is the scouting board's large segments, nothing else. | `components/ui/SegmentedTabs.tsx` | Five looks: pages passed class names (underline on one phone page, boxed pills on the next), Predictions used filter chips, Home and the recorder hand-built tab rows, Coverage had its own grid. Events' strip showed "Fo" for Forecast with nothing saying it scrolls. |
| **One card title** and **one page title.** | `styles/uniform.css` | Card titles came in four weight/spacing combinations. On phones an event's name was 14px and cut to one line, a team's 19px and wrapped. |
| **Text is never cut silently.** Text cut with "…" or a line clamp carries its full text as a title. | each component; `guard:uniformity` finds misses | Live Ratings names, Compare's team pool, Events standings and collapsed card titles were cut with no way to read them. |
| **A table fits its card.** When the columns don't fit, the rows show as cards; when there's room again, as a table. | `Table` primitive (measures its own box) | Four tables scrolled sideways on a 1366px desktop, cut at the card edge: Team Center's schedule needed 611px in a 371px column. |
| **Fullscreen is for content that outgrows a card**: a table, chart, heat map, video. Opt in with `expandable`. | `SurfaceCard` (off by default) | On by default: ~120 cards, five or six identical corner buttons per desktop screen. |
| **One empty state.** | `EmptyState` | A second component (`CardEmpty`) drew a dashed box inside the card: a box in a box. |
| **A missing value is "—"**, a change is `signedChange` ("+1.2", "−0.3", "0.0", never "-0.0"), a stat has one decimal. | `utils/formatValue.ts`, `metric()` | "N/A" wherever `metric()` ran and "—" elsewhere, often on one screen; Live Ratings showed "▼ -0.0"; Auto read 67.30 on Compare and 67.3 on Team Center. TBA's ranking score keeps its two decimals. |
| **A page's change/back actions sit in one row at the top** ("Change event", "‹ Back to QM 1"). | `MobileViewActions` | Match Center hand-built both, one inside the score card and one in the sidebar. |

Exceptions are explicit, never per page: a control that is a different kind of thing (a
stepper's number, the ⌘K palette's line, the hidden date input behind Home's day switcher)
says so with its own module or `data-control="bare"`.

---

## Guards

`npm run guard` runs, in order — cheapest and least flaky first:

| Guard | Checks | Needs a browser |
|---|---|---|
| `guard:colours` | A colour may only enter through a custom property | no |
| `guard:type` | Same, for `font-size`. Plus the legacy-token count. | no |
| `guard:hierarchy` | One display element per route; largest / median >= 2.5x | yes |
| `guard:boxes` | Per-route ceiling on border + radius + fill elements | yes |
| `guard:consistency` | Siblings line up; no new nested scrollers or shared control names | yes |
| `guard:contrast` | Both themes, all routes | yes |
| `guard:overflow` | 390px and up | yes |
| `guard:affordances` | Named, reachable controls | yes |
| `guard:uniformity` | One look per kind of thing (rule 5), phone and desktop | yes |

**A hero must be a value, not a label.** The guard checks the hero's text for a
digit, because a 36px word is the page shouting the name of the thing instead of
the thing. That also means a page with no data must not render its hero at all —
an em-dash on the display step is a placeholder shouting.

**Four routes cannot be measured from a cold start**: `/home` (no match today),
`/events/dashboard` (its own event picker), `/compare/alliance-advisor` (needs a
built alliance, a POST behind admin auth) and `/scouting/record` (needs a
camera). `guard:hierarchy` reports those as `~` and does not fail on them —
that says "this screen has no data", not "this screen has no hero". They are
verified separately by `node e2e/probe-conditional-heroes.mjs`, which drives
each into its populated state and measures the real rendered page.

The budget guards are budgets rather than flat bans because a flat ban would
have failed 546 times the day it landed, and a rule everyone switches off
enforces nothing. Each file gets its current count as a ceiling; adding fails,
removing is reported so the ceiling can be lowered.
