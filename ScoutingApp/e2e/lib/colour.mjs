/* sRGB -> Lab -> CIEDE2000. Used to decide whether swapping a hardcoded
   colour for a token is a change anyone can see. JND is about 2.3. */
function parse(v) {
  // Collapse whitespace but do not delete it: `color(srgb 0.29 0.36 0.43)`
  // separates its channels with spaces, so stripping them runs the numbers
  // together into one unparseable blob.
  v = String(v).trim().toLowerCase().replace(/\s+/g, ' ');
  let m = /^#([0-9a-f]{3,8})$/.exec(v);
  if (m) {
    let h = m[1];
    if (h.length === 3 || h.length === 4) h = h.split('').map((c) => c + c).join('');
    if (h.length === 6) return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16), 1];
    if (h.length === 8) return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16), parseInt(h.slice(6,8),16)/255];
    return null;
  }
  const nums = (s) => s.split(/[,/ ]+/).filter((x) => x !== '').map(parseFloat);
  m = /^color\( ?srgb ([^)]*)\)$/.exec(v);
  if (m) {
    const n = nums(m[1]);
    if (n.length < 3 || n.some(Number.isNaN)) return null;
    return [n[0]*255, n[1]*255, n[2]*255, n.length > 3 ? n[3] : 1];
  }
  m = /^rgba?\(([^)]*)\)$/.exec(v);
  if (m) {
    const raw = m[1];
    const n = nums(raw);
    if (n.length < 3 || n.some(Number.isNaN)) return null;
    let a = n.length > 3 ? n[3] : 1;
    if (a > 1) a /= 100;
    return [n[0], n[1], n[2], a];
  }
  return null;
}
const f = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
function lab(rgb) {
  const [r, g, b] = [f(rgb[0]), f(rgb[1]), f(rgb[2])];
  const X = (0.4124*r + 0.3576*g + 0.1805*b) / 0.95047;
  const Y = 0.2126*r + 0.7152*g + 0.0722*b;
  const Z = (0.0193*r + 0.1192*g + 0.9505*b) / 1.08883;
  const G = (t) => (t > 0.008856 ? Math.cbrt(t) : 7.787*t + 16/116);
  const [fx, fy, fz] = [G(X), G(Y), G(Z)];
  return [116*fy - 16, 500*(fx - fy), 200*(fy - fz)];
}
const rad = (d) => (d * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;
function de2000(c1, c2) {
  const [L1,a1,b1] = lab(c1), [L2,a2,b2] = lab(c2);
  const C1 = Math.hypot(a1,b1), C2 = Math.hypot(a2,b2), Cb = (C1+C2)/2;
  const G = Cb > 0 ? 0.5*(1 - Math.sqrt(Math.pow(Cb,7)/(Math.pow(Cb,7)+Math.pow(25,7)))) : 0;
  const a1p = (1+G)*a1, a2p = (1+G)*a2;
  const C1p = Math.hypot(a1p,b1), C2p = Math.hypot(a2p,b2);
  const h1p = (a1p || b1) ? ((deg(Math.atan2(b1,a1p)) % 360) + 360) % 360 : 0;
  const h2p = (a2p || b2) ? ((deg(Math.atan2(b2,a2p)) % 360) + 360) % 360 : 0;
  const dLp = L2-L1, dCp = C2p-C1p;
  let dhp = 0;
  if (C1p*C2p !== 0) { dhp = h2p-h1p; if (dhp > 180) dhp -= 360; else if (dhp < -180) dhp += 360; }
  const dHp = 2*Math.sqrt(C1p*C2p)*Math.sin(rad(dhp/2));
  const Lbp = (L1+L2)/2, Cbp = (C1p+C2p)/2;
  let hbp;
  if (C1p*C2p === 0) hbp = h1p+h2p;
  else if (Math.abs(h1p-h2p) <= 180) hbp = (h1p+h2p)/2;
  else if (h1p+h2p < 360) hbp = (h1p+h2p+360)/2;
  else hbp = (h1p+h2p-360)/2;
  const T = 1 - 0.17*Math.cos(rad(hbp-30)) + 0.24*Math.cos(rad(2*hbp))
            + 0.32*Math.cos(rad(3*hbp+6)) - 0.20*Math.cos(rad(4*hbp-63));
  const dTh = 30*Math.exp(-Math.pow((hbp-275)/25, 2));
  const Rc = Cbp > 0 ? 2*Math.sqrt(Math.pow(Cbp,7)/(Math.pow(Cbp,7)+Math.pow(25,7))) : 0;
  const Sl = 1 + (0.015*Math.pow(Lbp-50,2))/Math.sqrt(20+Math.pow(Lbp-50,2));
  const Sc = 1 + 0.045*Cbp;
  const Sh = 1 + 0.015*Cbp*T;
  const Rt = -Math.sin(rad(2*dTh))*Rc;
  return Math.sqrt(Math.pow(dLp/Sl,2) + Math.pow(dCp/Sc,2) + Math.pow(dHp/Sh,2) + Rt*(dCp/Sc)*(dHp/Sh));
}
export { parse, de2000 };
