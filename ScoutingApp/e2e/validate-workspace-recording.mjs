// Real UI + IndexedDB + API boundary, with every request intercepted locally.
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
const base = process.env.OFFLINE_TEST_URL || 'http://127.0.0.1:4179';
assert.ok(['localhost','127.0.0.1'].includes(new URL(base).hostname));
const browser = await chromium.launch({channel:'chrome'});
const realApi = process.env.WORKSPACE_REAL_API || '';
let realWorkspace = null;
if (realApi) {
 assert.equal(realApi, 'http://127.0.0.1:4182', 'Only the disposable loopback server is allowed');
 const health = await (await fetch(realApi+'/health')).json(); assert.equal(health.disposable_beta,true);
 const response = await fetch(realApi+'/workspaces',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Fixture scouting team',display_name:'Fixture Leader',frc_team_number:254})});
 assert.equal(response.status,200);realWorkspace=await response.json();
}
const workspace = {id:999999,name:'Fixture scouting team',frc_team_number:254};
const me = {id:999999,display_name:'Fixture Scout',role:'member',joined_at:null,last_seen_at:null};
let revoked = false, uploadMode = 'interrupted', joins = 0;
const uploads=[]; const errors=[];let realMember=null;
const realRequest=async(path,method='GET',body,token)=>{
 const response=await fetch(realApi+path,{method,headers:{'Content-Type':'application/json',...(token?{'X-Workspace-Access':token}:{})},...(body?{body:JSON.stringify(body)}:{})});
 return {status:response.status,body:await response.text(),contentType:'application/json'};
};
try {
 const context = await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block',acceptDownloads:true});
 await context.addInitScript(()=>{localStorage.setItem('frcmob_tour_prompt_dismissed_v1','1');localStorage.setItem('scouting_tutorial_autoplay','false');});
 await context.route('**/*',async route=>{
  const request=route.request(), url=new URL(request.url());
  if(url.pathname.startsWith('/_vercel/')) return route.fulfill({contentType:'application/javascript',body:''});
  const api = url.hostname==='localhost' && url.port==='8000' || url.pathname.startsWith('/api/');
  if(!api) return url.origin===new URL(base).origin ? route.continue() : route.abort();
  const path=url.pathname.replace(/^\/api/,'');
  if(path==='/health') return route.fulfill({json:{ok:true}});
  if(path==='/workspaces/join') {
   joins++; await new Promise(resolve=>setTimeout(resolve,200));
   if(realApi) {const result=await realRequest(path,'POST',{...request.postDataJSON(),join_code:realWorkspace.join_code});realMember=JSON.parse(result.body);return route.fulfill(result);}
   return route.fulfill({json:{ok:true,workspace,me,members:[me],access:{token:'fixture-workspace-token',expires_at_unix:Date.now()/1000+3600}}});
  }
  if(realApi && path==='/workspaces/me') return route.fulfill(await realRequest(path,'GET',undefined,request.headers()['x-workspace-access']));
  if(path==='/workspaces/me') return revoked ? route.fulfill({status:401,json:{detail:'Fixture access revoked'}}) : route.fulfill({json:{ok:true,workspace,me,members:[me]}});
  if(path==='/tracks/on-device-session') {
   const body=request.postDataJSON(); const headers=request.headers();
   uploads.push({id:body.id,workspaceId:body.workspaceId,correctAccess:headers['x-workspace-access']===(realMember?.access.token || 'fixture-workspace-token')});
   if(realApi) {
    const attemptMode=uploadMode;
    const result=await realRequest(path,'POST',attemptMode==='conflict'?{...body,workspaceId:realWorkspace.workspace.id+1}:body,headers['x-workspace-access']);
    assert.equal(result.status,attemptMode==='conflict'?409:200,'Real endpoint rejected payload');
    if(attemptMode==='interrupted') return route.abort('connectionreset');
    return route.fulfill(result);
   }
   if(uploadMode==='interrupted') return route.abort('connectionreset');
   if(uploadMode==='conflict') return route.fulfill({status:409,json:{detail:'Fixture review conflict'}});
   return route.fulfill({json:{ok:true,session_id:body.id}});
  }
  return route.fulfill({status:404,json:{detail:'No fixture data'}});
 });
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/#/my-team');await page.getByPlaceholder('ABCDE-12345').waitFor();
 await page.evaluate(async()=>{
  const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('frcmob-ondevice',1);r.onupgradeneeded=()=>{for(const name of ['sessions','calibrations'])if(!r.result.objectStoreNames.contains(name))r.result.createObjectStore(name,{keyPath:'id'});};r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
  const tx=db.transaction('sessions','readwrite');tx.objectStore('sessions').put({id:'workspace-flow-recording',eventKey:'2026test',matchKey:'2026test_qm1',createdAt:Date.now(),synced:false,workspaceId:null,payload:{schemaVersion:'on_device_session_v2',pointsByTeam:{frc254:[{timeSec:0,fieldX:1,fieldY:1},{timeSec:1,fieldX:2,fieldY:1}]},sampledFrameCount:2,modelVersion:'fixture',calibrationVersion:'fixture',captureSource:'video',poseSource:'static',identitySource:'manual',timingSource:'video_offset'}});
  await new Promise((resolve,reject)=>{tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);});db.close();window.dispatchEvent(new Event('frcmob:session-change'));
 });
 const readRecording=()=>page.evaluate(async()=>{const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('frcmob-ondevice',1);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});const r=db.transaction('sessions').objectStore('sessions').get('workspace-flow-recording');const row=await new Promise((resolve,reject)=>{r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});db.close();return row;});
 const form=page.locator('form').filter({has:page.getByPlaceholder('ABCDE-12345')});
 await form.getByPlaceholder('ABCDE-12345').fill('ABCDE12345');await form.getByRole('textbox',{name:'Your name',exact:true}).fill('Fixture Scout');
 await form.getByRole('button',{name:'Join team',exact:true}).dblclick();
 await page.getByText('Fixture scouting team',{exact:true}).first().waitFor();
 await page.waitForFunction(async()=>{const db=await new Promise(resolve=>{const r=indexedDB.open('frcmob-ondevice',1);r.onsuccess=()=>resolve(r.result);});const r=db.transaction('sessions').objectStore('sessions').get('workspace-flow-recording');const row=await new Promise(resolve=>{r.onsuccess=()=>resolve(r.result);});db.close();return row?.syncFailure?.kind==='connection';});
 assert.equal(joins,1,'Rapid join created duplicate requests');assert.equal(uploads.length,1);
 const interrupted=await readRecording();assert.equal(interrupted.synced,false);assert.equal(interrupted.workspaceId,realWorkspace?.workspace.id || workspace.id);
 uploadMode='conflict';
 const recovery=page.locator('section').filter({has:page.getByText('Saved on this phone',{exact:true})});
 await recovery.getByRole('button',{name:'Sync now',exact:true}).click();await page.getByText(/server response 409/).waitFor();
 assert.equal((await readRecording()).syncFailure.kind,'rejected');const conflictedCount=uploads.length;
 await page.reload();await page.getByText(/server response 409/).waitFor();await page.waitForTimeout(2700);assert.equal(uploads.length,conflictedCount,'Rejected recording replayed automatically');
 uploadMode='confirmed';await recovery.getByRole('button',{name:'Sync now',exact:true}).click();await page.waitForFunction(async()=>{const db=await new Promise(resolve=>{const r=indexedDB.open('frcmob-ondevice',1);r.onsuccess=()=>resolve(r.result);});const r=db.transaction('sessions').objectStore('sessions').get('workspace-flow-recording');const row=await new Promise(resolve=>{r.onsuccess=()=>resolve(r.result);});db.close();return row?.synced===true;});
 assert.ok(uploads.every(row=>row.id==='workspace-flow-recording'&&row.workspaceId===(realWorkspace?.workspace.id || workspace.id)&&row.correctAccess));
 if(realApi) {
  const evidence=await (await fetch(realApi+'/test-evidence')).json();assert.equal(evidence.sessions,1);assert.equal(evidence.tracks,2);
  const removal=await realRequest('/workspaces/me/members/'+realMember.me.id+'/remove','POST',{rotate_join_code:true},realWorkspace.access.token);assert.equal(removal.status,200);
 }
 revoked=true;await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
 await page.getByPlaceholder('ABCDE-12345').waitFor();assert.equal(await page.evaluate(()=>localStorage.getItem('frcmob_workspace_session_v1')),null);
 await page.goto(base+'/#/compare/picklist');await page.getByText('You were signed out of your team',{exact:true}).waitFor();
 assert.equal((await readRecording()).synced,true,'Revocation removed the saved recording');assert.deepEqual(errors,[]);
 await page.screenshot({path:'/tmp/frcmob-workspace-revoked-phone.png',fullPage:true});
 console.log(JSON.stringify({passed:true,rapidJoinSingleRequest:true,interruptedUploadRetained:true,workspacePinnedBeforeFirstUpload:true,conflictsNotAutoReplayed:true,confirmedRetrySameRecording:true,revocationClearsAccess:true,revocationRetainsRecording:true,pageErrors:errors,backend:realApi?'real-loopback-sqlite':'mocked',committedResponseLostAndRetried:Boolean(realApi),productionWrites:0}));
} finally {await browser.close();}
