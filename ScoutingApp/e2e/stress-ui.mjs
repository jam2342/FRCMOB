// Local-only adversarial UI sweep. Public upstream requests are GETs with no credentials.
// Private requests and every write are intercepted; fixtures never reach the live backend.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium, webkit } from 'playwright';
import { ROUTES } from './lib/harness.mjs';
const base = process.env.STRESS_URL || 'http://127.0.0.1:4179';
assert.ok(['localhost','127.0.0.1'].includes(new URL(base).hostname));
const out = process.env.STRESS_OUTPUT || '/tmp/frcmob-ui-stress';
await mkdir(out, {recursive:true});
const allProfiles = (process.env.STRESS_QUICK ? [
  {name:'small-phone-dark',width:320,height:720,theme:'dark'},
  {name:'desktop-light',width:1440,height:900,theme:'light'},
] : [
  {name:'small-phone-dark',width:320,height:720,theme:'dark'},
  {name:'phone-light',width:390,height:844,theme:'light'},
  {name:'landscape-dark',width:844,height:390,theme:'dark'},
  {name:'tablet-light',width:768,height:1024,theme:'light'},
  {name:'desktop-dark',width:1440,height:900,theme:'dark'},
  {name:'desktop-light',width:1440,height:900,theme:'light'},
  {name:'phone-large-text',width:390,height:844,theme:'dark',largeText:true},
]);
const profiles = process.env.STRESS_PROFILES ? allProfiles.filter(p=>process.env.STRESS_PROFILES.split(',').includes(p.name)) : allProfiles;
assert.ok(profiles.length, 'Select at least one stress profile');
const routes = process.env.STRESS_ROUTES ? process.env.STRESS_ROUTES.split(',') : [...ROUTES.filter(r=>!r.startsWith('/primitives')), '/my-team'];
const publicCache = new Map(); const upstream = []; const interceptedWrites = [];
const longName = 'ScoutingTeamWithAnExtremelyLongUnbrokenName'.repeat(5);
const workspace = {id:999999,name:longName.slice(0,80),frc_team_number:254};
const me = {id:999999,display_name:longName.slice(0,40),role:'leader',joined_at:null,last_seen_at:null};
const browser = await (process.env.STRESS_BROWSER === 'webkit' ? webkit : chromium).launch(process.env.STRESS_BROWSER === 'webkit' ? {} : {channel:'chrome'});
const results = []; const failures = [];
let mode = 'normal';
async function publicResponse(url) {
  // Ignore cache-busting values so stress navigation does not load the public service repeatedly.
  const key = new URL(url); for (const k of ['refresh','auto_heal_ratings','_','t','cache_bust']) key.searchParams.delete(k);
  const target = 'https://scouting-app-iryg.vercel.app'+key.pathname+key.search;
  if (!publicCache.has(target)) publicCache.set(target,(async()=>{
    const response = await fetch(target,{signal:AbortSignal.timeout(20_000)});
    const body = await response.text(); upstream.push({path:key.pathname,status:response.status});
    return {status:response.status,contentType:response.headers.get('content-type') || 'application/json',body};
  })().catch(error=>({status:503,json:{detail:'Stress fixture upstream unavailable: '+error.message}})));
  return publicCache.get(target);
}
async function routeRequests(route) {
  const request = route.request(); const url = new URL(request.url());
  if (url.hostname === 'localhost' && url.port === '8000') url.pathname = '/api'+url.pathname;
  if (url.pathname.startsWith('/_vercel/')) return route.fulfill({contentType:'application/javascript',body:''});
  if (!url.pathname.includes('/api/')) return url.origin === new URL(base).origin ? route.continue() : route.abort();
  if (!['GET','HEAD','OPTIONS'].includes(request.method())) {
    interceptedWrites.push({path:url.pathname,method:request.method()});
    return route.fulfill({status:409,json:{detail:'Stress test conflict: write intercepted locally'}});
  }
  if (mode === 'errors' && !url.pathname.endsWith('/health')) return route.fulfill({status:503,json:{detail:longName+' · temporarily unavailable'}});
  if (mode === 'offline') return route.abort('internetdisconnected');
  if (url.pathname.startsWith('/api/workspaces')) return route.fulfill({json:{ok:true,workspace,me,members:[me,{...me,id:999998,role:'member'}]}});
  if (url.pathname.startsWith('/api/picklists')) {
    const picklist={id:999999,event_key:'2026arc',title:longName.slice(0,80),created_by:longName.slice(0,40),version:1,live_mode:false,archived:false,created_at:null,updated_at:null,slots:[254,1678,1690,2046,2910,868].map(n=>({team_key:'frc'+n,tier:'first',status:'available',picked_by_alliance:null,dnp_reason:'',notes:longName}))};
    return route.fulfill({json:url.pathname==='/api/picklists'?{ok:true,picklists:[picklist]}:{ok:true,picklist}});
  }
  if (url.pathname.startsWith('/api/pit-scouting')) return route.fulfill({json:{ok:true,entries:[]}});
  if (url.pathname.startsWith('/api/scouting/rooms')) return route.fulfill({status:404,json:{detail:'No fixture room'}});
  if (url.pathname.startsWith('/api/scouting/insights/coverage')) return route.fulfill({json:{ok:true,event_key:'2026arc',summary:{total_slots:600,covered_slots:300,coverage_pct:50,total_entries:600,scout_count:25,outlier_count:1},grid:Array.from({length:100},(_,i)=>({match_key:'2026arc_qm'+(i+1),label:'Qual '+(i+1),comp_level:'qm',time:null,slots:[254,1678,1690,2046,2910,868].map((n,j)=>({team_key:'frc'+n,alliance:j<3?'red':'blue',station:String(j%3+1),entry_count:j%2,scouts:[longName.slice(0,40)]}))})),leaderboard:Array.from({length:25},(_,i)=>({scout_profile:longName.slice(0,40)+i,entry_count:25,matches_covered:20,teams_covered:10,best_qual_streak:12,last_entry_at:null})),outliers:[{kind:'scout_disagreement',match_key:'2026arc_qm1',team_key:'frc254',detail:longName,scouts:[longName.slice(0,40)]}]}});
  if (url.pathname.startsWith('/api/scouting/insights')) return route.fulfill({status:404,json:{detail:'No fixture scouting data'}});
  // Deny unknown/private prefixes rather than forwarding a fake workspace token.
  if (!/^\/api\/(health|events|teams|matches|ratings|synergy|predictions|scouting\/(team|event)|push\/public-key|season)/.test(url.pathname)) return route.fulfill({status:404,json:{detail:'No local fixture'}});
  return route.fulfill(await publicResponse(url));
}
async function measure(page) {
  return page.evaluate(()=>{
    const width=document.documentElement.clientWidth;
    const visible=el=>{const r=el.getBoundingClientRect();const s=getComputedStyle(el);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';};
    const offenders=[...document.querySelectorAll('body *')].filter(visible).map(el=>{
      const r=el.getBoundingClientRect();return {tag:el.tagName,cls:typeof el.className==='string'?el.className:'',right:Math.round(r.right-width),left:Math.round(r.left),text:el.textContent?.trim().slice(0,90)};
    }).filter(r=>r.right>2||r.left < -2).sort((a,b)=>b.right-a.right).slice(0,8);
    return {overflow:document.documentElement.scrollWidth-width,offenders,bodyLength:document.body.innerText.length,crash:/Something went wrong|Minified React error|couldn't load|Could not load this page/.test(document.body.innerText),bodyPreview:document.body.innerText.slice(0,400)};
  });
}
async function inspect(page,label,profile,errors) {
  const metric=await measure(page); const record={profile:profile.name,label,...metric,errors:[...errors]}; results.push(record);
  if (metric.overflow>2||metric.crash||errors.length||metric.bodyLength<100) {
    failures.push(record); await page.screenshot({path:out+'/'+profile.name+'-'+label.replace(/[^a-z0-9]/gi,'_').slice(0,100)+'.png',fullPage:true});
    console.log('FAIL',profile.name,label,JSON.stringify(record));
  }
}
try {
 for (const profile of profiles) {
  const context=await browser.newContext({viewport:{width:profile.width,height:profile.height},serviceWorkers:'block',reducedMotion:'reduce'});
  await context.route('**/*',routeRequests);
  await context.addInitScript(({theme,workspace,me,longName})=>{
    localStorage.setItem('frcmob_tour_prompt_dismissed_v1','1');
    localStorage.setItem('scouting_tutorial_autoplay','false');
    localStorage.setItem('scouting_theme_mode',theme);
    localStorage.setItem('scouting_center_event_key','2026arc');
    localStorage.setItem('scouting_center_team_key','frc254');
    localStorage.setItem('scouting_center_match_key','2026arc_qm1');
    localStorage.setItem('scouting_compare_event_key','2026arc');
    localStorage.setItem('scouting_compare_team_keys',JSON.stringify(['frc254','frc1678','frc1690']));
    localStorage.setItem('frcmob_workspace_session_v1',JSON.stringify({token:'local-stress-fixture',expiresAt:Date.now()/1000+3600,workspace,me}));
    window.__stressLongName=longName;
  },{theme:profile.theme,workspace,me,longName});
  const page=await context.newPage(); let errors=[]; page.on('pageerror',e=>{errors.push(e.message);console.log('PAGEERROR_STACK',e.stack);});
  page.on('console', message => {if(message.type()==='error' && !message.text().startsWith('Failed to load resource')) console.log('CONSOLE',message.text().slice(0,700));});
  const cdp = process.env.STRESS_DEBUG && process.env.STRESS_BROWSER !== 'webkit' ? await context.newCDPSession(page) : null;
  if(cdp) {await cdp.send('Debugger.enable');cdp.on('Debugger.paused', async event => {await writeFile(out+'/paused-stack.json',JSON.stringify(event.callFrames.map(frame=>({functionName:frame.functionName,url:frame.url,location:frame.location})),null,2)); console.log('Captured stalled renderer stack'); await cdp.send('Debugger.resume');});}

  for(const path of routes) {
    errors=[]; mode='normal';
    const watchdog = cdp ? setTimeout(()=>{console.log('Navigation watchdog',path);void cdp.send('Debugger.pause');},20_000) : null;
    await page.goto(base+'/#'+path+'?event=2026arc&team=frc254&match=2026arc_qm1',{waitUntil:'domcontentloaded'});
    await page.locator('main').first().waitFor({timeout:8000}).catch(()=>{}); await page.waitForLoadState('networkidle',{timeout:12_000}).catch(()=>{}); await page.getByRole('status',{name:'Loading page',exact:true}).waitFor({state:'hidden',timeout:8000}).catch(()=>{}); await page.waitForTimeout(200);
    if(profile.largeText) await page.evaluate(()=>{
      if(document.documentElement.dataset.stressTextScaled) return;
      const styles=getComputedStyle(document.documentElement);
      for(const name of Array.from(styles)) if(name.startsWith('--font-size-') || /^--type-.*-size$/.test(name)) {
        const value=styles.getPropertyValue(name).trim();
        if(/^\d*\.?\d+(px|rem|em)$/.test(value)) document.documentElement.style.setProperty(name, 'calc('+value+' * 2)');
      }
      document.documentElement.dataset.stressTextScaled='true';
    });
    await inspect(page,path,profile,errors);
    if(['/events','/settings','/my-team','/match-center','/scouting/coverage'].includes(path)) await page.screenshot({path:out+'/'+profile.name+'-sample-'+path.slice(1)+'.png',fullPage:true});
    if (['/events','/team-center','/scouting/coverage'].includes(path)) {
      const tabs=page.locator('main [role=tab]'); const count=await tabs.count();
      for(let i=0;i<Math.min(count,6);i++) {const tab=tabs.nth(i);if(await tab.isVisible()){await tab.click();await page.waitForTimeout(250);await inspect(page,path+'-tab-'+i,profile,errors);}}
    }
    if(path==='/my-team') {
      await page.getByRole('button',{name:'Leave workspace',exact:true}).click();
      await page.getByRole('dialog').waitFor();await inspect(page,'leave-dialog',profile,errors);
      await page.keyboard.press('Tab');await page.keyboard.press('Tab');await page.keyboard.press('Escape');
      await page.getByRole('dialog').waitFor({state:'hidden'});
    }
    console.log('Checked route',profile.name,path); if(watchdog) clearTimeout(watchdog);
  }
  // Exercise actual menus and dialogs, rather than only measuring resting pages.
  await page.goto(base+'/#/settings'); await page.waitForTimeout(500); errors=[];
  const themeSelect=page.getByRole('combobox',{name:'Theme',exact:true});
  await themeSelect.waitFor();
  for(let i=0;i<12;i++) await themeSelect.selectOption(i%2?'light':'dark');
  await themeSelect.selectOption(profile.theme);
  const density=page.getByRole('combobox',{name:'Density',exact:true});
  await density.selectOption('compact');await inspect(page,'compact-settings',profile,errors);await density.selectOption('comfortable');
  const refresh=page.getByRole('spinbutton',{name:/live refresh interval/i});
  if(await refresh.count()) {
    await refresh.fill('9999');await refresh.press('Tab');assert.equal(Number(await refresh.inputValue()),120);
    await refresh.fill('-999');await refresh.press('Tab');assert.equal(Number(await refresh.inputValue()),5);
    await refresh.fill('60');await refresh.press('Tab');
  }
  await inspect(page,'settings-interactions',profile,errors);
  const more=page.getByRole('button',{name:'More',exact:true});
  if(await more.isVisible()) {await more.click();await page.waitForTimeout(150);await inspect(page,'more-menu',profile,errors);await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});}
  // Navigation churn stresses cancellation and stale responses while keeping writes intercepted.
  await page.evaluate(async()=>{for(let i=0;i<40;i++){location.hash=['/events?tab=schedule','/team-center?tab=stats','/favorites','/settings'][i%4];await new Promise(resolve=>setTimeout(resolve,25));}});
  await page.getByRole('combobox',{name:'Theme',exact:true}).waitFor({timeout:8000}).catch(()=>errors.push('Rapid navigation did not reach Settings')); await inspect(page,'rapid-navigation',profile,errors);
  for(const state of ['errors','offline']) {
    mode=state; errors=[];
    await page.goto(base+'/#/events?event=2026nonexistent'+state+'&tab=schedule');await page.waitForTimeout(700);
    await inspect(page,state+'-events',profile,errors);
    await page.goto(base+'/#/my-team');await page.waitForTimeout(600);await inspect(page,state+'-workspace',profile,errors);
  }
  mode='normal'; await context.close(); console.log('Checked',profile.name);
 }
} finally {await browser.close();await writeFile(out+'/report.json',JSON.stringify({checked:results.length,failures,upstream,interceptedWrites,results},null,2));}
console.log(JSON.stringify({checked:results.length,failures:failures.length,publicRequests:upstream.length,writesIntercepted:interceptedWrites.length,report:out+'/report.json'}));
if(failures.length) process.exitCode=1;
