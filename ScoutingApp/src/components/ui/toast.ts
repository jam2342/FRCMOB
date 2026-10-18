// One toast at a time, shown by ToastHost (mounted once in the shell). Anything can show one
// without a provider: a page, a hook, the offline queue.
//
// Undo is the reason this exists. A toast that offers an action reports how it closed, so the
// caller can hold back the irreversible part (a delete, a sync) until the toast is gone and only
// skip it when the action was taken. A toast replaced by a newer one closes as 'replaced', which
// is not the action: the held-back work still runs.

export type ToastCloseReason = 'timeout' | 'action' | 'dismiss' | 'replaced';
export type ToastTone = 'success' | 'info' | 'warning';

export type ToastOptions = {
  message: string;
  tone?: ToastTone;
  actionLabel?: string;
  onAction?: () => void;
  onClose?: (reason: ToastCloseReason) => void;
  durationMs?: number;
};

export type ToastRecord = Required<Pick<ToastOptions, 'message' | 'tone' | 'durationMs'>> &
  Pick<ToastOptions, 'actionLabel' | 'onAction' | 'onClose'> & { id: number };

const DEFAULT_TOAST_MS = 5000;

type Listener = (toast: ToastRecord | null) => void;

let current: ToastRecord | null = null;
let nextId = 1;
const listeners = new Set<Listener>();
// The countdown lives here, not in the host, so a toast closes (and releases any held-back work)
// even where no host is mounted.
let timer: ReturnType<typeof setTimeout> | null = null;
let remainingMs = 0;
let startedAtMs = 0;
let paused = false;

function clearTimer() {
  if (timer !== null) clearTimeout(timer);
  timer = null;
}

function startTimer() {
  clearTimer();
  const id = current?.id;
  startedAtMs = Date.now();
  timer = setTimeout(() => {
    if (id !== undefined) closeToast(id, 'timeout');
  }, remainingMs);
}

function emit() {
  for (const listener of listeners) listener(current);
}

function close(reason: ToastCloseReason) {
  const closing = current;
  if (!closing) return;
  clearTimer();
  paused = false;
  current = null;
  emit();
  try {
    closing.onClose?.(reason);
  } catch {
    // A caller's cleanup failing must not take the next toast down with it.
  }
}

export function showToast(options: ToastOptions): number {
  if (current) close('replaced');
  current = {
    id: nextId++,
    message: options.message,
    tone: options.tone ?? 'success',
    durationMs: options.durationMs ?? DEFAULT_TOAST_MS,
    actionLabel: options.actionLabel,
    onAction: options.onAction,
    onClose: options.onClose,
  };
  remainingMs = current.durationMs;
  paused = false;
  startTimer();
  emit();
  return current.id;
}

// Closes the toast only if it is still the one with this id, so a stale timer or an unmounting
// page can't close a newer toast.
export function closeToast(id: number, reason: ToastCloseReason = 'dismiss'): void {
  if (!current || current.id !== id) return;
  close(reason);
}

export function runToastAction(id: number): void {
  if (!current || current.id !== id) return;
  const { onAction } = current;
  close('action');
  onAction?.();
}

// Hover or focus on the toast holds the countdown, so Undo can't vanish under the pointer.
export function pauseToast(id: number): void {
  if (!current || current.id !== id || paused) return;
  paused = true;
  clearTimer();
  remainingMs = Math.max(0, remainingMs - (Date.now() - startedAtMs));
}

export function resumeToast(id: number): void {
  if (!current || current.id !== id || !paused) return;
  paused = false;
  startTimer();
}

export function currentToast(): ToastRecord | null {
  return current;
}

export function subscribeToasts(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
