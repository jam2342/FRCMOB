import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import {
  createWorkspace,
  getMyWorkspace,
  joinWorkspace,
  leaveWorkspace,
  removeWorkspaceMember,
  renameMeInWorkspace,
  rotateWorkspaceJoinCode,
  setWorkspaceMemberRole,
  updateMyWorkspace,
  type WorkspaceMemberRecord,
  type WorkspaceStateResponse,
} from '../api';
import { OfflineReadyCard } from '../components/ui/OfflineReadyCard';
import { SyncStatusCard } from '../features/offline/SyncStatusCard';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { Button, Chip, FieldCheckbox, FieldText, Modal } from '../components/ui/primitives';
import { TeamScoutingCard } from '../features/workspace/TeamScoutingCard';
import { useWorkspace } from '../features/workspace/useWorkspace';
import { issuedJoinCodeFor, workspaceEndReason } from '../features/workspace/workspaceSession';
import { hapticSuccess } from '../utils/haptics';
import './MyTeamPage.css';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong. Try again.';
}

function parseTeamNumber(raw: string): number | null {
  const value = raw.trim();
  if (!value) return null;
  return /^\d{1,5}$/.test(value) && Number(value) > 0 ? Number(value) : NaN;
}

function lastSeen(iso: string | null): string {
  if (!iso) return 'not seen yet';
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (Number.isNaN(minutes)) return 'not seen yet';
  if (minutes < 2) return 'active now';
  if (minutes < 60) return `seen ${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `seen ${hours}h ago`;
  return `seen ${Math.round(hours / 24)}d ago`;
}

function SetUpTeam() {
  const revoked = workspaceEndReason() === 'revoked';
  const [joinCode, setJoinCode] = useState('');
  const [joinName, setJoinName] = useState('');
  const [workspaceName, setWorkspaceName] = useState('');
  const [teamNumber, setTeamNumber] = useState('');
  const [creatorName, setCreatorName] = useState('');
  const [busy, setBusy] = useState<'join' | 'create' | null>(null);
  const [error, setError] = useState<{ form: 'join' | 'create'; text: string } | null>(null);

  const submitJoin = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!joinName.trim()) { setError({form:'join',text:'Enter your name so your team knows who joined.'}); return; }
    const code = joinCode.toUpperCase().replace(/[OIL]/g, char => char === 'O' ? '0' : '1').replace(/[^A-Z0-9]/g, '');
    if (!/^[0-9A-HJKMNP-TV-Z]{10}$/.test(code)) { setError({form:'join',text:'Enter the 10-character code from your team lead. Spaces and the dash are optional.'}); return; }
    setBusy('join');
    setError(null);
    try {
      await joinWorkspace({ join_code: code, display_name: joinName.trim() });
      hapticSuccess();
    } catch (err) {
      setError({ form: 'join', text: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  };

  const submitCreate = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!workspaceName.trim() || !creatorName.trim()) { setError({form:'create',text:'Enter a workspace name and your name.'}); return; }
    const number = parseTeamNumber(teamNumber);
    if (Number.isNaN(number)) { setError({form:'create',text:'Enter a valid team number using digits only, from 1 to 99999, or leave it blank.'}); return; }
    setBusy('create');
    setError(null);
    try {
      await createWorkspace({
        name: workspaceName.trim(),
        frc_team_number: number,
        display_name: creatorName.trim(),
      });
      hapticSuccess();
    } catch (err) {
      setError({ form: 'create', text: errorMessage(err) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <SurfaceCard
        title="My Team"
        subtitle="A private space for your team's picklists, pit notes and scouting rooms. Events, ratings and predictions stay public for everyone."
        expandable={false}
        mobileCollapsible={false}
      >
        {revoked ? (
          <p className="center-callout warning">
            Your access to your team's workspace ended. Ask your team lead for the current code to rejoin.
          </p>
        ) : null}
        <p className="my-team__lede">
          One person creates the workspace and shares its code. Everyone else joins with that code and their
          name. No accounts or passwords.
        </p>
      </SurfaceCard>

      <div className="my-team__setup-grid">
        <SurfaceCard title="Join your team" subtitle="Got a code from your team lead?" expandable={false} mobileCollapsible={false}>
          <form className="my-team__form" onSubmit={submitJoin}>
            <FieldText
              label="Join code"
              placeholder="ABCDE-12345"
              value={joinCode}
              onChange={(event) => setJoinCode(event.target.value)}
              autoCapitalize="characters"
              autoComplete="off"
              spellCheck={false}
              required
            />
            <FieldText
              label="Your name"
              hint="Shown to your team on assignments and entries."
              value={joinName}
              onChange={(event) => setJoinName(event.target.value)}
              autoComplete="nickname"
              maxLength={40}
              required
            />
            {error?.form === 'join' ? <p className="center-callout warning" role="alert">{error.text}</p> : null}
            <Button type="submit" variant="primary" fullWidth loading={busy === 'join'} disabled={busy !== null}>
              Join team
            </Button>
          </form>
        </SurfaceCard>

        <SurfaceCard title="Create a workspace" subtitle="Starting fresh for your team?" expandable={false} mobileCollapsible={false}>
          <form className="my-team__form" onSubmit={submitCreate}>
            <FieldText
              label="Workspace name"
              placeholder="Robonauts Scouting"
              value={workspaceName}
              onChange={(event) => setWorkspaceName(event.target.value)}
              maxLength={80}
              required
            />
            <FieldText
              label="FRC team number"
              hint="Optional."
              inputMode="numeric"
              value={teamNumber}
              onChange={(event) => setTeamNumber(event.target.value)}
              maxLength={5}
            />
            <FieldText
              label="Your name"
              value={creatorName}
              onChange={(event) => setCreatorName(event.target.value)}
              autoComplete="nickname"
              maxLength={40}
              required
            />
            {error?.form === 'create' ? <p className="center-callout warning" role="alert">{error.text}</p> : null}
            <Button type="submit" variant="primary" fullWidth loading={busy === 'create'} disabled={busy !== null}>
              Create workspace
            </Button>
          </form>
        </SurfaceCard>
      </div>
    </>
  );
}

function JoinCodePanel({ code, isLeader, onRotate, busy }: {
  code: string | null;
  isLeader: boolean;
  onRotate: () => void;
  busy: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  if (code) {
    return (
      <div className="my-team__code-block">
        <p className="my-team__code" aria-label={`Join code ${code.split('').join(' ')}`}>{code}</p>
        <div className="my-team__code-actions">
          <Button variant="primary" onClick={copy}>{copied ? 'Copied' : 'Copy code'}</Button>
        </div>
        <p className="my-team__note">
          Share it with your scouts now. It's only shown once; if you lose it, make a new one. People already in the
          workspace stay in.
        </p>
      </div>
    );
  }
  if (!isLeader) {
    return <p className="my-team__note">Ask a team leader for the code to invite someone.</p>;
  }
  return (
    <div className="my-team__code-block">
      <p className="my-team__note">
        Codes are shown only when they're made, so the old one isn't visible here. Make a new code to invite people;
        the old code stops working and everyone already in stays in.
      </p>
      <Button onClick={onRotate} loading={busy} disabled={busy}>Make a new code</Button>
    </div>
  );
}

function TeamWorkspaceView() {
  const session = useWorkspace();
  const [state, setState] = useState<WorkspaceStateResponse | null>(null);
  const [freshCode, setFreshCode] = useState<string | null>(() =>
    session ? issuedJoinCodeFor(session.workspace.id) : null,
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState('');
  const [editingWorkspace, setEditingWorkspace] = useState(false);
  const [workspaceName, setWorkspaceName] = useState('');
  const [teamNumber, setTeamNumber] = useState('');
  const [removing, setRemoving] = useState<WorkspaceMemberRecord | null>(null);
  const [rotateOnRemove, setRotateOnRemove] = useState(true);
  const [leaving, setLeaving] = useState<'confirm' | 'last-member' | null>(null);
  const refreshInFlightRef = useRef<Promise<void> | null>(null);

  const refresh = useCallback(() => {
    if (refreshInFlightRef.current) return refreshInFlightRef.current;
    const request = (async () => {
      try {
        setState(await getMyWorkspace());
        setError(null);
      } catch (err) {
        setError(errorMessage(err));
      }
    })();
    refreshInFlightRef.current = request;
    void request.finally(() => {
      if (refreshInFlightRef.current === request) refreshInFlightRef.current = null;
    });
    return request;
  }, []);

  useEffect(() => {
    void refresh();
    // Leaders watch scouts join during a pit-side briefing, so keep the list live
    // while the page is visible, and catch up when the tab comes back.
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh();
    }, 20_000);
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [refresh]);

  const run = async (label: string, action: () => Promise<WorkspaceStateResponse | void>) => {
    setBusy(label);
    setError(null);
    try {
      const result = await action();
      if (result) {
        setState(result);
        if (result.join_code) setFreshCode(result.join_code);
      }
      return true;
    } catch (err) {
      setError(errorMessage(err));
      return false;
    } finally {
      setBusy(null);
    }
  };

  if (!session) return null;
  const me = state?.me;
  const isLeader = (me?.role ?? session.me.role) === 'leader';
  const members = state?.members ?? [];
  const workspace = state?.workspace ?? session.workspace;

  const leave = async (confirmLast: boolean) => {
    setBusy('leave');
    setError(null);
    try {
      await leaveWorkspace(confirmLast);
      setLeaving(null);
    } catch (err) {
      const text = errorMessage(err);
      if (!confirmLast && /last member/i.test(text)) {
        setLeaving('last-member');
      } else {
        setLeaving(null);
        setError(text);
      }
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <SurfaceCard
        title={workspace.name}
        subtitle={
          workspace.frc_team_number
            ? `Team ${workspace.frc_team_number} · private workspace`
            : 'Private team workspace'
        }
        expandable={false}
        mobileCollapsible={false}
      >
        {error ? <p className="center-callout warning">{error}</p> : null}
        <p className="my-team__lede">
          Picklists, pit scouting, scouting rooms and coverage you use while signed in here are visible only to
          this workspace.
        </p>
        {renaming ? (
          <form
            className="my-team__inline-form"
            onSubmit={async (event) => {
              event.preventDefault();
              if (await run('rename', () => renameMeInWorkspace(newName))) setRenaming(false);
            }}
          >
            <FieldText label="Your name" value={newName} onChange={(event) => setNewName(event.target.value)} maxLength={40} required />
            <div className="my-team__row-actions">
              <Button type="submit" variant="primary" loading={busy === 'rename'}>Save</Button>
              <Button variant="quiet" onClick={() => setRenaming(false)}>Cancel</Button>
            </div>
          </form>
        ) : (
          <div className="my-team__identity">
            <span className="my-team__you-are">
              You're <strong>{me?.display_name ?? session.me.display_name}</strong>
              <Chip size="sm" tone={isLeader ? 'accent' : 'neutral'}>{isLeader ? 'Leader' : 'Member'}</Chip>
            </span>
            <Button size="sm" variant="quiet" onClick={() => { setNewName(me?.display_name ?? session.me.display_name); setRenaming(true); }}>
              Change your name
            </Button>
          </div>
        )}
        {isLeader && editingWorkspace ? (
          <form
            className="my-team__inline-form"
            onSubmit={async (event) => {
              event.preventDefault();
              const number = parseTeamNumber(teamNumber);
              if (Number.isNaN(number)) { setError('Enter a valid team number using digits only, from 1 to 99999, or leave it blank.'); return; }
              if (!workspaceName.trim()) { setError('Enter a workspace name.'); return; }
              const ok = await run('workspace', () =>
                updateMyWorkspace({
                  name: workspaceName,
                  frc_team_number: number,
                  clear_team_number: number === null,
                }),
              );
              if (ok) setEditingWorkspace(false);
            }}
          >
            <FieldText label="Workspace name" value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} maxLength={80} required />
            <FieldText label="FRC team number" hint="Leave empty for none." inputMode="numeric" value={teamNumber} onChange={(event) => setTeamNumber(event.target.value)} maxLength={5} />
            <div className="my-team__row-actions">
              <Button type="submit" variant="primary" loading={busy === 'workspace'}>Save</Button>
              <Button variant="quiet" onClick={() => setEditingWorkspace(false)}>Cancel</Button>
            </div>
          </form>
        ) : isLeader ? (
          <Button
            size="sm"
            variant="quiet"
            onClick={() => {
              setWorkspaceName(workspace.name);
              setTeamNumber(workspace.frc_team_number ? String(workspace.frc_team_number) : '');
              setEditingWorkspace(true);
            }}
          >
            Edit workspace
          </Button>
        ) : null}
      </SurfaceCard>

      <TeamScoutingCard />

      <SurfaceCard title="Invite scouts" subtitle="Anyone with the code can join this workspace." expandable={false} mobileCollapsible={false}>
        <JoinCodePanel
          code={freshCode}
          isLeader={isLeader}
          busy={busy === 'rotate'}
          onRotate={() => void run('rotate', rotateWorkspaceJoinCode)}
        />
      </SurfaceCard>

      <SurfaceCard
        title={state ? `Members (${members.length})` : 'Members'}
        subtitle={isLeader ? 'Leaders can promote, demote and remove members.' : undefined}
        expandable={false}
        mobileCollapsible={false}
      >
        {!state && !error ? <p className="my-team__note">Loading members…</p> : null}
        <ul className="my-team__members">
          {members.map((member) => {
            const self = member.id === me?.id;
            return (
              <li key={member.id} className="my-team__member">
                <div className="my-team__member-main">
                  <span className="my-team__member-name">
                    {member.display_name}
                    {self ? <span className="my-team__you"> (you)</span> : null}
                  </span>
                  <span className="my-team__member-meta">{lastSeen(member.last_seen_at)}</span>
                </div>
                <Chip size="sm" tone={member.role === 'leader' ? 'accent' : 'neutral'}>
                  {member.role === 'leader' ? 'Leader' : 'Member'}
                </Chip>
                {isLeader && !self ? (
                  <div className="my-team__member-actions">
                    <Button
                      size="sm"
                      variant="quiet"
                      loading={busy === `role-${member.id}`}
                      onClick={() =>
                        void run(`role-${member.id}`, () =>
                          setWorkspaceMemberRole(member.id, member.role === 'leader' ? 'member' : 'leader'),
                        )
                      }
                    >
                      {member.role === 'leader' ? 'Make member' : 'Make leader'}
                    </Button>
                    <Button size="sm" variant="danger" onClick={() => { setRotateOnRemove(true); setRemoving(member); }}>
                      Remove
                    </Button>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      </SurfaceCard>

      <SurfaceCard title="Leave workspace" expandable={false} mobileCollapsible={false}>
        <p className="my-team__note">
          This device stops seeing your team's picklists, pit notes and rooms. You can rejoin with a current code.
        </p>
        <Button variant="danger" onClick={() => setLeaving('confirm')}>Leave workspace</Button>
      </SurfaceCard>

      <Modal
        open={removing !== null}
        onClose={() => setRemoving(null)}
        title={`Remove ${removing?.display_name ?? ''}?`}
        dismissible={busy !== 'remove'}
        footer={
          <>
            <Button variant="quiet" onClick={() => setRemoving(null)} disabled={busy === 'remove'}>Cancel</Button>
            <Button
              variant="danger"
              loading={busy === 'remove'}
              onClick={async () => {
                if (!removing) return;
                if (await run('remove', () => removeWorkspaceMember(removing.id, rotateOnRemove))) setRemoving(null);
              }}
            >
              Remove
            </Button>
          </>
        }
      >
        <p>They lose access to this workspace on every device right away, including any open scouting room.</p>
        <FieldCheckbox
          label="Also make a new join code, so they can't rejoin with the old one"
          checked={rotateOnRemove}
          onChange={(event) => setRotateOnRemove(event.target.checked)}
        />
      </Modal>

      <Modal
        open={leaving !== null}
        onClose={() => setLeaving(null)}
        title={leaving === 'last-member' ? "You're the last member" : `Leave ${workspace.name}?`}
        dismissible={busy !== 'leave'}
        footer={
          <>
            <Button variant="quiet" onClick={() => setLeaving(null)} disabled={busy === 'leave'}>Stay</Button>
            <Button variant="danger" loading={busy === 'leave'} onClick={() => void leave(leaving === 'last-member')}>
              {leaving === 'last-member' ? 'Leave and lock it' : 'Leave'}
            </Button>
          </>
        }
      >
        {leaving === 'last-member' ? (
          <p>Nobody will be able to open this workspace's picklists, pit notes or rooms after you leave.</p>
        ) : (
          <p>You'll need a current join code to come back.</p>
        )}
      </Modal>
    </>
  );
}

export function MyTeamPage() {
  const session = useWorkspace();
  return (
    <div className="center-page-container my-team">

        {session ? <TeamWorkspaceView key={session.workspace.id} /> : <SetUpTeam />}
        <OfflineReadyCard />
        <SyncStatusCard />

    </div>
  );
}
