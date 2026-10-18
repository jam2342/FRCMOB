// The stream players the site may frame. The Content-Security-Policy's frame-src
// is built from this list (build/contentSecurityPolicy.ts), and pages only embed
// URLs on it; anything else (a custom TBA "iframe" webcast) gets a Watch link
// instead of a frame the browser would block.
export const EMBED_FRAME_ORIGINS = [
  'https://www.youtube.com',
  'https://www.youtube-nocookie.com',
  'https://player.twitch.tv',
] as const;

export function embeddableUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const origin = new URL(url).origin;
    return (EMBED_FRAME_ORIGINS as readonly string[]).includes(origin) ? url : null;
  } catch {
    return null;
  }
}
