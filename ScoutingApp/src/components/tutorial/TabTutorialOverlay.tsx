import { cx } from '../ui/primitives/cx';
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import type { TutorialScope } from '../../layout/userSettings';
import {
  getTutorialBlueprint,
  type TutorialWalkthroughStep,
} from '../../tutorial/tutorialBlueprints';
import { markTutorialSeen } from '../../tutorial/tutorialState';
import { RobotMascot } from '../ui/RobotMascot';
import { pickMobileDock, placeDesktopPanel } from './panelPlacement';
import './TabTutorialOverlay.css';

type SpotlightRect = { top: number; left: number; width: number; height: number };

type TabTutorialOverlayProps = {
  scope: TutorialScope;
  onClose: () => void;
};

const MOBILE_BREAKPOINT_PX = 900;
// Only until the panel has been measured once.
const DESKTOP_PANEL_FALLBACK_HEIGHT_PX = 320;



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

function isVisible(el: HTMLElement | null): boolean {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return r.width > 2 && r.height > 2;
}

// The steps that belong on this screen: the right layout, and an optional target actually shown.
function stepsFor(walkthrough: TutorialWalkthroughStep[], isMobile: boolean): TutorialWalkthroughStep[] {
  return walkthrough.filter((step) => {
    if (step.only === 'phone' && !isMobile) return false;
    if (step.only === 'desktop' && isMobile) return false;
    return !step.requiresTarget || isVisible(findTargetElement(resolveSelector(step, isMobile)));
  });
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
        <RobotMascot pose={pose} talking />
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
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= MOBILE_BREAKPOINT_PX);
  const [walkthrough, setWalkthrough] = useState(() =>
    blueprint.walkthrough.filter((step) => !step.requiresTarget && step.only !== (isMobile ? 'desktop' : 'phone')),
  );
  const [stepIndex, setStepIndex] = useState(0);
  const [spotlight, setSpotlight] = useState<SpotlightRect | null>(null);
  const [targetFound, setTargetFound] = useState(false);
  const [mobileDock, setMobileDock] = useState<'top' | 'bottom'>('bottom');
  const [panelStyle, setPanelStyle] = useState<CSSProperties>({});
  const panelRef = useRef<HTMLElement | null>(null);

  useLayoutEffect(() => {
    // Panels mounted beside the overlay are available after the DOM commits.
    setWalkthrough(stepsFor(blueprint.walkthrough, isMobile));
    setStepIndex(0);
  }, [blueprint, isMobile]);

  const totalSteps = walkthrough.length;
  const safeIndex = totalSteps ? Math.max(0, Math.min(stepIndex, totalSteps - 1)) : 0;
  const step = totalSteps ? walkthrough[safeIndex] : null;
  const atStart = safeIndex === 0;
  const atEnd = safeIndex >= totalSteps - 1;
  const mobileDockClass = mobileDock === 'top' ? 'is-mobile-top' : 'is-mobile-bottom';

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
      if (isMobile && visible) {
        const panelHeight = panelRef.current?.offsetHeight || 0;
        setMobileDock(pickMobileDock(buildSpotlightRect(rect), panelHeight, window.innerHeight));
      }
      if (!isMobile && visible) {
        const panelHeight = panelRef.current?.offsetHeight || DESKTOP_PANEL_FALLBACK_HEIGHT_PX;
        setPanelStyle(placeDesktopPanel(rect, step.placement || 'auto', panelHeight, { width: window.innerWidth, height: window.innerHeight }));
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
        ref={panelRef}
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
          <p className="tut-eyebrow">Page tour</p>
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
              {step.tip ? (
                <ul className="tut-tips">
                  <li>{step.tip}</li>
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
            <button type="button" className="tut-skip-all" onClick={finish}>
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
