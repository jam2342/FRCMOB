// Stateful mock of the private workspace and team-room API, shaped like the backend
// (routes_workspaces.py, routes_scouting_rooms.py). Public reads go to the real data.
export function makeServer(opts = {}) {
  const now = () => new Date().toISOString();
  const ws = { id: 7, name: 'QA Team', frc_team_number: 971, join_code_rotated_at: null, created_at: now() };
  const me = { id: 11, display_name: opts.name || 'Alice', role: opts.role || 'leader', joined_at: now(), last_seen_at: now() };
  const members = [me, { id: 12, display_name: 'Bob', role: 'member', joined_at: now(), last_seen_at: now() }];
  const roomKey = 'room-a1b2c3';
  const room = { room_key: roomKey, event_key: '2026arc', title: null, created_by: me.display_name, archived: false,
    created_at: now(), updated_at: now(), last_activity_at: now(), leader_scout_profile: me.display_name, leader_source: 'owner_present',
    presence: [{ scout_profile: me.display_name, connections: 1, first_connected_at: now(), last_connected_at: now(), last_seen_at: now() }],
    secondary_leader_scout_profiles: [], ws_path: `/scouting/rooms/${roomKey}/ws` };
  const entries = new Map(); // client_entry_id -> record
  let serverId = 1;
  const assignments = opts.assignments || [];
  const state = { ws, me, members, room, roomKey, entries, assignments, posts: [], writesFail: !!opts.writesFail, sockets: new Set(), picklists: [], pit: [] };
  const access = () => ({ room_role: 'owner', room_access_token: 'rtok-' + roomKey, expires_at: new Date(Date.now() + 86400e3).toISOString(), expires_at_unix: Math.floor(Date.now() / 1000) + 86400, ttl_sec: 86400, header: 'X-Scouting-Room-Access' });
  const teamSnap = () => ({ ok: true, room_key: roomKey, event_key: '2026arc', me: { member_id: me.id, display_name: me.display_name, role: me.role },
    members: members.map(m => ({ member_id: m.id, display_name: m.display_name, role: m.role })), assignments: state.assignments });
  const records = () => [...entries.values()];
  const wsState = () => ({ ok: true, room: { ...room, room_role: 'owner' }, entries: records(), assignments: [], my_assignments: [] });
  state.session = { token: 'wtok-qa', expiresAt: Math.floor(Date.now() / 1000) + 30 * 86400, workspace: { id: ws.id, name: ws.name, frc_team_number: ws.frc_team_number }, me: { id: me.id, display_name: me.display_name, role: me.role } };
  function persist(body) {
    const id = body.client_entry_id || body.entry?.id;
    const existing = entries.get(id);
    if (existing) {
      if ((body.entry.saved_at_ms || 0) <= (existing.entry.saved_at_ms || 0)) return [existing, false];
      existing.entry = { ...body.entry, id: existing.entry.id, scout_profile: me.display_name }; existing.updated_at = now();
      return [existing, false];
    }
    const e = body.entry;
    const rec = { server_id: serverId++, room_key: roomKey, event_key: e.event_key, match_key: e.match_key, team_key: e.team_key, scout_profile: me.display_name,
      client_entry_id: id, created_at: now(), updated_at: now(), entry: { ...e, scout_profile: me.display_name },
      scores: { total_points: e.points?.total ?? null, driver_score_0_100: null, manual_rating_0_100: null, scouting_api_rating_0_100: null, overall_scout_rating_0_100: null } };
    entries.set(id, rec); return [rec, true];
  }
  async function handle(route) {
    const req = route.request(); const url = new URL(req.url()); const p = url.pathname.replace(/^\/api/, ''); const m = req.method();
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }).then(() => true);
    const isWrite = m !== 'GET';
    if (state.offline) return route.abort('internetdisconnected');
    if (p.startsWith('/workspaces/me') && m === 'GET') return json({ ok: true, workspace: ws, me, members });
    if (p === '/scouting/rooms/team' && m === 'POST') { if (state.writesFail) return json({ detail: 'Forbidden' }, 403); return json({ ...teamSnap(), created: false, access: access() }); }
    if (p.startsWith('/scouting/rooms/team/') && m === 'GET') return json(teamSnap());
    if (p === '/scouting/rooms' && m === 'POST') { if (state.writesFail) return json({ detail: 'Forbidden' }, 403); return json({ ...wsState(), access: access() }); }
    const em = p.match(/^\/scouting\/rooms\/([^/]+)\/entries$/);
    if (em && m === 'POST') {
      const body = JSON.parse(req.postData() || '{}'); state.posts.push({ t: Date.now(), id: body.client_entry_id, ts: body.entry?.form?.teleop_scored, team: body.entry?.team_key, match: body.entry?.match_key, saved_at_ms: body.entry?.saved_at_ms });
      if (state.writesFail) return json({ detail: 'Forbidden' }, 403);
      if (state.slowMs) await new Promise(r => setTimeout(r, state.slowMs));
      const [rec, created] = persist(body);
      for (const s of state.sockets) try { s.send(JSON.stringify({ type: 'entry_saved', room_key: roomKey, created, entry: rec.entry, server_entry: rec, server_time: now() })); } catch {}
      return json({ ok: true, room_key: roomKey, created, entry: rec });
    }
    if (em && m === 'GET') return json({ ok: true, room_key: roomKey, entries: records() });
    const rm = p.match(/^\/scouting\/rooms\/([^/]+)$/);
    if (rm && m === 'GET') return json({ ...wsState(), access: access() });
    if (p.startsWith('/workspaces') || p.startsWith('/scouting/rooms') || p.startsWith('/picklists') || p.startsWith('/pit-scouting') || p.startsWith('/push')) {
      if (state.handleExtra) { const r = await state.handleExtra(route, p, m, url, json); if (r) return; }
      state.unmocked = (state.unmocked || []); state.unmocked.push(m + ' ' + p);
      return route.fallback();
    }
    return route.fallback();
  }
  state.setOffline = async (ctx, v) => { state.offline = v; await ctx.setOffline(v); };
  state.install = async (ctx) => {
    await ctx.addInitScript((s) => { try { localStorage.setItem('frcmob_workspace_session_v1', JSON.stringify(s)); } catch {} }, state.session);
    await ctx.route('**/api/**', handle);
    await ctx.routeWebSocket(/\/scouting\/rooms\/[^/]+\/ws/, (sock) => {
      state.sockets.add(sock);
      sock.onClose(() => state.sockets.delete(sock));
      sock.onMessage((msg) => { try { const d = JSON.parse(msg); if (d.type === 'ping') sock.send(JSON.stringify({ type: 'pong' })); } catch {} });
      setTimeout(() => { try { sock.send(JSON.stringify({ type: 'snapshot', room: { ...room, room_role: 'owner' }, entries: records(), assignments: [] })); } catch {} }, 50);
    });
  };
  state.snapshotAll = () => { for (const s of state.sockets) try { s.send(JSON.stringify({ type: 'snapshot', room: { ...room, room_role: 'owner' }, entries: records(), assignments: [] })); } catch {} };
  return state;
}
