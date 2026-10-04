import {beforeEach,describe,expect,it,vi} from 'vitest';
import {storageMethodTarget} from '../../test/storage';
import {readPitDraft,savePitDraft,clearConfirmedPitDraft,rememberPitSelection,readPitSelection,pitDraftPersisted} from './pitDrafts';
describe('private pit recovery drafts',()=>{
 beforeEach(()=>localStorage.clear());
 it('recovers edits only for the same workspace, event and robot',()=>{
  expect(savePitDraft(1,'2026arc','frc254',{notes:'Keep me'})).toBe(true);
  expect(readPitDraft(1,'2026arc','frc254')).toEqual({notes:'Keep me'});
  rememberPitSelection(1,'2026arc','frc254');expect(readPitSelection(1,'2026arc')).toBe('frc254');expect(readPitSelection(2,'2026arc')).toBeNull();
  expect(readPitDraft(2,'2026arc','frc254')).toBeNull();expect(readPitDraft(1,'2026other','frc254')).toBeNull();expect(readPitDraft(1,'2026arc','frc1678')).toBeNull();
 });
 it('does not erase newer edits when an earlier save finishes',()=>{
  savePitDraft(1,'2026arc','frc254',{notes:'Newer'});clearConfirmedPitDraft(1,'2026arc','frc254',{notes:'Earlier'});expect(readPitDraft(1,'2026arc','frc254')).toEqual({notes:'Newer'});
  clearConfirmedPitDraft(1,'2026arc','frc254',{notes:'Newer'});expect(readPitDraft(1,'2026arc','frc254')).toBeNull();
 });
 it('reports quota failures instead of claiming the draft is safe',()=>{
  const spy=vi.spyOn(storageMethodTarget(),'setItem').mockImplementation(()=>{throw new DOMException('Full','QuotaExceededError');});
  expect(savePitDraft(1,'2026arc','frc254',{notes:'Keep page open'})).toBe(false);expect(readPitDraft(1,'2026arc','frc254')).toEqual({notes:'Keep page open'});expect(pitDraftPersisted(1,'2026arc','frc254')).toBe(false);spy.mockRestore();clearConfirmedPitDraft(1,'2026arc','frc254',{notes:'Keep page open'});
 });
});
