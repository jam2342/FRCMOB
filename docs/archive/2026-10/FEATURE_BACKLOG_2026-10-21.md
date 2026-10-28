# Feature backlog — 2026-10-21

Polish and feature ideas that build on what FRCMOB already has. Each item names the existing code it extends, so nothing here starts from zero. Tick items off as they ship and link the PR.

All but one shipped on branch `claude/happy-volta-oafe1w` (one commit each). The lock-screen countdown is still open: see its note.

Quality bar for every item: unit tests, `npm run guard`, axe in light and dark, phone width, and a quiet no-op where a device lacks the capability. Prefer an existing surface (Settings, More sheet, share sheet) over a new permanent button.

## Device polish

### Quick wins
- [x] **Haptics on iPhone.** `utils/haptics.ts` uses `navigator.vibrate`, which does nothing on iOS Safari or in the Capacitor iOS app. Route the three helpers through `@capacitor/haptics` when running natively.
- [x] **Keep the screen on mid-match.** Hold a screen wake lock while a match is being scouted or recorded; release it on save, leave, or hide.
- [x] **Home-screen shortcuts.** Add `shortcuts` to `public/manifest.json`: Scout next match, My Team, Match Center.
- [x] **Feel the phase change.** A distinct haptic when auto ends and when endgame starts, driven by the scout countdown (`pages/scoutDraftTimer.ts`).

### Cherry on top
- [x] **Add our matches to my calendar.** An `.ics` subscription on My Team, using the backend's match-time estimates (`services/events/match_times.py`).
- [x] **Event recap card.** A second layout for `features/shareCard/renderShareCard.ts`: record, best match, rating change, scouting coverage and top scouts.
- [x] **App icon badge.** `navigator.setAppBadge` for unscouted assignments or results since the last visit (the away-summary logic already counts these).
- [x] **A small win moment.** A short, reduced-motion-aware flourish on a team win or playoff clinch, built on `RollingScore` and `CountUp`.

### Bigger bets
- [ ] **Lock-screen next-match countdown.** iOS Live Activity and an Android ongoing notification. Native work on both platforms; best after the native beta. Not built: it needs Swift (ActivityKit) and Kotlin code plus push-token plumbing, which can't be built or checked without Xcode and devices.
- [x] **Scout thank-yous.** An end-of-day personal note from the leaderboard data ("14 matches, best streak 9").

## App features

### Strategy and scouting
- [x] **What scouts said.** Scouting entries collect free-text notes, but Team Center never shows them. Add a "What scouts said" card with quick tags (defense, tipped, brownout, fast cycles) that can be filtered and searched.
- [x] **Live flags.** One tap in the scouting form to flag "broke down", "playing defense" or "great driver" during a match, pushed through the scouting room so the strategist sees it straight away.
- [x] **Strategy whiteboard.** Draw paths and zones on the field image inside Strategy Briefing, saved per match and shared with the drive team.
- [x] **Selection-day draft board.** A full-screen alliance-selection view that joins the picklist's picked/declined state, `BestNextPick`, and the eight alliance slots, so best-available updates with each tap.

### Predictions and what-ifs
- [x] **Custom match-up.** Pick any six teams and get predicted scores and win probability. Today predictions cover scheduled matches only.
- [x] **"What do we need?" rank simulator.** Choose results for the remaining quals and see your projected rank, building on the event forecast (`services/simulation/forecast.py`).

### Fun and retention
- [x] **Head-to-head history.** On Compare and Team Center: how often two teams have played with and against each other, and the results, across seasons.
- [x] **Season story.** A My Team timeline of the season: events, rating over time (`RatingHistoryCard`), best matches and awards, ending in a recap card.
- [x] **Scout training mode.** New scouts practice on a past match, and their entry is compared with the official breakdown (`services/scoring/truth.py`) to show where they missed points.

## Where what shipped differs from the plan

- **Event recap card**: finish, record, qual rank, rating and best match. No scouting coverage or top scouts, since the picture is often shared outside the team; the scout thank-you covers the team's own scouting.
- **App icon badge**: counts the scout's assignments still to do. Results since the last visit are not counted.
- **Win moment**: shows when your team wins a match while you watch it come in. A playoff clinch gets no separate moment.
- **Head to head**: on Compare only, not Team Center.
- **Season story**: no awards. The app doesn't store awards, so they would mean a TBA call per event.
- **Draft board**: if TBA has no rankings for the event, captains come from the standings worked out from the official results, and the board says so.
- **Practice scouting**: climbs are checked exactly. Fuel is only official per alliance, so a robot's count is only shown as its share of that total.
