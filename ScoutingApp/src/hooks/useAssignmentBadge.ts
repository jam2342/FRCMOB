import { useEffect } from 'react';
import type { MyAssignment } from '../features/workspace/teamAssignments';
import { setAppBadgeCount } from '../utils/appBadge';

// Wherever this scout's assignments are known, the icon badge shows how many are left. Null
// (still loading, or no room) leaves the badge as it was rather than flashing it to zero.
export function useAssignmentBadge(assignments: readonly MyAssignment[] | null): void {
  const left = assignments ? assignments.filter((item) => !item.done).length : null;
  useEffect(() => {
    if (left !== null) setAppBadgeCount(left);
  }, [left]);
}
