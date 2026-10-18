// Pit scouting endpoints on top of the room mock (shapes from routes_pit_scouting.py).
export function installPit(srv) {
  srv.pitById = new Map(); let id = 1;
  const now = () => new Date().toISOString();
  srv.handleExtra = async (route, p, m, url, json) => {
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
      const k = body.event_key + '|' + body.team_key; const ex = srv.pitById.get(k) || { id: id++, event_key: body.event_key, team_key: body.team_key, scout_profile: srv.me.display_name, payload: {}, photos: [], photo_urls: [], created_at: now() };
      const path = `pit_photos/${body.event_key}/${body.team_key}/${Date.now()}.jpg`;
      ex.photos = [...ex.photos, path]; ex.photo_urls = [...(ex.photo_urls || []), body.image_base64]; ex.updated_at = now();
      srv.pitById.set(k, ex); return json({ ok: true, photo: path, entry: ex });
    }
    return undefined;
  };
}
