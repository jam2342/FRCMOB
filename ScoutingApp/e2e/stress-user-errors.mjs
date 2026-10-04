// Local-only user mistake/recovery tests. Every API call is intercepted.
import assert from 'node:assert/strict';
import { chromium, webkit } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
const base=process.env.USER_URL || 'http://127.0.0.1:4179';
assert.ok(['localhost','127.0.0.1'].includes(new URL(base).hostname),'Use a local preview, never production.');
const out=process.env.USER_OUTPUT || '/tmp/frcmob-user-errors';await mkdir(out,{recursive:true});
const browser=await (process.env.USER_BROWSER==='webkit'?webkit:chromium).launch(process.env.USER_BROWSER==='webkit'?{}:{channel:'chrome'});
const findings=[];const errors=[];const writes=[];
const workspace={id:999999,name:'User error fixture',frc_team_number:254};const me={id:999999,display_name:'Fixture Scout',role:'leader'};
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const contexts=[];
async function pageFor(width,signedIn=true,failRead=false){
 const context=await browser.newContext({viewport:{width,height:844},serviceWorkers:'block'});contexts.push(context);
 await context.addInitScript(({signedIn,workspace,me,failRead})=>{localStorage.setItem('frcmob_tour_prompt_dismissed_v1','1');localStorage.setItem('scouting_tutorial_autoplay','false');localStorage.setItem('scouting_theme_mode','light');if(failRead==='draft-only' && !localStorage.getItem('frcmob_pit_draft_v1:999999:2026test:frc254')){localStorage.setItem('frcmob_pit_selection_v1:999999:2026test','frc254');localStorage.setItem('frcmob_pit_draft_v1:999999:2026test:frc254',JSON.stringify({payload:{notes:'Offline local draft'}}));}if(signedIn)localStorage.setItem('frcmob_workspace_session_v1',JSON.stringify({token:'local-user-error-fixture',expiresAt:Date.now()/1000+3600,workspace,me}));},{signedIn,workspace,me,failRead});
 let entries=[];let saves=0;
 await context.route('**/*',async route=>{
  const r=route.request(),u=new URL(r.url());const path=u.pathname.replace(/^\/api/,'');
  if(u.pathname.startsWith('/_vercel/'))return route.fulfill({contentType:'application/javascript',body:''});
  if(!u.pathname.startsWith('/api/') && !(u.hostname==='localhost'&&u.port==='8000'))return u.origin===base?route.continue():route.abort();
  if(r.method()!=='GET') writes.push({path,body:r.postDataJSON()});
  if(path==='/health')return route.fulfill({json:{ok:true}});
  if(path==='/workspaces/me')return route.fulfill({json:{ok:true,workspace,me,members:[me]}});
  if(path==='/workspaces'||path==='/workspaces/join')return route.fulfill({status:400,json:{detail:'Fixture rejected input'}});
  if(path.includes('/teams/event/')) {const numbers=path.includes('2026slow')?[254]:path.includes('2026fast')?[1678]:[254,1678];if(path.includes('2026slow'))await pause(900);return route.fulfill({json:{ok:true,teams:numbers.map(n=>({team_key:'frc'+n,team_number:n,nickname:'Fixture team '+n}))}});}
  if(path==='/pit-scouting'&&r.method()==='GET'){if(failRead){if(failRead!== 'draft-only')failRead=false;return route.fulfill({status:503,json:{detail:'Saved notes temporarily unavailable'}});}return route.fulfill({json:{ok:true,entries}});}
  if(path.startsWith('/pit-scouting')&&r.method()==='POST'){
   saves++;const body=r.postDataJSON();if(saves===1){await pause(700);return route.fulfill({status:503,json:{detail:'Connection temporarily unavailable'}});}
   await pause(500);const entry={id:saves,...body,photos:[],updated_at:new Date().toISOString()};entries=[entry];return route.fulfill({json:{ok:true,entry}});
  }
  return route.fulfill({status:404,json:{detail:'No fixture data'}});
 });
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));return page;
}
async function check(page,label,condition){let passed=false;for(let i=0;i<30;i++){passed=await condition();if(passed)break;await page.waitForTimeout(50);}findings.push({label,passed});if(!passed)await page.screenshot({path:out+'/'+label+'.png',fullPage:true});console.log(passed?'PASS':'FAIL',label);}
try{
 for(const width of [390,1440]){
  const page=await pageFor(width,false);await page.goto(base+'/#/my-team');await page.getByPlaceholder('Robonauts Scouting').waitFor();
  const create=page.locator('form').filter({has:page.getByPlaceholder('Robonauts Scouting')});
  await create.getByPlaceholder('Robonauts Scouting').fill('Test');await create.getByRole('textbox',{name:'Your name',exact:true}).fill('Scout');await create.getByRole('textbox',{name:'FRC team number'}).fill('-254');
  const before=writes.length;await create.getByRole('button',{name:'Create workspace'}).click();await page.waitForTimeout(300);
  await check(page,'invalid-team-'+width,async()=>writes.length===before && /whole|positive|digits|valid team number/.test(await create.innerText()));
  await create.getByRole('textbox',{name:'FRC team number'}).fill('254');await create.getByRole('textbox',{name:'Your name',exact:true}).fill('   ');
  const whitespace=writes.length;await create.getByRole('button',{name:'Create workspace'}).click();await page.waitForTimeout(150);await check(page,'blank-name-'+width,async()=>writes.length===whitespace);
  const join=page.locator('form').filter({has:page.getByPlaceholder('ABCDE-12345')});await join.getByPlaceholder('ABCDE-12345').fill('wrong');await join.getByRole('textbox',{name:'Your name',exact:true}).fill('Scout');const invalidCode=writes.length;await join.getByRole('button',{name:'Join team',exact:true}).click();await check(page,'invalid-join-code-'+width,async()=>writes.length===invalidCode && (await join.innerText()).includes('10-character'));
  const pit=await pageFor(width);await pit.goto(base+'/#/scouting/pit?event=2026test');await pit.getByRole('button',{name:'Team 254',exact:true}).waitFor();await pit.getByRole('button',{name:'Team 254',exact:true}).click();
  const notes=pit.getByRole('textbox',{name:'Notes',exact:true});await notes.fill('Keep my pit notes after accidental navigation');
  await pit.goto(base+'/#/settings');await pit.getByRole('combobox',{name:'Theme',exact:true}).waitFor();await pit.goBack();await pit.getByRole('button',{name:'Team 254',exact:true}).click();
  await check(pit,'pit-navigation-draft-'+width,async()=>await notes.inputValue()==='Keep my pit notes after accidental navigation');
  await notes.fill('Keep my edits when a save fails');await pit.getByRole('button',{name:'Save entry',exact:true}).first().click();await pit.getByText('Connection temporarily unavailable',{exact:true}).waitFor();await pit.reload();await pit.getByRole('textbox',{name:'Notes',exact:true}).waitFor();
  await check(pit,'pit-failed-save-reload-'+width,async()=>await notes.inputValue()==='Keep my edits when a save fails');
  await notes.fill('Team 254 draft');pit.on('dialog',dialog=>dialog.accept());await pit.getByRole('button',{name:'Team 1678',exact:true}).click();await notes.fill('Team 1678 draft');await pit.getByRole('button',{name:'Team 254',exact:true}).click();
  await check(pit,'pit-switch-team-draft-'+width,async()=>await notes.inputValue()==='Team 254 draft');
  await notes.fill('Submitted snapshot');await pit.getByRole('button',{name:'Save entry',exact:true}).first().click();await notes.fill('New edits while saving');await pit.getByText('Saved pit entry for #254.',{exact:true}).waitFor();await pit.reload();await pit.getByRole('button',{name:/Team 254/}).click();
  await check(pit,'pit-edit-during-save-'+width,async()=>await notes.inputValue()==='New edits while saving');
  const weight=pit.getByRole('spinbutton',{name:/^Weight/});await weight.fill('-5');const invalidWeight=writes.length;await pit.getByRole('button',{name:'Save entry',exact:true}).first().click();await pit.waitForTimeout(150);await check(pit,'pit-negative-weight-'+width,async()=>writes.length===invalidWeight && /Weight.*positive|Weight.*zero/.test(await pit.innerText('body')));await weight.fill('120');
  const photoWrites=writes.length;await pit.locator('input[type=file]').setInputFiles({name:'wrong.txt',mimeType:'text/plain',buffer:Buffer.from('wrong file')});await pit.waitForTimeout(100);await check(pit,'pit-wrong-photo-'+width,async()=>writes.length===photoWrites && (await pit.getByRole('alert').innerText()).includes('Choose an image'));
  const corruptWrites=writes.length;await pit.locator('input[type=file]').setInputFiles({name:'broken.jpg',mimeType:'image/jpeg',buffer:Buffer.from('unreadable image')});await check(pit,'pit-corrupt-photo-'+width,async()=>writes.length===corruptWrites && (await pit.getByRole('alert').innerText()).includes('Could not read this photo'));
  await check(pit,'pit-overflow-'+width,()=>pit.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await pit.screenshot({path:out+'/pit-recovery-'+width+'.png',fullPage:true});
  const value=JSON.stringify({token:'other-local-fixture',expiresAt:Date.now()/1000+3600,workspace:{...workspace,id:999998},me});await pit.evaluate(value=>{localStorage.setItem('frcmob_workspace_session_v1',value);window.dispatchEvent(new StorageEvent('storage',{key:'frcmob_workspace_session_v1',newValue:value}));},value);await pit.getByRole('button',{name:/Team 254/}).click();
  await check(pit,'pit-other-workspace-'+width,async()=>await notes.inputValue()!=='New edits while saving');
  const race=await pageFor(width);const slowStarted=race.waitForRequest(request=>request.url().includes('/teams/event/2026slow'));await race.goto(base+'/#/scouting/pit?event=2026slow');await slowStarted;const slowFinished=race.waitForResponse(response=>response.url().includes('/teams/event/2026slow'));await race.evaluate(()=>{location.hash='/scouting/pit?event=2026fast';});await race.getByRole('button',{name:'Team 1678',exact:true}).waitFor();await slowFinished;await check(race,'pit-stale-event-'+width,async()=>await race.getByRole('button',{name:'Team 1678',exact:true}).count()===1 && await race.getByRole('button',{name:'Team 254',exact:true}).count()===0);
  const quota=await pageFor(width);await quota.goto(base+'/#/scouting/pit?event=2026test');await quota.getByRole('button',{name:'Team 254',exact:true}).click();await quota.evaluate(()=>{const original=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(key.startsWith('frcmob_pit_draft_v1:'))throw new DOMException('Storage full','QuotaExceededError');return original.call(this,key,value);};});await quota.getByRole('textbox',{name:'Notes',exact:true}).fill('Keep this draft open');await check(quota,'pit-full-storage-warning-'+width,async()=>(await quota.getByRole('alert').innerText()).includes('could not save your draft'));quota.on('dialog',dialog=>dialog.dismiss());await quota.getByRole('button',{name:'Team 1678',exact:true}).click();await check(quota,'pit-full-storage-cancel-'+width,async()=>await quota.getByRole('textbox',{name:'Notes',exact:true}).inputValue()==='Keep this draft open');await quota.goto(base+'/#/settings');await quota.getByRole('combobox',{name:'Theme',exact:true}).waitFor();await quota.goBack();await check(quota,'pit-full-storage-route-recovery-'+width,async()=>await quota.getByRole('textbox',{name:'Notes',exact:true}).inputValue()==='Keep this draft open');
  const retry=await pageFor(width,true,true);await retry.goto(base+'/#/scouting/pit?event=2026test');await retry.getByRole('alert').waitFor();await check(retry,'pit-failed-read-blocks-editing-'+width,async()=>await retry.getByRole('button',{name:'Team 254',exact:true}).count()===0);await retry.getByRole('textbox',{name:'Search events',exact:true}).press('Enter');await check(retry,'pit-load-retry-'+width,async()=>await retry.getByRole('button',{name:'Team 254',exact:true}).count()===1);
  const draftOnly=await pageFor(width,true,'draft-only');await draftOnly.goto(base+'/#/scouting/pit?event=2026test');await draftOnly.getByRole('textbox',{name:'Notes',exact:true}).waitFor();await check(draftOnly,'pit-disconnected-draft-'+width,async()=>await draftOnly.getByRole('textbox',{name:'Notes',exact:true}).inputValue()==='Offline local draft' && await draftOnly.getByRole('button',{name:'Save entry',exact:true}).first().isDisabled());await draftOnly.getByRole('textbox',{name:'Notes',exact:true}).fill('Offline edits retained');await draftOnly.reload();await check(draftOnly,'pit-disconnected-edit-reload-'+width,async()=>await draftOnly.getByRole('textbox',{name:'Notes',exact:true}).inputValue()==='Offline edits retained');
  await pit.screenshot({path:out+'/pit-phone-'+width+'.png',fullPage:true});
 }
 assert.deepEqual(errors,[]);await writeFile(out+'/report.json',JSON.stringify({findings,errors,writes:writes.length,productionWrites:0},null,2));console.log(JSON.stringify({checks:findings.length,failures:findings.filter(f=>!f.passed).length,errors,productionWrites:0}));if(findings.some(f=>!f.passed))process.exitCode=1;
}finally{await browser.close();}
