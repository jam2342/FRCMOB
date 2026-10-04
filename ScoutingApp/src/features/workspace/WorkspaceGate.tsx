import { Fragment, type ReactNode } from 'react';
import { EmptyState } from '../../components/ui/EmptyState';
import { UsersIcon } from '../../components/ui/Icons';
import { Button } from '../../components/ui/primitives';
import { useWorkspace } from './useWorkspace';
import { workspaceEndReason } from './workspaceSession';
import './WorkspaceGate.css';

// Team-only tools (picklists, pit notes, rooms) render through this. Without a
// workspace the page explains why instead of failing on a 401.
export function WorkspaceGate({
  feature,
  viewBar,
  children,
}: {
  feature: string;
  // The page's own tab bar, so the gated screen can still navigate sideways.
  viewBar?: ReactNode;
  children: ReactNode;
}) {
  const session = useWorkspace();
  // Keyed by workspace: switching teams remounts the tools, so nothing a page
  // loaded or cached for the previous team survives into the next.
  if (session) return <Fragment key={session.workspace.id}>{children}</Fragment>;
  const revoked = workspaceEndReason() === 'revoked';
  return (
    <>
      {viewBar}
      <div className="workspace-gate">
        <EmptyState
          icon={<UsersIcon />}
          title={revoked ? 'You were signed out of your team' : `${feature} are private to your team`}
          description={
            revoked
              ? 'Your access to the team workspace ended. Ask your team lead for the current code to rejoin.'
              : "Create your team's workspace, or join one with the code your team lead shares. Everything you add stays visible only to your team."
          }
          action={
            <Button as="a" href="#/my-team" variant="primary">
              {revoked ? 'Rejoin your team' : 'Set up your team'}
            </Button>
          }
        />
      </div>
    </>
  );
}
