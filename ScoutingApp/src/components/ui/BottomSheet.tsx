import { useCallback, useEffect, useRef, useState } from 'react';
import './BottomSheet.css';

interface BottomSheetProps {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  title?: string;
  /** Snap points as percentages of viewport height (e.g. [40, 80]) */
  snapPoints?: number[];
  className?: string;
}

export function BottomSheet({
  open,
  onClose,
  children,
  title,
  snapPoints = [50],
  className,
}: BottomSheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const [currentSnap, setCurrentSnap] = useState(0);
  const [dragOffset, setDragOffset] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const dragging = useRef(false);
  const startY = useRef(0);
  const sheetHeight = useRef(0);

  // Lock body scroll when open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
      const resetTimer = window.setTimeout(() => {
        setCurrentSnap(0);
        setDragOffset(0);
        setIsDragging(false);
      }, 0);
      return () => {
        window.clearTimeout(resetTimer);
        document.body.style.overflow = '';
      };
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onClose]);

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    dragging.current = true;
    setIsDragging(true);
    startY.current = e.touches[0].clientY;
    const sheet = sheetRef.current;
    if (sheet) {
      sheetHeight.current = sheet.offsetHeight;
    }
  }, []);

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragging.current) return;
    const deltaY = e.touches[0].clientY - startY.current;
    // Only allow dragging down (positive delta) or up with rubber band
    setDragOffset(Math.max(deltaY, -30));
  }, []);

  const handleTouchEnd = useCallback(() => {
    if (!dragging.current) return;
    dragging.current = false;
    setIsDragging(false);

    if (dragOffset > 100) {
      // Dragged down enough → close or go to lower snap
      if (currentSnap === 0) {
        onClose();
      } else {
        setCurrentSnap((s) => Math.max(0, s - 1));
      }
    } else if (dragOffset < -40 && currentSnap < snapPoints.length - 1) {
      // Dragged up → go to higher snap
      setCurrentSnap((s) => Math.min(snapPoints.length - 1, s + 1));
    }
    setDragOffset(0);
  }, [dragOffset, currentSnap, snapPoints, onClose]);

  if (!open) return null;

  const snapHeight = snapPoints[currentSnap] ?? 50;

  return (
    <div className="bs__backdrop" onClick={onClose}>
      <div
        ref={sheetRef}
        className={`bs ${className || ''}`}
        style={{
          height: `${snapHeight}vh`,
          transform: dragOffset ? `translateY(${dragOffset}px)` : undefined,
          transition: isDragging ? 'none' : undefined,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="bs__handle-area"
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
        >
          <div className="bs__handle" />
        </div>
        {title && (
          <div className="bs__header">
            <h3 className="bs__title">{title}</h3>
            <button className="bs__close" onClick={onClose} aria-label="Close">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        )}
        <div className="bs__content">{children}</div>
      </div>
    </div>
  );
}
