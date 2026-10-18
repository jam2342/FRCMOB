// A finished match drawn as a picture to share (1080×1350, the portrait size social apps show
// whole). Drawn straight on a canvas, so no screenshot library and nothing from the page leaks in.
// Always the dark brand look, whatever theme the app is in.

export type ShareCardMatch = {
  eventName: string;
  matchLabel: string;
  redScore: number;
  blueScore: number;
  red: number[];
  blue: number[];
  winner: 'red' | 'blue' | 'tie' | null;
  siteLabel: string;
};

const W = 1080;
const H = 1350;
const FONT = '"IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif';
const COLORS = {
  bg: '#151413',
  surface: '#252422',
  border: '#33322e',
  text: '#ececeb',
  muted: '#afaeaa',
  dim: '#5e5b55',
  accent: '#3cbb7e',
  red: '#fb9090',
  redFill: 'rgba(207, 92, 104, 0.30)',
  blue: '#60a5fa',
  blueFill: 'rgba(92, 141, 207, 0.30)',
};

function fitText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number, weight: number, startSize: number): number {
  let size = startSize;
  ctx.font = `${weight} ${size}px ${FONT}`;
  while (size > 20 && ctx.measureText(text).width > maxWidth) {
    size -= 2;
    ctx.font = `${weight} ${size}px ${FONT}`;
  }
  return size;
}

export function winnerLine(match: Pick<ShareCardMatch, 'winner'>): string {
  if (match.winner === 'red') return 'Red alliance wins';
  if (match.winner === 'blue') return 'Blue alliance wins';
  if (match.winner === 'tie') return 'Tied';
  return 'Final';
}

export async function renderShareCard(match: ShareCardMatch): Promise<Blob> {
  // The page's font may not be loaded at these weights yet; a fallback font would still draw.
  try {
    await Promise.all([document.fonts.load(`700 120px ${FONT}`), document.fonts.load(`500 40px ${FONT}`)]);
  } catch {
    // Fall back to the system font.
  }
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('This browser cannot draw pictures.');

  ctx.fillStyle = COLORS.bg;
  ctx.fillRect(0, 0, W, H);
  // The winner's colour washes in from the top-left corner.
  const wash = ctx.createLinearGradient(0, 0, W * 0.9, H * 0.6);
  wash.addColorStop(0, match.winner === 'blue' ? COLORS.blueFill : match.winner === 'red' ? COLORS.redFill : 'rgba(255,255,255,0.06)');
  wash.addColorStop(1, 'rgba(21, 20, 19, 0)');
  ctx.fillStyle = wash;
  ctx.fillRect(0, 0, W, H);

  const pad = 88;
  ctx.textBaseline = 'alphabetic';
  ctx.fillStyle = COLORS.muted;
  const eventSize = fitText(ctx, match.eventName.toUpperCase(), W - pad * 2, 600, 40);
  ctx.font = `600 ${eventSize}px ${FONT}`;
  ctx.fillText(match.eventName.toUpperCase(), pad, 170);
  ctx.fillStyle = COLORS.text;
  ctx.font = `700 64px ${FONT}`;
  ctx.fillText(match.matchLabel, pad, 250);

  // The score, winner in its alliance colour, loser dimmed.
  const redColor = match.winner === 'blue' ? COLORS.muted : COLORS.red;
  const blueColor = match.winner === 'red' ? COLORS.muted : COLORS.blue;
  const red = String(match.redScore);
  const blue = String(match.blueScore);
  const scoreSize = fitText(ctx, `${red} – ${blue}`, W - pad * 2, 700, 230);
  ctx.font = `700 ${scoreSize}px ${FONT}`;
  const dash = ' – ';
  const redW = ctx.measureText(red).width;
  const dashW = ctx.measureText(dash).width;
  const scoreY = 600;
  ctx.fillStyle = redColor;
  ctx.fillText(red, pad, scoreY);
  ctx.fillStyle = COLORS.dim;
  ctx.fillText(dash, pad + redW, scoreY);
  ctx.fillStyle = blueColor;
  ctx.fillText(blue, pad + redW + dashW, scoreY);

  ctx.fillStyle = COLORS.text;
  ctx.font = `600 54px ${FONT}`;
  ctx.fillText(winnerLine(match), pad, 700);

  // The two alliances.
  const boxY = 790;
  const boxH = 300;
  const gap = 32;
  const boxW = (W - pad * 2 - gap) / 2;
  const drawAlliance = (x: number, label: string, teams: number[], color: string) => {
    ctx.fillStyle = COLORS.surface;
    ctx.strokeStyle = COLORS.border;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(x, boxY, boxW, boxH, 28);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.font = `600 34px ${FONT}`;
    ctx.fillText(label, x + 36, boxY + 70);
    ctx.font = `700 52px ${FONT}`;
    teams.slice(0, 4).forEach((team, index) => {
      ctx.fillText(String(team), x + 36, boxY + 140 + index * 62);
    });
  };
  drawAlliance(pad, 'RED', match.red, COLORS.red);
  drawAlliance(pad + boxW + gap, 'BLUE', match.blue, COLORS.blue);

  // Footer: the wordmark and where to find it.
  ctx.strokeStyle = COLORS.border;
  ctx.beginPath();
  ctx.moveTo(pad, 1180);
  ctx.lineTo(W - pad, 1180);
  ctx.stroke();
  ctx.font = `700 46px ${FONT}`;
  ctx.fillStyle = COLORS.text;
  ctx.fillText('FRC', pad, 1262);
  const frcW = ctx.measureText('FRC').width;
  ctx.fillStyle = COLORS.accent;
  ctx.fillText('MOB', pad + frcW, 1262);
  ctx.font = `500 30px ${FONT}`;
  ctx.fillStyle = COLORS.muted;
  ctx.textAlign = 'right';
  ctx.fillText(match.siteLabel, W - pad, 1258);
  ctx.textAlign = 'left';

  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('Could not make the picture.'))), 'image/png');
  });
}
