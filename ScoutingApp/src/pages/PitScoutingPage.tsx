import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { EventPicker } from '../components/EventPicker';
import { ScoutingViewBar } from '../components/ScoutingViewBar';
import { scoutingViewTitle } from '../components/pageViewBarConfig';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { useEventNames } from '../hooks/useEventNames';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { WorkspaceGate } from '../features/workspace/WorkspaceGate';
import { PitConflicts } from '../features/pit/PitConflicts';
import { PitForm } from '../features/pit/PitForm';
import { PitPhotos } from '../features/pit/PitPhotos';
import { PitSaveBar } from '../features/pit/PitSaveBar';
import { PitEventStrip } from '../features/pit/PitEventStrip';
import { PitTeamPicker } from '../features/pit/PitTeamPicker';
import { usePitStepper } from '../features/pit/pitStepConfig';
import { usePitScouting } from '../features/pit/usePitScouting';
import { deepLinkTeam } from '../features/pit/pitTeams';

function PitScoutingWorkspacePage() {
  const isMobile = useMobileLayout();
  const eventName = useEventNames();
  const pit = usePitScouting();
  const stepper = usePitStepper(pit.selectedTeam);
  const { selectedTeam, errorText } = pit;
  const errorRef = useRef<HTMLParagraphElement | null>(null);
  const [searchParams] = useSearchParams();
  const linkedTeam = searchParams.get('team');
  const openedLink = useRef('');
  const busy = pit.savingForm || pit.uploadingPhoto;

  useEffect(() => { if (errorText && !selectedTeam) errorRef.current?.scrollIntoView?.({ block: 'nearest' }); }, [errorText, selectedTeam]);

  // Opens the linked team once its event has loaded; it never claims the team.
  useEffect(() => {
    const target = deepLinkTeam(linkedTeam, pit.teams);
    const id = `${pit.eventKey}:${target}`;
    if (!target || openedLink.current === id) return;
    openedLink.current = id;
    pit.selectTeamForEditing(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkedTeam, pit.teams, pit.eventKey]);

  const discardUnsaved = (action: string) =>
    !(pit.formDirty && !pit.draftStored && !window.confirm(`This draft could not be saved on your device. Discard it and ${action}?`));

  const eventPicker = (close: () => void) => (
    <EventPicker
      label="Event"
      value={pit.eventKey}
      onSelect={key => { if (!discardUnsaved('change events')) { pit.setEventInput(pit.eventKey); return; } pit.selectEvent(key); close(); }}
      inputValue={pit.eventInput}
      onInputChange={pit.setEventInput}
      onSubmit={() => { if (!discardUnsaved('reload teams')) { pit.setEventInput(pit.eventKey); return; } pit.commitInput(); close(); }}
      loading={pit.loading}
      disabled={busy}
    />
  );
  const errorAlert = errorText && !selectedTeam ? <p ref={errorRef} className="center-callout warning" role="alert">{errorText}</p> : null;

  return (
    <>
      <ScoutingViewBar />
      <div className="center-page-container">

        {isMobile ? (
          // With a team open, the pinned team strip is the header.
          selectedTeam ? null : (
            <PitEventStrip
              eventName={pit.eventKey ? eventName(pit.eventKey) : ''}
              done={pit.completion.done}
              total={pit.completion.total}
              forceOpen={!pit.eventKey}
              picker={eventPicker}
              alert={errorAlert}
            />
          )
        ) : (
          <SurfaceCard tourId="pit-head"
            title={scoutingViewTitle('/scouting/pit')}
            subtitle="Robot specs, claimed capabilities, and photos — one entry per team."
            mobileCollapsible={false}
            right={
              pit.completion.total > 0 ? (
                <span className="center-chip">
                  {pit.completion.done}/{pit.completion.total} teams done
                </span>
              ) : null
            }
          >
            {eventPicker(() => undefined)}
            {errorAlert}
          </SurfaceCard>
        )}

        <PitTeamPicker
          eventKey={pit.eventKey}
          formDirty={pit.formDirty}
          teams={pit.teams}
          entriesByTeam={pit.entriesByTeam}
          selectedTeam={selectedTeam}
          disabled={busy}
          collapsible={isMobile}
          onSelect={pit.selectTeamForEditing}
        />

        {selectedTeam ? (
          <>
            <PitConflicts
              conflicts={pit.conflicts}
              saved={pit.selectedEntry?.payload ?? {}}
              disabled={busy || !pit.serverNotesReady}
              onResolve={(field, value) => void pit.resolveConflict(field, value)}
            />
            <PitForm
              teamKey={selectedTeam}
              nickname={pit.teams.find(team => team.team_key === selectedTeam)?.nickname ?? null}
              values={pit.form}
              onChange={pit.setField}
              stepper={stepper}
              photos={
                <PitPhotos
                  teamKey={selectedTeam}
                  entry={pit.selectedEntry}
                  uploading={pit.uploadingPhoto}
                  disabled={!pit.serverNotesReady}
                  onAdd={pit.addPhoto}
                  onDelete={path => void pit.deletePhoto(path)}
                  waiting={pit.waitingPhotos}
                  onDeleteWaiting={id => void pit.deleteWaitingPhoto(id)}
                  onRetryWaiting={ids => void pit.retryWaitingPhotos(ids)}
                />
              }
            />
            <PitSaveBar
              status={pit.saveStatus}
              saving={pit.savingForm}
              disabled={!pit.serverNotesReady}
              onSave={() => void pit.save()}
              onSaveAndNext={() => void pit.saveAndNext()}
              stepper={stepper}
            />
          </>
        ) : null}

      </div>
    </>
  );
}

// Team-only: pit notes are private to a workspace, so nothing loads until
// the device has joined one.
export function PitScoutingPage() {
  return (
    <WorkspaceGate feature="Pit notes" viewBar={<ScoutingViewBar />}>
      <PitScoutingWorkspacePage />
    </WorkspaceGate>
  );
}
