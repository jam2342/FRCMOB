import type { CSSProperties } from 'react';
import type { TutorialStepPlacement } from '../../tutorial/tutorialBlueprints';

const DESKTOP_PANEL_WIDTH_PX = 400;
const DESKTOP_VIEWPORT_MARGIN_PX = 12;
const TARGET_GAP_PX = 16;

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

type Side = Exclude<TutorialStepPlacement, 'auto'>;
const SIDES: Side[] = ['right', 'left', 'bottom', 'top'];

// Beside the target, never on it. The panel used to be a fixed 320-560px tall and clamped into
// the screen when its preferred side had no room, so it often sat on the very card it described
// (Favorites' cards 89% covered, My Team's join form 64%). Now it takes the preferred side if the
// whole panel fits there, else the first side that fits, else the side with the most room.
export function placeDesktopPanel(
  rect: { top: number; left: number; right: number; bottom: number; width: number; height: number },
  preferred: TutorialStepPlacement,
  panelHeight: number,
  viewport: { width: number; height: number },
): CSSProperties {
  const margin = DESKTOP_VIEWPORT_MARGIN_PX;
  const width = Math.min(DESKTOP_PANEL_WIDTH_PX, viewport.width - margin * 2);
  const height = Math.min(panelHeight, viewport.height - margin * 2);
  const room: Record<Side, number> = {
    right: viewport.width - rect.right - TARGET_GAP_PX - margin,
    left: rect.left - TARGET_GAP_PX - margin,
    bottom: viewport.height - rect.bottom - TARGET_GAP_PX - margin,
    top: rect.top - TARGET_GAP_PX - margin,
  };
  const need = (side: Side) => (side === 'left' || side === 'right' ? width : height);
  const order = preferred === 'auto' ? SIDES : [preferred, ...SIDES.filter((side) => side !== preferred)];
  const side = order.find((candidate) => room[candidate] >= need(candidate))
    ?? order.reduce((best, candidate) => (room[candidate] / need(candidate) > room[best] / need(best) ? candidate : best));

  let top: number;
  let left: number;
  if (side === 'right' || side === 'left') {
    left = side === 'right' ? rect.right + TARGET_GAP_PX : rect.left - width - TARGET_GAP_PX;
    top = rect.top + Math.min(rect.height, height) / 2 - height / 2;
  } else {
    left = rect.left + rect.width / 2 - width / 2;
    top = side === 'bottom' ? rect.bottom + TARGET_GAP_PX : rect.top - height - TARGET_GAP_PX;
  }
  top = clamp(top, margin, Math.max(margin, viewport.height - height - margin));
  left = clamp(left, margin, Math.max(margin, viewport.width - width - margin));
  return { width: `${width}px`, top: `${top}px`, left: `${left}px`, maxHeight: `${viewport.height - top - margin}px` };
}

const MOBILE_MARGIN_PX = 8;
const HEADING_BAND_PX = 56;

// Which edge the phone coach card docks to. A card's heading sits at the top of its spotlight, so
// the card goes where that heading stays visible; with no such edge, where more of the target
// shows. The bottom edge wins ties: the thumb is already there.
export function pickMobileDock(
  spot: { top: number; height: number },
  panelHeight: number,
  viewportHeight: number,
): 'top' | 'bottom' {
  const band = Math.min(spot.height, HEADING_BAND_PX);
  const bottomEdge = viewportHeight - MOBILE_MARGIN_PX - panelHeight;
  const topEdge = MOBILE_MARGIN_PX + panelHeight;
  const bottomClear = spot.top + band <= bottomEdge;
  const topClear = spot.top >= topEdge;
  if (bottomClear) return 'bottom';
  if (topClear) return 'top';
  const shownBottom = Math.max(0, Math.min(spot.top + spot.height, bottomEdge) - Math.max(spot.top, 0));
  const shownTop = Math.max(0, Math.min(spot.top + spot.height, viewportHeight) - Math.max(spot.top, topEdge));
  return shownTop > shownBottom ? 'top' : 'bottom';
}
