// The FRCMOB mark, drawn by hand on a 512 grid: an M split into the red and blue
// alliances with a small robot between them, arms up. Every icon, splash and the
// sidebar wordmark come from these shapes (scripts/brand-assets.mjs renders them).
export const COLORS = { bg: '#151413', red: '#cf5c68', blue: '#5c8dcf', green: '#5fd3a6', ink: '#f2efe9' };

const LEFT = [[118, 384], [118, 136], [182, 136], [256, 236], [256, 322], [182, 222], [182, 384]];
const mirror = (pts) => pts.map(([x, y]) => [512 - x, y]);
const points = (pts) => pts.map(([x, y]) => `${x},${y}`).join(' ');
// The glyph's box runs y 53–384; this lifts it to the optical centre of the square.
const CENTER_Y = 28;

function robot(fill, eyes) {
  const head = `<rect x="210" y="104" width="92" height="70" rx="20" fill="${fill}"/>`
    + `<rect x="250" y="70" width="12" height="36" fill="${fill}"/><circle cx="256" cy="66" r="13" fill="${fill}"/>`;
  return eyes ? head + `<circle cx="236" cy="139" r="10" fill="${eyes}"/><circle cx="276" cy="139" r="10" fill="${eyes}"/>` : head;
}

export function glyph({ red = COLORS.red, blue = COLORS.blue, green = COLORS.green, eyes = COLORS.bg } = {}) {
  return `<g transform="translate(0 ${CENTER_Y})"><polygon points="${points(LEFT)}" fill="${red}"/>`
    + `<polygon points="${points(mirror(LEFT))}" fill="${blue}"/>${robot(green, eyes)}</g>`;
}

// One-colour silhouette with the eyes punched through (Android notification badge).
export function silhouette(fill = '#ffffff') {
  const halves = `<polygon points="${points(LEFT)}" fill="${fill}" transform="translate(-6 0)"/>`
    + `<polygon points="${points(mirror(LEFT))}" fill="${fill}" transform="translate(6 0)"/>`;
  return `<defs><mask id="eyes"><rect width="512" height="512" fill="#fff"/>`
    + `<circle cx="236" cy="139" r="10" fill="#000"/><circle cx="276" cy="139" r="10" fill="#000"/></mask></defs>`
    + `<g transform="translate(0 ${CENTER_Y})">${halves}<g mask="url(#eyes)">${robot(fill)}</g></g>`;
}

const scaled = (inner, scale) => {
  const offset = (512 - 512 * scale) / 2;
  return `<g transform="translate(${offset} ${offset}) scale(${scale})">${inner}</g>`;
};

export const marks = {
  // Rounded tile: favicon, sidebar, PWA "any" icons.
  tile: () => `<rect width="512" height="512" rx="112" fill="${COLORS.bg}"/>${glyph()}`,
  // Full-bleed square; iOS and maskable PWA icons get their corners from the OS.
  square: () => `<rect width="512" height="512" fill="${COLORS.bg}"/>${scaled(glyph(), 0.8)}`,
  round: () => `<circle cx="256" cy="256" r="256" fill="${COLORS.bg}"/>${scaled(glyph(), 0.78)}`,
  // Android adaptive foreground: transparent, inside the 66/108 safe circle.
  adaptiveForeground: () => scaled(glyph(), 0.72),
  badge: () => scaled(silhouette(), 0.92),
};

export const svg = (inner, width = 512, height = width, viewBox = '0 0 512 512') =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" width="${width}" height="${height}">${inner}</svg>`;

