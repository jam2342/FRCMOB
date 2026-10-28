import type { SeasonStoryEvent } from '../../api';
import { canvasPng, COLORS, drawFooter, fitText, FONT, H, loadCardFonts, W } from '../shareCard/renderShareCard';
import { bestMatchText, finishText, rankFigure, recordText } from './story';

export type RecapCard = {
  teamNumber: number;
  nickname: string | null;
  event: SeasonStoryEvent;
  siteLabel: string;
};

// One team's event, drawn to share: the finish, the record, and the numbers behind it. Same
// size and dark brand look as the match picture (renderShareCard.ts).
export async function renderRecapCard(card: RecapCard): Promise<Blob> {
  await loadCardFonts();
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('This browser cannot draw pictures.');

  const won = card.event.finish === 'won';
  ctx.fillStyle = COLORS.bg;
  ctx.fillRect(0, 0, W, H);
  const wash = ctx.createLinearGradient(0, 0, W * 0.9, H * 0.6);
  wash.addColorStop(0, won ? 'rgba(60, 187, 126, 0.28)' : 'rgba(255,255,255,0.06)');
  wash.addColorStop(1, 'rgba(21, 20, 19, 0)');
  ctx.fillStyle = wash;
  ctx.fillRect(0, 0, W, H);

  const pad = 88;
  ctx.textBaseline = 'alphabetic';
  ctx.fillStyle = COLORS.muted;
  const eventName = card.event.event_name.toUpperCase();
  ctx.font = `600 ${fitText(ctx, eventName, W - pad * 2, 600, 40)}px ${FONT}`;
  ctx.fillText(eventName, pad, 170);

  ctx.fillStyle = COLORS.text;
  const teamLine = `Team ${card.teamNumber}`;
  ctx.font = `700 ${fitText(ctx, teamLine, W - pad * 2, 700, 96)}px ${FONT}`;
  ctx.fillText(teamLine, pad, 280);
  if (card.nickname) {
    ctx.fillStyle = COLORS.muted;
    ctx.font = `500 ${fitText(ctx, card.nickname, W - pad * 2, 500, 44)}px ${FONT}`;
    ctx.fillText(card.nickname, pad, 345);
  }

  const finish = finishText(card.event);
  ctx.fillStyle = won ? COLORS.accent : COLORS.text;
  ctx.font = `700 ${fitText(ctx, finish, W - pad * 2, 700, 92)}px ${FONT}`;
  ctx.fillText(finish, pad, 500);

  ctx.fillStyle = COLORS.text;
  ctx.font = `700 ${fitText(ctx, recordText(card.event.record), W - pad * 2, 700, 230)}px ${FONT}`;
  ctx.fillText(recordText(card.event.record), pad, 740);
  ctx.fillStyle = COLORS.muted;
  ctx.font = `500 40px ${FONT}`;
  ctx.fillText('wins – losses' + (card.event.record.ties ? ' – ties' : ''), pad, 800);

  // Up to three figures in a row of boxes.
  const figures = [
    ['QUAL RANK', rankFigure(card.event)],
    ['RATING', card.event.rating_0_100 != null ? card.event.rating_0_100.toFixed(1) : null],
    ['BEST MATCH', bestMatchText(card.event.best_match)],
  ].filter((row): row is [string, string] => Boolean(row[1]));
  const boxY = 880;
  const boxH = 220;
  const gap = 28;
  const boxW = figures.length ? (W - pad * 2 - gap * (figures.length - 1)) / figures.length : 0;
  figures.forEach(([label, value], i) => {
    const x = pad + i * (boxW + gap);
    ctx.fillStyle = COLORS.surface;
    ctx.strokeStyle = COLORS.border;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(x, boxY, boxW, boxH, 28);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = COLORS.muted;
    ctx.font = `600 28px ${FONT}`;
    ctx.fillText(label, x + 32, boxY + 62);
    ctx.fillStyle = COLORS.text;
    ctx.font = `700 ${fitText(ctx, value, boxW - 64, 700, 60)}px ${FONT}`;
    ctx.fillText(value, x + 32, boxY + 150);
  });

  drawFooter(ctx, pad, card.siteLabel);
  return canvasPng(canvas);
}
