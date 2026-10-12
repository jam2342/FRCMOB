// Real scrolling geometry and touch gestures, with API traffic isolated by fixtures.
import assert from 'node:assert/strict';
import { chromium, webkit } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
const base = process.env.SCOUT_TEST_URL || 'http://127.0.0.1:4187';
assert.ok(['localhost', '127.0.0.1'].includes(new URL(base).hostname));
const out = process.env.SCOUT_TEST_OUTPUT || '/tmp/frcmob-scout-scroll-verified';
await mkdir(out, { recursive: true });
const engine = process.env.SCOUT_TEST_BROWSER === 'webkit' ? 'webkit' : 'chrome';
const browser = await (engine === 'webkit' ? webkit : chromium).launch(engine === 'chrome' ? { channel: 'chrome' } : {});
const profiles = [
 { name: '250-dark', width: 250, height: 450, theme: 'dark' },
 { name: '390-light', width: 390, height: 844, theme: 'light' },
 { name: 'desktop-light', width: 1366, height: 900, theme: 'light' },
];
const selected = process.env.SCOUT_TEST_PROFILES ? profiles.filter(p => process.env.SCOUT_TEST_PROFILES.split(',').includes(p.name)) : profiles;
assert.ok(selected.length);
const results = [], errors = [], writes = [];
const workspace = { id: 9002, name: 'Disposable scout workspace', frc_team_number: 254 };
const session = { token: 'local-scout-fixture', expiresAt: Date.now() / 1000 + 3600, workspace, me: { id: 9002, display_name: 'Fixture Scout', role: 'member' } };
const scoutRoute = '/scouting?event=2026test&match=2026test_qm1&team=frc254';
const draftKey = 'frcmob_scout_draft_v1:w9002:2026test_qm1:frc254';
const entriesKey = 'scouting_manual_entries_v2:w9002';
async function check(page, profile, label, verify) {
  try { await verify(); results.push({ profile, label, passed: true }); }
  catch (error) {
    results.push({ profile, label, passed: false, error: error.message });
    await page.screenshot({ path: `${out}/${profile}-${label}-failed.png`, fullPage: true });
    throw error;
  }
}
try {
  for (const profile of selected) {
    const context = await browser.newContext({ viewport: { width: profile.width, height: profile.height }, serviceWorkers: 'block', hasTouch: true, reducedMotion: process.env.SCOUT_TEST_REDUCED_MOTION || 'no-preference' });
    await context.addInitScript(({ session, theme }) => {
      localStorage.setItem('frcmob_tour_prompt_dismissed_v1', '1');
      localStorage.setItem('scouting_tutorial_autoplay', 'false');
      localStorage.setItem('scouting_theme_mode', theme);
      if (!localStorage.getItem('frcmob_workspace_session_v1')) localStorage.setItem('frcmob_workspace_session_v1', JSON.stringify(session));
    }, { session, theme: profile.theme });
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.pathname.startsWith('/_vercel/')) return route.fulfill({ contentType: 'application/javascript', body: '' });
      const api = url.pathname.startsWith('/api/') || (url.hostname === 'localhost' && url.port === '8000');
      if (!api) return url.origin === new URL(base).origin && request.method() === 'GET' ? route.continue() : route.abort();
      const path = url.pathname.replace(/^\/api/, '');
      if (request.method() !== 'GET') { writes.push({ path, method: request.method() }); return route.fulfill({ status: 409, json: { detail: 'Mutation blocked by local fixture' } }); }
      if (path === '/health') return route.fulfill({ json: { ok: true } });
      if (path === '/workspaces/me') return route.fulfill({ json: { ok: true, workspace, me: session.me, members: [session.me] } });
      if (path === '/events/suggested' || path === '/events/search') return route.fulfill({ json: { ok: true, events: [{ event_key: '2026test', name: 'Local scout event', year: 2026, start_date: '2026-10-01', end_date: '2026-10-03' }] } });
      if (path.includes('/schedule')) return route.fulfill({ json: {
        ok: true, event_key: '2026test', event_name: 'Local scout event', published: true, times_published: false, count: 2,
        matches: [1, 2].map(n => ({ match_key: `2026test_qm${n}`, display_name: `QM ${n}`, comp_level: 'qm', match_number: n, set_number: 1, scheduled_time: null, has_time: false,
          red: [254, 1678, 1323].map((n, i) => ({ team_key: 'frc' + n, team_number: n, nickname: 'Fixture robot', station: 'Red ' + (i + 1) })),
          blue: [1114, 2056, 118].map((n, i) => ({ team_key: 'frc' + n, team_number: n, nickname: 'Fixture robot', station: 'Blue ' + (i + 1) })),
        })),
      } });
      if (path.startsWith('/teams/event/')) return route.fulfill({ json: { ok: true, teams: [] } });
      if (path.startsWith('/teams/')) return route.fulfill({ json: {} });
      return route.fulfill({ status: 404, json: { detail: 'No local fixture data' } });
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push({ profile: profile.name, error: error.message }));
    page.on('dialog', dialog => dialog.dismiss());
    await page.goto(base + '/#' + scoutRoute);
    await page.locator('.scouting-layout-grid').waitFor();
    const snapshot = async label => {
      await page.evaluate(() => { document.scrollingElement.scrollTo(0,0); document.body.scrollTo(0,0); });
      await page.screenshot({ path: `${out}/${profile.name}-${label}.png`, fullPage: true });
      const size = await page.evaluate(() => ({viewport:innerWidth, scroll:document.documentElement.scrollWidth}));
      assert.ok(size.scroll <= size.viewport, `${label} overflows ${JSON.stringify(size)}`);
      results.push({profile:profile.name, label, overflow:size, passed:true});
    };
    if (profile.width > 1120) {
      assert.equal(await page.getByLabel('Match',{exact:true}).evaluate(el=>el.tagName),'SELECT');
      assert.equal(await page.getByLabel('Endgame result',{exact:true}).evaluate(el=>el.tagName),'SELECT');
      await snapshot('desktop'); await context.close(); continue;
    }

    const tab = name => page.getByRole('tab',{name,exact:true});
    await tab('Scout').click(); await tab('Teleop').click();
    const samples=[];
    await page.evaluate(()=>{window.__samples=[];window.__sample=()=>{const root=document.querySelector('.scouting-layout-grid');window.__samples.push({y:scrollY,body:document.body.scrollTop,height:document.documentElement.scrollHeight,nav:document.querySelector('.mobile-view-toggle').getBoundingClientRect().height,hero:document.querySelector('.fm-scout-hero').getBoundingClientRect().height,classes:root.className})};window.addEventListener('scroll',window.__sample);});
    await page.mouse.move(profile.width/2,profile.height*0.65);
    for(const dy of [80,100,100,100,100,-20,-20,-20,-80,-100]) {await page.mouse.wheel(0,dy);await page.waitForTimeout(90);if(samples.length===1){const transforms=await page.evaluate(()=>[getComputedStyle(document.querySelector('.mobile-view-toggle')).transform,getComputedStyle(document.querySelector('.fm-scout-hero')).transform]);assert.equal(transforms[0],transforms[1],'Header sections separated during the collapse animation');}await page.waitForTimeout(260);samples.push(await page.evaluate(()=>({y:scrollY,body:document.body.scrollTop,height:document.documentElement.scrollHeight,nav:document.querySelector('.mobile-view-toggle').getBoundingClientRect().height,hero:document.querySelector('.fm-scout-hero').getBoundingClientRect().height,hidden:document.querySelector('.mobile-view-toggle').inert})));if([5,9].includes(samples.length)) await page.screenshot({path:`${out}/${profile.name}-${samples.length===5?'collapsed':'expanded'}.png`});}
    await writeFile(`${out}/${profile.name}-scroll.json`,JSON.stringify({samples,events:await page.evaluate(()=>window.__samples)},null,2));
    assert.ok(samples.every(sample => sample.height === samples[0].height), 'Scroll range changed during a swipe');
    assert.ok(samples.every(sample => sample.nav === samples[0].nav && sample.hero === samples[0].hero), 'Scouting header resized during scrolling');
    for (let i=1;i<samples.length;i++) {
      const expected=[100,100,100,100,-20,-20,-20,-80,-100][i-1];
      assert.ok(Math.abs(samples[i].y-samples[i-1].y-expected)<=1, 'Scroll moved beyond the requested gesture');
    }
    assert.equal(samples[0].hidden,false,'Header hid before its controls scrolled past');
    assert.equal(samples[4].hidden,true,'Downward scrolling did not collapse the controls');
    for(const i of [5,6,7]) assert.equal(samples[i].hidden,true,'Tiny upward movements expanded the controls');
    assert.equal(samples[8].hidden,false,'Deliberate upward scrolling did not restore the controls');
    const settled=await page.evaluate(()=>({y:scrollY,height:document.documentElement.scrollHeight}));
    await page.waitForTimeout(500);
    assert.deepEqual(await page.evaluate(()=>({y:scrollY,height:document.documentElement.scrollHeight})),settled,'Header animation moved the page after the gesture ended');
    await page.mouse.wheel(0,100);
    await page.waitForTimeout(250);
    assert.equal(await page.locator('.mobile-view-toggle').evaluate(el=>el.inert),true);
    for(let i=0;i<64;i++) {
      await page.evaluate(()=>window.scrollBy({top:-1,behavior:'instant'}));
      await page.waitForTimeout(20);
    }
    assert.equal(await page.locator('.mobile-view-toggle').evaluate(el=>el.inert),false,'Slow upward scrolling did not restore the controls');
    await page.waitForTimeout(200);
    if(engine==='chrome') {
      await tab('Robot').click();
      await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));
      await page.waitForTimeout(200);
      const rail=page.locator('.page-view-bar');
      const rect=await rail.boundingBox();
      assert.ok(rect && rect.y>=0);
      const cdp=await context.newCDPSession(page);
      const x=Math.min(profile.width-20,rect.x+rect.width/2),start=rect.y+rect.height/2;
      await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y:start}]});
      for(let i=1;i<=8;i++) {
        await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:Math.max(2,start-i*10)}]});
        await page.waitForTimeout(20);
      }
      await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
      await page.waitForTimeout(300);
      assert.ok(await page.evaluate(()=>scrollY)>15,'Vertical swipe starting on the horizontal tab strip was blocked');
      await cdp.detach();
    }
    assert.equal(errors.length,0);
    console.log(profile.name+': seamless collapse and stable forward/reverse scrolling'+(engine==='chrome'?' and vertical tab-strip swipes':'')+' passed');
    await context.close();
  }
} finally {await browser.close();}
