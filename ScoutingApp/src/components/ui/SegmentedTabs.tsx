import { type KeyboardEvent, type ReactNode } from 'react';

function cx(...args: (string | false | null | undefined | 0)[]): string {
  return args.filter(Boolean).join(' ');
}

function nextEnabledIndex<T extends string>(
  items: readonly SegmentedTabItem<T>[],
  currentIndex: number,
  direction: 1 | -1,
): number {
  for (let step = 1; step <= items.length; step += 1) {
    const nextIndex = (currentIndex + direction * step + items.length) % items.length;
    if (!items[nextIndex].disabled) return nextIndex;
  }
  return currentIndex;
}

function edgeEnabledIndex<T extends string>(
  items: readonly SegmentedTabItem<T>[],
  direction: 1 | -1,
): number {
  const indexes = items.map((_, index) => index);
  const ordered = direction === 1 ? indexes : indexes.reverse();
  return ordered.find((index) => !items[index].disabled) ?? 0;
}

type SegmentedTabItem<T extends string> = {
  value: T;
  label: string;
  icon?: ReactNode;
  disabled?: boolean;
  title?: string;
  className?: string;
  panelId?: string;
};

type SegmentedTabsProps<T extends string> = {
  items: readonly SegmentedTabItem<T>[];
  value: T;
  onChange: (next: T) => void;
  ariaLabel: string;
  className?: string;
  itemClassName?: string;
};

export function SegmentedTabs<T extends string>({
  items,
  value,
  onChange,
  ariaLabel,
  className = '',
  itemClassName = '',
}: SegmentedTabsProps<T>) {
  if (items.length === 0) return null;

  const focusTab = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const buttons = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    window.requestAnimationFrame(() => buttons?.[index]?.focus());
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = nextEnabledIndex(items, index, 1);
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = nextEnabledIndex(items, index, -1);
    } else if (event.key === 'Home') {
      nextIndex = edgeEnabledIndex(items, 1);
    } else if (event.key === 'End') {
      nextIndex = edgeEnabledIndex(items, -1);
    }
    if (nextIndex == null || nextIndex === index) return;
    event.preventDefault();
    onChange(items[nextIndex].value);
    focusTab(event, nextIndex);
  };

  return (
    <div className={cx('segmented-tabs', className)} role="tablist" aria-label={ariaLabel} aria-orientation="horizontal">
      {items.map((item, index) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={active}
            aria-controls={item.panelId}
            tabIndex={active ? 0 : -1}
            className={cx('segmented-tabs__item', itemClassName, item.className, active && 'active')}
            onClick={() => onChange(item.value)}
            onKeyDown={(event) => handleKeyDown(event, index)}
            disabled={item.disabled}
            title={item.title}
          >
            {item.icon ? <span className="segmented-tabs__icon">{item.icon}</span> : null}
            <span className="segmented-tabs__label">{item.label}</span>
          </button>
        );
      })}
    </div>
  );
}
