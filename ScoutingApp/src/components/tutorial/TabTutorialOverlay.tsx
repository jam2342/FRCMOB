import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import type { TutorialScope } from '../../layout/userSettings';
import {
  getTutorialBlueprint,
  type TutorialStepPlacement,
  type TutorialWalkthroughStep,
} from '../../tutorial/tutorialBlueprints';
import { markAllTutorialsSeen, markTutorialSeen } from '../../tutorial/tutorialState';
import { TutorialRobotMascot } from './TutorialRobotMascot';

type SpotlightRect = { top: number; left: number; width: number; height: number };

type TabTutorialOverlayProps = {
  scope: TutorialScope;
  onClose: () => void;
};

const MOBILE_BREAKPOINT_PX = 900;
const DESKTOP_PANEL_WIDTH_PX = 400;
const DESKTOP_PANEL_HEIGHT_PX = 560;
const DESKTOP_VIEWPORT_MARGIN_PX = 12;
const TARGET_GAP_PX = 16;
const MOBILE_DOCK_SWITCH_RATIO = 0.5;

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

function resolveSelector(step: TutorialWalkthroughStep, isMobile: boolean): string | undefined {
  if (isMobile) return step.mobileSelector || step.selector;
  return step.selector || step.mobileSelector;
}

function findTargetElement(selector: string | undefined): HTMLElement | null {
  if (!selector) return null;
  try {
    const nodes = Array.from(document.querySelectorAll<HTMLElement>(selector));
    if (!nodes.length) return null;
    return nodes.find((n) => {
      const r = n.getBoundingClientRect();
      return r.width > 2 && r.height > 2;
    }) || nodes[0] || null;
  } catch {
    return null;
  }
}

function buildSpotlightRect(rect: DOMRect): SpotlightRect {
  const padding = 6;
  return {
    top: Math.max(6, rect.top - padding),
    left: Math.max(6, rect.left - padding),
    width: Math.max(18, rect.width + padding * 2),
    height: Math.max(18, rect.height + padding * 2),
  };
}

function resolvePlacement(preferred: TutorialStepPlacement, rect: DOMRect): Exclude<TutorialStepPlacement, 'auto'> {
  if (preferred !== 'auto') return preferred;
  const spaceRight = window.innerWidth - rect.right;
  const spaceLeft = rect.left;
  const spaceBottom = window.innerHeight - rect.bottom;
  if (spaceRight >= DESKTOP_PANEL_WIDTH_PX + TARGET_GAP_PX + 32) return 'right';
  if (spaceLeft >= DESKTOP_PANEL_WIDTH_PX + TARGET_GAP_PX + 32) return 'left';
  if (spaceBottom >= DESKTOP_PANEL_HEIGHT_PX * 0.6) return 'bottom';
  return 'top';
}

function computeDesktopPanelStyle(rect: DOMRect, placement: Exclude<TutorialStepPlacement, 'auto'>): CSSProperties {
  const width = Math.min(DESKTOP_PANEL_WIDTH_PX, window.innerWidth - DESKTOP_VIEWPORT_MARGIN_PX * 2);
  const fallbackHeight = Math.min(DESKTOP_PANEL_HEIGHT_PX, window.innerHeight - DESKTOP_VIEWPORT_MARGIN_PX * 2);
  const maxTop = window.innerHeight - fallbackHeight - DESKTOP_VIEWPORT_MARGIN_PX;
  let top = DESKTOP_VIEWPORT_MARGIN_PX;
  let left = DESKTOP_VIEWPORT_MARGIN_PX;

  if (placement === 'right') {
    left = rect.right + TARGET_GAP_PX;
    top = rect.top + rect.height / 2 - fallbackHeight / 2;
  } else if (placement === 'left') {
    left = rect.left - width - TARGET_GAP_PX;
    top = rect.top + rect.height / 2 - fallbackHeight / 2;
  } else if (placement === 'bottom') {
    left = rect.left + rect.width / 2 - width / 2;
    top = rect.bottom + TARGET_GAP_PX;
  } else {
    left = rect.left + rect.width / 2 - width / 2;
    top = rect.top - fallbackHeight - TARGET_GAP_PX;
  }

  top = clamp(top, DESKTOP_VIEWPORT_MARGIN_PX, Math.max(DESKTOP_VIEWPORT_MARGIN_PX, maxTop));
  left = clamp(
    left,
    DESKTOP_VIEWPORT_MARGIN_PX,
    Math.max(DESKTOP_VIEWPORT_MARGIN_PX, window.innerWidth - width - DESKTOP_VIEWPORT_MARGIN_PX),
  );
  const height = Math.max(320, Math.min(DESKTOP_PANEL_HEIGHT_PX, window.innerHeight - top - DESKTOP_VIEWPORT_MARGIN_PX));
  return { width: `${width}px`, height: `${height}px`, maxHeight: `${height}px`, top: `${top}px`, left: `${left}px` };
}

