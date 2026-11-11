import { useCallback, useEffect, useMemo, useState } from 'react';
import { listQueuedPitPhotos } from '../offline/pitPhotoQueue';
import type { QueuedPitPhoto } from '../offline/pitPhotoQueue';

export type WaitingPhoto = { id: string; teamKey: string; url: string; createdAt: number; attempts: number };

// Photos still on this phone for one workspace's event, kept in step with the queue as it uploads.
export function usePitPhotoQueue(workspaceId: number, eventKey: string) {
  const [rows, setRows] = useState<QueuedPitPhoto[]>([]);

  const refresh = useCallback(async () => {
    const all = await listQueuedPitPhotos();
    setRows(all.filter((row) => row.workspaceId === workspaceId && row.eventKey === eventKey));
  }, [workspaceId, eventKey]);

  useEffect(() => {
    void refresh();
    const onChange = () => { void refresh(); };
    window.addEventListener('pitphotos:change', onChange);
    return () => window.removeEventListener('pitphotos:change', onChange);
  }, [refresh]);

  // One object URL per waiting photo, released when it uploads or the page closes.
  const waiting = useMemo<WaitingPhoto[]>(
    () => rows.map((row) => ({
      id: row.id,
      teamKey: row.teamKey,
      url: URL.createObjectURL(new Blob([row.data], { type: row.type })),
      createdAt: row.createdAt,
      attempts: row.attempts,
    })),
    [rows],
  );
  useEffect(() => () => { waiting.forEach((photo) => URL.revokeObjectURL(photo.url)); }, [waiting]);

  return { waiting, refresh };
}
