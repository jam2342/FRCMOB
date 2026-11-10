import { useEffect, useRef } from 'react';
import { EventPicker } from '../components/EventPicker';
import { ScoutingViewBar } from '../components/ScoutingViewBar';
import { scoutingViewTitle } from '../components/pageViewBarConfig';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { useMobileLayout } from '../hooks/useMobileLayout';
import { WorkspaceGate } from '../features/workspace/WorkspaceGate';
import { PitForm } from '../features/pit/PitForm';
import { PitPhotos } from '../features/pit/PitPhotos';
import { PitSaveBar } from '../features/pit/PitSaveBar';
import { PitTeamPicker } from '../features/pit/PitTeamPicker';
import { usePitScouting } from '../features/pit/usePitScouting';

function PitScoutingWorkspacePage() {
  const isMobile = useMobileLayout();
  const pit = usePitScouting();
  const { selectedTeam, errorText } = pit;
  const errorRef = useRef<HTMLParagraphElement | null>(null);
  const busy = pit.savingForm || pit.uploadingPhoto;

  useEffect(() => { if (errorText && !selectedTeam) errorRef.current?.scrollIntoView?.({ block: 'nearest' }); }, [errorText, selectedTeam]);

  const discardUnsaved = (action: string) =>
    !(pit.formDirty && !pit.draftStored && !window.confirm(`This draft could not be saved on your device. Discard it and ${action}?`));

  return (
    <>
      <ScoutingViewBar />
      <div className="center-page-container">

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
          <EventPicker
            label="Event"
            value={pit.eventKey}
            onSelect={key => { if (!discardUnsaved('change events')) { pit.setEventInput(pit.eventKey); return; } pit.selectEvent(key); }}
            inputValue={pit.eventInput}
            onInputChange={pit.setEventInput}
            onSubmit={() => { if (!discardUnsaved('reload teams')) { pit.setEventInput(pit.eventKey); return; } pit.commitInput(); }}
            loading={pit.loading}
            disabled={busy}
          />
          {errorText && !selectedTeam ? <p ref={errorRef} className="center-callout warning" role="alert">{errorText}</p> : null}
        </SurfaceCard>

        <PitTeamPicker
          teams={pit.teams}
          entriesByTeam={pit.entriesByTeam}
          selectedTeam={selectedTeam}
          disabled={busy}
          collapsible={isMobile}
          onSelect={pit.selectTeamForEditing}
        />

        {selectedTeam ? (
          <>
            <PitForm
              teamKey={selectedTeam}
              nickname={pit.teams.find(team => team.team_key === selectedTeam)?.nickname ?? null}
              values={pit.form}
              onChange={pit.setField}
              photos={
                <PitPhotos
                  teamKey={selectedTeam}
                  entry={pit.selectedEntry}
                  uploading={pit.uploadingPhoto}
                  disabled={!pit.serverNotesReady}
                  onAdd={pit.addPhoto}
                  onDelete={path => void pit.deletePhoto(path)}
                />
              }
            />
            <PitSaveBar
              status={pit.saveStatus}
              saving={pit.savingForm}
              disabled={!pit.serverNotesReady}
              onSave={() => void pit.save()}
              onSaveAndNext={() => void pit.saveAndNext()}
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
