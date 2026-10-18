// Picklist endpoints on top of the room mock (shapes from routes_picklists.py).
export function installPick(srv, teams = ['frc971','frc254','frc1678','frc27','frc103','frc131','frc167','frc548','frc587','frc1114']) {
  const now = () => new Date().toISOString();
  const slot = (t, i) => ({ team_key: t, tier: i < 8 ? 'first' : 'second', status: 'available', picked_by_alliance: null, dnp_reason: '', notes: '' });
  srv.pick = { id: 41, event_key: '2026arc', title: 'Picklist QA', created_by: 'Alice', slots: teams.map(slot), version: 3, live_mode: false, archived: false, created_at: now(), updated_at: now() };
  srv.puts = [];
  const prev = srv.handleExtra;
  srv.handleExtra = async (route, p, m, url, json) => {
    if (p === '/picklists' && m === 'GET') return json({ ok: true, picklists: srv.pick ? [srv.pick] : [] });
    if (p === `/picklists/${srv.pick?.id}` && m === 'GET') return json({ ok: true, picklist: srv.pick });
    if (p === `/picklists/${srv.pick?.id}` && m === 'PUT') {
      const body = JSON.parse(route.request().postData() || '{}'); srv.puts.push({ t: Date.now(), version: body.version, order: body.slots?.map(s => s.team_key.slice(3)).join(',') });
      if (srv.writesFail) return json({ detail: 'Forbidden' }, 403);
      if (body.version !== srv.pick.version) return json({ ok: true, conflict: true, detail: 'Picklist was updated by someone else.', picklist: srv.pick });
      srv.pick = { ...srv.pick, ...(body.slots ? { slots: body.slots } : {}), ...(body.title ? { title: body.title } : {}), ...(typeof body.live_mode === 'boolean' ? { live_mode: body.live_mode } : {}), version: srv.pick.version + 1, updated_at: now() };
      return json({ ok: true, picklist: srv.pick });
    }
    if (p === `/picklists/${srv.pick?.id}` && m === 'DELETE') { srv.deleted = true; srv.pick = null; return json({ ok: true }); }
    if (prev) return prev(route, p, m, url, json);
    return undefined;
  };
}
