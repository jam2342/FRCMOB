# Guards

Browser checks that run against the app and fail loudly. They exist so the CSS
remodel can't silently break things a human wouldn't notice: a control that
disappears, text that vanishes in one theme, a page that scrolls sideways on a
phone.

They are plain Node scripts using `playwright` — no extra test framework. All
of them need the app running:

```bash
npm run dev -- --port 5199 --host 127.0.0.1
```

Point them elsewhere with `GUARD_BASE_URL`.

## Running against real data

The guards work with no backend, but most pages then render an empty state —
and the bugs live in the components that only appear once there is data. Result
tables, prediction rows and ranking chips accounted for 121 contrast failures
that an empty-state sweep could not see at all.

With a backend and an ingested event:

```bash
export GUARD_BASE_URL=http://127.0.0.1:5173   # a CORS-allowed port
export GUARD_EVENT_KEY=2026arc
export GUARD_TEAM_KEY=frc1114
npm run guard
```

Seeding writes the selected event and team into localStorage before the app
boots, which is what turns Team Center, Match Center and Compare from a prompt
into a populated screen. Both variables are optional and independent.

Guard 1 keeps a separate baseline per mode — `affordances.json` unseeded,
`affordances.seeded.json` seeded — because a selected match legitimately
replaces "No Match Selected". Comparing across modes would report dozens of
phantom losses.

| Command | Guard | Catches |
|---|---|---|
| `npm run guard:colours` | 5 | A colour hardcoded instead of taken from a token |
| `npm run guard:contrast` | 3 | Text that fails WCAG AA in either theme |
| `npm run guard:overflow` | 4 | Horizontal scroll at 320px and 390px |
| `npm run guard:affordances` | 1 | A button/label/card that disappeared |
| `npm run guard` | all | Runs the four in sequence |

Guard 5 is the only one that needs no browser, so it is first in the sequence —
it fails in under a second.

Each exits nonzero on failure, so they drop straight into CI.

## Why the contrast math lives in `lib/contrast.mjs`

Two earlier throwaway versions of this check produced confidently wrong answers
— a ratio of `9.6e8`, and `1.66:1` for dark text on a white page — because they
treated `rgba(0,0,0,0)` as opaque black and never composited translucent
layers. Colour maths is easy to get wrong and impossible to eyeball.

So the browser side only reports colour *strings*, and every calculation happens
in `lib/contrast.mjs`, which is pure and unit-tested by `lib/contrast.test.mjs`
under the normal `npm test` run. If a guard result ever looks suspicious, the
test file is where to check the maths — not the guard script.

One case worth knowing about: this codebase uses `color-mix()` heavily, which
Chrome computes to `color(srgb r g b / a)`. The first version of the parser
didn't handle that and silently skipped 62% of the page. Unparseable colours are
counted and reported rather than assumed readable.

## The affordance baseline

`guard-affordances.mjs` compares the app against
`e2e/baselines/affordances.json` — every button label, `aria-label`, `title`,
heading, internal link and form control, per route.

```bash
npm run guard:affordances            # compare
node e2e/guard-affordances.mjs --update   # accept the current state
```

The remodel is a styling migration, so this set should stay identical. Anything
lost is a bug. Anything added should be deliberate — review the diff, then
`--update` and commit the change in its own commit so the intent is on record.

## The colour budget

`guard-colours.mjs` enforces one rule: a colour may only enter the system
through a custom property. `--color-danger: #ef4444` is how a colour gets
defined; `color: #ef4444` is how it gets copied, and copying is what produced
878 distinct colour values in a product with 74 tokens.

It is a budget rather than a flat ban because a flat ban would have failed 809
times the day it landed, and a rule everyone switches off enforces nothing.
Each file carries its current count as a ceiling: adding a literal fails,
removing one is reported so the ceiling can be lowered.

```bash
npm run guard:colours                 # check
node e2e/guard-colours.mjs --update   # lock in an improvement
```

A file at zero is banned outright, which is where they all end up.

## Telling whether a CSS change moved any pixels

`style-snapshot.mjs` records every element's resolved colour properties across
all routes and both themes; `style-diff.mjs` compares two captures by CIEDE2000
and buckets the differences by whether a human could see them.

```bash
node e2e/style-snapshot.mjs /tmp/before.json
# ... make the change, rebuild ...
node e2e/style-snapshot.mjs /tmp/after.json
node e2e/style-diff.mjs /tmp/before.json /tmp/after.json
```

This is what makes a colour codemod defensible. Rewriting `#16212c` as
`var(--color-bg-elevated)` changes the text of every rule it touches while
moving nothing anyone can see, so the only useful question is how many changes
land above the just-noticeable difference of about 2.3 — and when one does, this
names the element and the property instead of handing back a red blob.

It also survives a row of data changing between runs, which a screenshot diff
does not. Sanity-check it the same way it was built: two captures with no edits
between them must differ in zero of ~29,600 elements.

There is no committed baseline — a snapshot is ~30 MB and would be stale within
a day. It is a before/after tool for a specific change, not a standing check.

## Running the guards

Run them **one at a time when writing baselines**. `npm run guard` fires eight
browser sweeps back to back, and a single-worker local backend cannot keep up:
the team-intel endpoint takes about 30s cold, requests start failing, and a page
that never loaded gets recorded as the truth. The box census once baselined 131
boxes instead of 145 that way, and the affordance snapshot reported forty
controls lost that a direct check found present on three consecutive loads.

    node e2e/guard-boxes.mjs --update      # one
    sleep 4                                # let the backend catch up
    node e2e/guard-boxes.mjs               # verify before moving on

`settleContent` in lib/harness.mjs guards against this — it measures the
`.ps-content` region rather than `document.body`, because the shell chrome is
about 200 characters on every route and masks an empty page, and it reloads up
to three times below 400 characters. It cannot rescue a backend that is simply
saturated.
