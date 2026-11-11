// Pit scouting endpoints on top of the room mock (shapes from routes_pit_scouting.py).
export function installPit(srv) {
  srv.pitById = new Map(); let id = 1;
  srv.pitAssignments = new Map(); // `${event}|${team}` -> row, shapes from routes_pit_scouting.py
  const now = () => new Date().toISOString();
  srv.handleExtra = async (route, p, m, url, json) => {
    if (p.startsWith('/pit-scouting/assignments')) {
      const memberName = (mid) => srv.members.find(x => x.id === mid)?.display_name ?? null;
      const row = (r) => ({ team_key: r.team_key, member_id: r.member_id, member_name: memberName(r.member_id), source: r.source, assigned_by_member_id: r.by, updated_at: r.updated_at });
      const list = (event) => [...srv.pitAssignments.values()].filter(r => r.event_key === event).sort((a, b) => Number(a.team_key.slice(3)) - Number(b.team_key.slice(3))).map(row);
      if (p === '/pit-scouting/assignments' && m === 'GET') return json({ ok: true, assignments: list(url.searchParams.get('event_key')) });
      if (m !== 'POST') return undefined;
      const body = JSON.parse(route.request().postData() || '{}'); srv.posts.push({ pitAssignment: p.split('/').pop(), body, t: Date.now() });
      if (srv.writesFail) return json({ detail: 'Forbidden' }, 403);
      const key = body.event_key + '|' + body.team_key; const held = srv.pitAssignments.get(key);
      const leader = srv.me.role === 'leader';
      if (p.endsWith('/claim')) {
        if (body.force && !leader) return json({ detail: 'Only workspace leaders can do that.' }, 403);
        if (held && held.member_id !== srv.me.id && !body.force) return json({ detail: `${body.team_key} is already taken by ${memberName(held.member_id)}.` }, 409);
        const r = { event_key: body.event_key, team_key: body.team_key, member_id: srv.me.id, source: 'claim', by: srv.me.id, updated_at: now() };
        srv.pitAssignments.set(key, r); return json({ ok: true, assignment: row(r) });
      }
      if (p.endsWith('/release')) {
        if (!held) return json({ ok: true, released: false });
        if (held.member_id !== srv.me.id && !leader) return json({ detail: 'Only the scout who took this team, or a leader, can release it.' }, 403);
        srv.pitAssignments.delete(key); return json({ ok: true, released: true });
      }
      if (p.endsWith('/set')) {
        if (!leader) return json({ detail: 'Only workspace leaders can do that.' }, 403);
        if (body.member_id == null) { srv.pitAssignments.delete(key); return json({ ok: true, assignment: null }); }
        const r = { event_key: body.event_key, team_key: body.team_key, member_id: body.member_id, source: 'leader', by: srv.me.id, updated_at: now() };
        srv.pitAssignments.set(key, r); return json({ ok: true, assignment: row(r) });
      }
      if (p.endsWith('/auto-split')) {
        if (!leader) return json({ detail: 'Only workspace leaders can do that.' }, 403);
        // Scouted means answers; a photo-only row is still to do.
        const scouted = new Set([...srv.pitById.values()].filter(e => e.event_key === body.event_key && Object.keys(e.payload || {}).length).map(e => e.team_key));
        const ids = [...new Set(body.member_ids)].sort((a, b) => a - b);
        const eligible = [...new Set(body.team_keys)].filter(t => !(body.only_unscouted && scouted.has(t)) && !(srv.pitAssignments.get(body.event_key + '|' + t)?.member_id != null && !body.replace_existing));
        eligible.forEach((t, i) => srv.pitAssignments.set(body.event_key + '|' + t, { event_key: body.event_key, team_key: t, member_id: ids[i % ids.length], source: 'auto', by: srv.me.id, updated_at: now() }));
        return json({ ok: true, assignments: list(body.event_key) });
      }
      return undefined;
    }
    if (p === '/pit-scouting' && m === 'GET') return json({ ok: true, entries: [...srv.pitById.values()].filter(e => e.event_key === url.searchParams.get('event_key')) });
    if (p === '/pit-scouting' && m === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}'); srv.posts.push({ pit: true, body, t: Date.now() });
      if (srv.writesFail) return json({ detail: 'Forbidden' }, 403);
      const k = body.event_key + '|' + body.team_key; const ex = srv.pitById.get(k);
      const entry = { id: ex?.id ?? id++, event_key: body.event_key, team_key: body.team_key, scout_profile: body.scout_profile || srv.me.display_name, payload: body.payload, photos: ex?.photos ?? [], photo_urls: ex?.photo_urls ?? [], created_at: ex?.created_at ?? now(), updated_at: now() };
      srv.pitById.set(k, entry); return json({ ok: true, entry });
    }
    if (p === '/pit-scouting/photo' && m === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}'); srv.posts.push({ photo: true, len: body.image_base64?.length });
      if (srv.writesFail) return json({ detail: 'Forbidden' }, 403);
      if (srv.refusePhotos?.has(body.team_key)) return json({ detail: 'Photo limit reached (12 per team). Delete one first.' }, 400);
      const k = body.event_key + '|' + body.team_key; const ex = srv.pitById.get(k) || { id: id++, event_key: body.event_key, team_key: body.team_key, scout_profile: srv.me.display_name, payload: {}, photos: [], photo_urls: [], created_at: now() };
      const path = `pit_photos/${body.event_key}/${body.team_key}/${Date.now()}.jpg`;
      ex.photos = [...ex.photos, path]; ex.photo_urls = [...(ex.photo_urls || []), body.image_base64]; ex.updated_at = now();
      srv.pitById.set(k, ex); return json({ ok: true, photo: path, entry: ex });
    }
    return undefined;
  };
}
