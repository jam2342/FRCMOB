import QRCode from 'qrcode';
import { useSearchParams } from 'react-router-dom';
import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { copyTextToClipboard } from './centerUtils';
import {
  createWorkspace,
  getMyWorkspace,
  getWorkspaceSignupPolicy,
  joinWorkspace,
  leaveWorkspace,
  removeWorkspaceMember,
  renameMeInWorkspace,
  rotateWorkspaceJoinCode,
  setWorkspaceMemberRole,
  updateMyWorkspace,
  type WorkspaceMemberRecord,
  type WorkspaceSignupPolicy,
  type WorkspaceStateResponse,
} from '../api';
import { OfflineReadyCard } from '../components/ui/OfflineReadyCard';
import { SyncStatusCard } from '../features/offline/SyncStatusCard';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { Button, Chip, FieldCheckbox, FieldText, Modal } from '../components/ui/primitives';
import { MemberSheet } from '../features/workspace/MemberSheet';
import { TeamScoutingCard } from '../features/workspace/TeamScoutingCard';
import { SeasonStoryCard } from '../features/seasonStory/SeasonStoryCard';
import { useWorkspace } from '../features/workspace/useWorkspace';
import { issuedJoinCodeFor, workspaceEndReason } from '../features/workspace/workspaceSession';
import { TurnstileChallenge } from '../features/workspace/TurnstileChallenge';
import { isNativeApp, shareableAppUrl } from '../platform/runtime';
import { hapticSuccess } from '../utils/haptics';
import { useMobileLayout } from '../hooks/useMobileLayout';
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
  const isMobileLayout = useMobileLayout();
  const [showCreate, setShowCreate] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  // A shared invite link carries the code; it fills the field but never submits. The code is
  // dropped from the address right away so it doesn't linger in history or a screenshot.
  const [joinCode, setJoinCode] = useState(() => (searchParams.get('code') ?? '').slice(0, 24));
  const [joinName, setJoinName] = useState('');
  const [workspaceName, setWorkspaceName] = useState('');
  const [teamNumber, setTeamNumber] = useState('');
  const [creatorName, setCreatorName] = useState('');
  const [busy, setBusy] = useState<'join' | 'create' | null>(null);
  const [error, setError] = useState<{ form: 'join' | 'create'; text: string } | null>(null);
  const [signupPolicy, setSignupPolicy] = useState<WorkspaceSignupPolicy | null>(null);
  const [signupUnavailable, setSignupUnavailable] = useState(false);
  const [challengeToken, setChallengeToken] = useState<string | null>(null);
  const [challengeAttempt, setChallengeAttempt] = useState(0);
  const nativeChallenge = isNativeApp() && signupPolicy?.required;
  const canCreate = Boolean(signupPolicy && (!signupPolicy.required || (signupPolicy.site_key && challengeToken && !nativeChallenge)));

  useEffect(() => {
    if (!searchParams.has('code')) return;
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('code');
      return next;
    }, { replace: true });
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    let active = true;
    getWorkspaceSignupPolicy().then((policy) => {
      if (active) setSignupPolicy(policy);
    }).catch(() => {
      if (active) setSignupUnavailable(true);
    });
    return () => { active = false; };
  }, []);

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
    if (!canCreate) return;
    setBusy('create');
    setError(null);
    try {
      await createWorkspace({
        name: workspaceName.trim(),
        frc_team_number: number,
        display_name: creatorName.trim(),
        challenge_token: signupPolicy?.required ? challengeToken : null,
      });
      hapticSuccess();
    } catch (err) {
      setError({ form: 'create', text: errorMessage(err) });
    } finally {
      // Challenge tokens are single-use, including when the request fails.
      setChallengeToken(null);
      setChallengeAttempt((attempt) => attempt + 1);
      setBusy(null);
    }
  };

  return (
    <>
      <SurfaceCard tourId="myteam-intro"
        title="My Team"
        subtitle="A private space for your team's picklists, pit notes and scouting rooms. Events, ratings and predictions stay public for everyone."
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
        <SurfaceCard tourId="myteam-join" title="Join your team" subtitle="Got a code from your team lead?" mobileCollapsible={false}>
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

        {/* Most people join a team someone else made. On a phone the second form, three more
            boxes and a security check, waits behind one tap. */}
        {isMobileLayout && !showCreate && error?.form !== 'create' ? (
          <p className="my-team__create-link">
            Starting fresh for your team?{' '}
            <Button variant="quiet" size="sm" onClick={() => setShowCreate(true)}>Create a workspace</Button>
          </p>
        ) : (
        <SurfaceCard tourId="myteam-create" title="Create a workspace" subtitle="Starting fresh for your team?" mobileCollapsible={false}>
          <form className="my-team__form" onSubmit={submitCreate}>
            <FieldText
              label="Workspace name"
              placeholder="Eagle Robotics Scouting"
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
            {signupUnavailable || (signupPolicy?.required && !signupPolicy.site_key) ? (
              <p className="center-callout warning" role="status">Workspace creation is temporarily unavailable. You can still join your team with its code.</p>
            ) : nativeChallenge ? (
              <p className="my-team__note">Create your workspace in a browser to complete the security check, then join here with its code. <a href={shareableAppUrl('/my-team')} target="_blank" rel="noopener noreferrer">Open My Team in a browser</a></p>
            ) : signupPolicy?.required && signupPolicy.site_key ? (
              <div className="my-team__challenge">
                <TurnstileChallenge key={challengeAttempt} siteKey={signupPolicy.site_key} onToken={setChallengeToken} />
              </div>
            ) : !signupPolicy ? (
              <div className="my-team__challenge">
                <p className="my-team__note" role="status">Checking workspace availability…</p>
              </div>
            ) : null}
            {error?.form === 'create' ? <p className="center-callout warning" role="alert">{error.text}</p> : null}
            <Button type="submit" variant="primary" fullWidth loading={busy === 'create'} disabled={busy !== null || !canCreate}>
              Create workspace
            </Button>
          </form>
        </SurfaceCard>
        )}
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
  const [copied, setCopied] = useState<'idle' | 'copied' | 'failed'>('idle');
  const [linkCopied, setLinkCopied] = useState<'idle' | 'copied' | 'failed'>('idle');
  const qrRef = useRef<HTMLCanvasElement>(null);
  const inviteLink = code ? shareableAppUrl(`/my-team?code=${encodeURIComponent(code)}`) : '';

  useEffect(() => {
    if (!code || !qrRef.current) return;
    // jsdom has no canvas; the code and link are still on screen without it.
    QRCode.toCanvas(qrRef.current, inviteLink, { width: 192, margin: 2, errorCorrectionLevel: 'M', color: { dark: '#0f172a', light: '#ffffff' } }).catch(() => {});
  }, [code, inviteLink]);

  const share = async () => {
    if (typeof navigator.share === 'function') {
      try {
        await navigator.share({ title: 'Join our scouting team', text: 'Join our FRC scouting workspace:', url: inviteLink });
        return;
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
      }
    }
    const ok = await copyTextToClipboard(inviteLink);
    setLinkCopied(ok ? 'copied' : 'failed');
    if (ok) window.setTimeout(() => setLinkCopied('idle'), 2000);
  };

  const copy = async () => {
    if (!code) return;
    const ok = await copyTextToClipboard(code);
    setCopied(ok ? 'copied' : 'failed');
    if (ok) window.setTimeout(() => setCopied('idle'), 2000);
  };

  if (code) {
    return (
      <div className="my-team__code-block">
        <p className="my-team__code" aria-label={`Join code ${code.split('').join(' ')}`}>{code}</p>
        <div className="my-team__code-actions">
          <Button variant="primary" onClick={copy}>{copied === 'copied' ? 'Copied' : 'Copy code'}</Button>
          <Button onClick={share}>{linkCopied === 'copied' ? 'Link copied' : 'Share invite link'}</Button>
        </div>
        <div className="my-team__qr">
          <canvas ref={qrRef} width={192} height={192} role="img" aria-label="QR code that opens the invite link" />
          <p className="my-team__note">Or have a teammate scan this with their camera.</p>
        </div>
        {linkCopied === 'failed' ? (
          <p className="my-team__note" role="alert">This browser blocked sharing and copying. Send the code above instead.</p>
        ) : null}
        {copied === 'failed' ? (
          <p className="my-team__note" role="alert">This browser blocked copying. Write the code down or select it above.</p>
        ) : null}
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
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState('');
  const [editingWorkspace, setEditingWorkspace] = useState(false);
  const [workspaceName, setWorkspaceName] = useState('');
  const [teamNumber, setTeamNumber] = useState('');
  const [removing, setRemoving] = useState<WorkspaceMemberRecord | null>(null);
  const [acting, setActing] = useState<WorkspaceMemberRecord | null>(null);
  const [rotateOnRemove, setRotateOnRemove] = useState(true);
  const [leaving, setLeaving] = useState<'confirm' | 'last-member' | null>(null);
  const refreshInFlightRef = useRef<Promise<void> | null>(null);

  const refresh = useCallback(() => {
    if (refreshInFlightRef.current) return refreshInFlightRef.current;
    const request = (async () => {
      try {
        setState(await getMyWorkspace());
        setRefreshError(null);
      } catch (err) {
        setRefreshError(errorMessage(err));
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
    if (label === 'remove') setRemoveError(null);
    try {
      const result = await action();
      if (result) {
        setState(result);
        if (result.join_code) setFreshCode(result.join_code);
      }
      return true;
    } catch (err) {
      if (label === 'remove') setRemoveError(errorMessage(err));
      else setError(errorMessage(err));
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
      {/* What a scout opens this page for: where they're scouting and what's next. */}
      <TeamScoutingCard members={state?.members} />

      <SurfaceCard tourId="myteam-workspace"
        title={workspace.name}
        subtitle={
          workspace.frc_team_number
            ? `Team ${workspace.frc_team_number} · private workspace`
            : 'Private team workspace'
        }
        mobileCollapsible={false}
      >
        {error || refreshError ? <p className="center-callout warning" role="alert">{error || refreshError}</p> : null}
        <p className="my-team__lede">Only your team sees its picklists, pit notes and scouting.</p>
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

      {isLeader ? (
      <SurfaceCard tourId="myteam-invite" title="Invite scouts" subtitle="Anyone with the code can join this workspace." mobileCollapsible={false}>
        <JoinCodePanel
          code={freshCode}
          isLeader={isLeader}
          busy={busy === 'rotate'}
          onRotate={() => void run('rotate', rotateWorkspaceJoinCode)}
        />
      </SurfaceCard>
      ) : null}

      <SurfaceCard tourId="myteam-members"
        title={state ? `Members (${members.length})` : 'Members'}
        subtitle={isLeader ? 'Tap a member to change their role or remove them.' : undefined}
        mobileCollapsible={false}
      >
        {!state && !error ? <p className="my-team__note">Loading members…</p> : null}
        <ul className="my-team__members">
          {members.map((member) => {
            const self = member.id === me?.id;
            const tappable = isLeader && !self;
            const content = (
            <>
            <span className="my-team__member-main">
              <span className="my-team__member-name" title={member.display_name}>
                {member.display_name}
                {self ? <span className="my-team__you"> (you)</span> : null}
              </span>
              <span className="my-team__member-meta">{lastSeen(member.last_seen_at)}</span>
            </span>
            <Chip size="sm" tone={member.role === 'leader' ? 'accent' : 'neutral'}>
              {member.role === 'leader' ? 'Leader' : 'Member'}
            </Chip>
            {tappable ? <span className="my-team__member-arrow" aria-hidden="true">›</span> : null}
            </>
            );
            return (
              <li key={member.id} className="my-team__member-item">
                {tappable ? (
                  <button
                    type="button"
                    className="my-team__member my-team__member--action"
                    aria-label={`${member.display_name}, ${member.role}. Open actions`}
                    onClick={() => { setError(null); setActing(member); }}
                  >
                    {content}
                  </button>
                ) : (
                  <div className="my-team__member">{content}</div>
                )}
              </li>
            );
          })}
        </ul>
      </SurfaceCard>

      {/* Leaders reach their tools first; the season recap is reading, so it follows them. */}
{workspace.frc_team_number ? <SeasonStoryCard teamKey={`frc${workspace.frc_team_number}`} title="Our season" /> : null}

      <SurfaceCard title="Leave workspace" mobileCollapsible={false}>
        <p className="my-team__note">
          This device stops seeing your team's picklists, pit notes and rooms. You can rejoin with a current code.
        </p>
        <Button variant="danger" onClick={() => setLeaving('confirm')}>Leave workspace</Button>
      </SurfaceCard>

      <MemberSheet
        member={acting}
        busy={acting !== null && busy === `role-${acting.id}`}
        error={error}
        onClose={() => setActing(null)}
        onToggleRole={async () => {
          if (!acting) return;
          const target = acting;
          if (await run(`role-${target.id}`, () => setWorkspaceMemberRole(target.id, target.role === 'leader' ? 'member' : 'leader'))) {
            setActing(null);
          }
        }}
        onRemove={() => { setRotateOnRemove(true); setRemoveError(null); setRemoving(acting); setActing(null); }}
      />

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
        {removeError ? <p className="center-callout warning" role="alert">{removeError}</p> : null}
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
