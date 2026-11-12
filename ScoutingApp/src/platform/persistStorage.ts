// Browsers may clear a site's storage under pressure, and Safari clears it for a site
// not opened in seven days. That would drop work still waiting for signal (queued edits,
// pit photos, recordings), so the first time something is kept for later, ask the
// browser to keep this site's storage. Asked then, not at start, because Firefox shows
// a prompt for it. A refusal changes nothing: it only means the usual rules apply.
let asked = false;

export function keepStorageForQueuedWork(): void {
  if (asked || typeof navigator === 'undefined') return;
  asked = true;
  const storage = navigator.storage;
  if (!storage?.persist || !storage.persisted) return;
  void storage.persisted()
    .then((already) => (already ? true : storage.persist()))
    .catch(() => false);
}

export function resetPersistStorageForTests(): void {
  asked = false;
}