function resolveRobotPose(stepNumber: number): 'wave' | 'point-left' | 'point-right' {
  if (stepNumber % 4 === 0) return 'point-left';
  if (stepNumber % 3 === 0) return 'point-right';
  return 'wave';
}

function TutorialRobotGuide({
  step,
  stepNumber,
  totalSteps,
}: {
  step: TutorialWalkthroughStep | null;
  stepNumber: number;
  totalSteps: number;
}) {
  const guideLine = step ? step.title : 'Ready when you are.';
  const pose = resolveRobotPose(stepNumber);

  return (
    <div className="tut-guide" aria-hidden="true">
      <div className="tut-robot-stage">
        <TutorialRobotMascot pose={pose} talking />
      </div>
      <div className="tut-guide-bubble is-talking">
        <div className="tut-guide-kicker-row">
          <p className="tut-guide-kicker">ScoutBot</p>
          <span className="tut-guide-meter" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        </div>
        <p className="tut-guide-line">
          Step {stepNumber}/{totalSteps}: {guideLine}
        </p>
      </div>
    </div>
  );
}

export function TabTutorialOverlay({ scope, onClose }: TabTutorialOverlayProps) {
  const blueprint = useMemo(() => getTutorialBlueprint(scope), [scope]);
  const [stepIndex, setStepIndex] = useState(0);
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= MOBILE_BREAKPOINT_PX);
  const [spotlight, setSpotlight] = useState<SpotlightRect | null>(null);
  const [targetFound, setTargetFound] = useState(false);
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({});

  const totalSteps = blueprint.walkthrough.length;
  const safeIndex = totalSteps ? Math.max(0, Math.min(stepIndex, totalSteps - 1)) : 0;
  const step = totalSteps ? blueprint.walkthrough[safeIndex] : null;
  const atStart = safeIndex === 0;
  const atEnd = safeIndex >= totalSteps - 1;
  const mobileDockClass =
    spotlight && spotlight.top + spotlight.height / 2 > window.innerHeight * MOBILE_DOCK_SWITCH_RATIO
      ? 'is-mobile-top'
      : 'is-mobile-bottom';

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= MOBILE_BREAKPOINT_PX);
    window.addEventListener('resize', onResize, { passive: true });
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  useEffect(() => {
    if (!step) return;
    const target = findTargetElement(resolveSelector(step, isMobile));
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: isMobile ? 'center' : 'nearest', inline: 'nearest' });
  }, [isMobile, step]);

  useEffect(() => {
    if (!step) return;
    const selector = resolveSelector(step, isMobile);
    let frame = 0;

    const update = () => {
      const target = findTargetElement(selector);
      if (!target) {
        setTargetFound(false);
        setSpotlight(null);
        if (!isMobile) setPanelStyle({});
        return;
      }
      const rect = target.getBoundingClientRect();
      const visible = rect.width > 2 && rect.height > 2;
      setTargetFound(visible);
      setSpotlight(visible ? buildSpotlightRect(rect) : null);
      if (!isMobile && visible) {
        setPanelStyle(computeDesktopPanelStyle(rect, resolvePlacement(step.placement || 'auto', rect)));
      } else if (!isMobile) {
        setPanelStyle({});
      }
    };

    const schedule = () => {
      if (frame) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(update);
    };

    schedule();
    window.addEventListener('scroll', schedule, true);
    window.addEventListener('resize', schedule);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule, true);
      window.removeEventListener('resize', schedule);
    };
  }, [isMobile, step]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key === 'ArrowRight' && !atEnd) {
        event.preventDefault();
        setStepIndex((c) => Math.min(totalSteps - 1, c + 1));
      }
      if (event.key === 'ArrowLeft' && !atStart) {
        event.preventDefault();
        setStepIndex((c) => Math.max(0, c - 1));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [atEnd, atStart, onClose, totalSteps]);

  function finish() {
    markTutorialSeen(scope);
    onClose();
  }

  function skipAllTutorials() {
    markAllTutorialsSeen();
    onClose();
  }

  const tips = step ? (isMobile ? step.tooltips.slice(0, 1) : step.tooltips.slice(0, 3)) : [];
  const effectivePanelStyle = !isMobile && targetFound ? panelStyle : undefined;

  return (
    <div className={cx('tut-root', isMobile && 'is-mobile')} role="dialog" aria-modal="true" aria-labelledby="tut-title">
      <button type="button" className="tut-backdrop" onClick={onClose} aria-label="Close tutorial" />
      {spotlight ? (
        <div
          className="tut-spotlight"
          style={{
            top: `${spotlight.top}px`,
            left: `${spotlight.left}px`,
            width: `${spotlight.width}px`,
            height: `${spotlight.height}px`,
          }}
        />
      ) : null}
      <article
        className={cx(
          'tut-panel',
          isMobile ? 'is-mobile' : 'is-desktop',
          isMobile && mobileDockClass,
          !isMobile && !targetFound && 'is-detached',
        )}
        style={effectivePanelStyle}
      >
        <button type="button" className="tut-close" onClick={onClose} aria-label="Close">
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
        </button>

        <header className="tut-head">
          <p className="tut-eyebrow">{blueprint.scope.replace('-', ' ')}</p>
          <h2 id="tut-title" className="tut-title">{blueprint.title}</h2>
        </header>

        <div className="tut-steps" role="progressbar" aria-valuemin={1} aria-valuemax={totalSteps} aria-valuenow={safeIndex + 1}>
          {Array.from({ length: totalSteps }).map((_, i) => (
            <button
              key={`tut-dot-${i}`}
              type="button"
              className={cx('tut-dot', i === safeIndex && 'is-active', i < safeIndex && 'is-done')}
              onClick={() => setStepIndex(i)}
              aria-label={`Go to step ${i + 1}`}
            />
          ))}
        </div>

        <TutorialRobotGuide step={step} stepNumber={safeIndex + 1} totalSteps={totalSteps} />

        <div className="tut-body">
          {step ? (
            <>
              <p className="tut-label">{step.system}</p>
              <h3 className="tut-step-title">{step.title}</h3>
              <p className="tut-step-desc">{step.description}</p>
              {tips.length > 0 ? (
                <ul className="tut-tips">
                  {tips.map((t, i) => (
                    <li key={`tut-tip-${i}`}>{t}</li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : (
            <p className="tut-step-desc">No steps available for this section.</p>
          )}
        </div>

        <footer className="tut-foot">
          <button
            type="button"
            className="tut-btn tut-btn-ghost"
            onClick={() => setStepIndex((c) => Math.max(0, c - 1))}
            disabled={atStart || !step}
          >
            Back
          </button>
          <span className="tut-counter">{safeIndex + 1} / {totalSteps}</span>
          <div className="tut-foot-actions">
            <button type="button" className="tut-skip-all" onClick={skipAllTutorials}>
              I've already seen this
            </button>
            {atEnd ? (
              <button type="button" className="tut-btn tut-btn-primary" onClick={finish}>
                Done
              </button>
            ) : (
              <button
                type="button"
                className="tut-btn tut-btn-primary"
                onClick={() => setStepIndex((c) => Math.min(totalSteps - 1, c + 1))}
                disabled={!step}
              >
                Next
              </button>
            )}
          </div>
        </footer>
      </article>
    </div>
  );
}
