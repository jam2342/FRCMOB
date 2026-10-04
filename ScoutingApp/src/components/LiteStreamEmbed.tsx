import { useState } from 'react';
import { PlayIcon } from './ui/Icons';
import './LiteStreamEmbed.css';

const IFRAME_ALLOW =
  'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';

function youtubeId(embedUrl: string): string | null {
  const match = embedUrl.match(/youtube(?:-nocookie)?\.com\/embed\/([A-Za-z0-9_-]{6,})/);
  return match ? match[1] : null;
}

function withAutoplay(embedUrl: string): string {
  try {
    const url = new URL(embedUrl);
    url.searchParams.set('autoplay', '1');
    return url.toString();
  } catch {
    return embedUrl;
  }
}

// A stream player costs ~1 MB of YouTube or Twitch scripts before anyone
// presses play, and most visits never do. Show the thumbnail and load the
// real player on the first tap.
export function LiteStreamEmbed({ src, title, className }: { src: string; title: string; className?: string }) {
  const [playing, setPlaying] = useState(false);
  if (playing) {
    return (
      <iframe
        className={className}
        title={title}
        src={withAutoplay(src)}
        allow={IFRAME_ALLOW}
        referrerPolicy="strict-origin-when-cross-origin"
        allowFullScreen
      />
    );
  }
  const id = youtubeId(src);
  return (
    <button
      type="button"
      className={`lite-stream ${className ?? ''}`.trim()}
      style={id ? { backgroundImage: `url(https://i.ytimg.com/vi/${id}/hqdefault.jpg)` } : undefined}
      onClick={() => setPlaying(true)}
      aria-label={`Play ${title}`}
    >
      <span className="lite-stream__play" aria-hidden="true">
        <PlayIcon />
      </span>
    </button>
  );
}
