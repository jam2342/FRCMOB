import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

function cx(...args: Array<string | false | null | undefined>): string {
  return args.filter(Boolean).join(' ');
}

type ActionOverflowItem = {
  label: string;
  to?: string;
  href?: string;
  onClick?: () => void;
  icon?: ReactNode;
};

type ActionOverflowMenuProps = {
  label?: string;
  items: ActionOverflowItem[];
  className?: string;
  align?: 'left' | 'right';
};

export function ActionOverflowMenu({
  label = 'More',
  items,
  className,
  align = 'right',
}: ActionOverflowMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (rootRef.current?.contains(target)) return;
      setOpen(false);
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };

    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  if (items.length === 0) return null;

  return (
    <div
      ref={rootRef}
      className={cx('action-overflow', align === 'left' ? 'align-left' : 'align-right', className)}
    >
      <button
        type="button"
        className={cx('center-btn', 'ghost', 'action-overflow-toggle', open && 'active')}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        {label}
        <span className="action-overflow-caret" aria-hidden="true">v</span>
      </button>

      {open ? (
        <div className="action-overflow-menu" role="menu" aria-label={`${label} menu`}>
          {items.map((item) => {
            const key = `${item.label}-${item.to || item.href || 'action'}`;

            if (item.to) {
              return (
                <Link
                  key={key}
                  className="action-overflow-item"
                  to={item.to}
                  role="menuitem"
                  onClick={() => setOpen(false)}
                >
                  {item.icon} {item.label}
                </Link>
              );
            }

            if (item.href) {
              return (
                <a
                  key={key}
                  className="action-overflow-item"
                  href={item.href}
                  role="menuitem"
                  onClick={() => setOpen(false)}
                >
                  {item.icon} {item.label}
                </a>
              );
            }

            return (
              <button
                key={key}
                type="button"
                className="action-overflow-item"
                role="menuitem"
                onClick={() => {
                  item.onClick?.();
                  setOpen(false);
                }}
              >
                {item.icon} {item.label}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
