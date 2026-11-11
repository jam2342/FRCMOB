import { useEffect, useState } from 'react';
import { getMyWorkspace } from '../../api';
import type { WorkspaceMemberRecord } from '../../api';

export type Scout = Pick<WorkspaceMemberRecord, 'id' | 'display_name'>;

// Only leaders open these sheets, so the member list is fetched the first time one opens.
export function usePitScouts(wanted: boolean): { scouts: Scout[]; failed: boolean } {
  const [scouts, setScouts] = useState<Scout[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!wanted || scouts !== null) return undefined;
    let cancelled = false;
    getMyWorkspace().then(
      (state) => { if (!cancelled) { setScouts(state.members.map(({ id, display_name }) => ({ id, display_name }))); setFailed(false); } },
      () => { if (!cancelled) setFailed(true); },
    );
    return () => { cancelled = true; };
  }, [wanted, scouts]);
  return { scouts: scouts ?? [], failed };
}
