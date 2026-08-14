import { useEffect, useMemo, useRef, useState } from 'react';
import { getTeamLogo } from '../api';

const logoCache = new Map<string, string | null>();
const inflight = new Map<string, Promise<string | null>>();

function normalizeTeamKey(teamKey: string): string {
  return String(teamKey || '').trim().toLowerCase();
}

function normalizeEventKey(eventKey?: string): string {
  return String(eventKey || '').trim().toLowerCase();
}

function buildCacheKey(teamKey: string, eventKey?: string): string {
  return `${normalizeTeamKey(teamKey)}|${normalizeEventKey(eventKey)}`;
}

async function resolveLogoUrl(teamKey: string, eventKey?: string): Promise<string | null> {
  try {
    const primary = await getTeamLogo(teamKey, eventKey);
    if (primary.available && primary.image_url) return primary.image_url;
  } catch {
    // try fallback path below
  }

  // If event-specific lookup missed, try generic team lookup once.
  if (eventKey) {
    try {
      const fallback = await getTeamLogo(teamKey);
      if (fallback.available && fallback.image_url) return fallback.image_url;
    } catch {
      return null;
    }
  }

  return null;
}

function fetchLogo(teamKey: string, eventKey?: string): Promise<string | null> {
  const cacheKey = buildCacheKey(teamKey, eventKey);
  const cached = logoCache.get(cacheKey);
  if (cached !== undefined) return Promise.resolve(cached);

  const existing = inflight.get(cacheKey);
  if (existing) return existing;

  const p = resolveLogoUrl(teamKey, eventKey)
    .then((url) => {
      logoCache.set(cacheKey, url);
      inflight.delete(cacheKey);
      return url;
    })
    .catch(() => {
      logoCache.set(cacheKey, null);
      inflight.delete(cacheKey);
      return null;
    });

  inflight.set(cacheKey, p);
  return p;
}

/* ------------------------------------------------------------------ */
/*  <TeamAvatar />                                                     */
/*  A tiny, lazy-loading team logo with a colored-circle fallback.     */
/*  Falls back gracefully when the image is unavailable or errors.     */
/* ------------------------------------------------------------------ */

type TeamAvatarProps = {
  teamKey: string;
  teamNumber: number;
  /** Optional event context for logo lookup */
  eventKey?: string;
  /** CSS pixel size (default 20) */
  size?: number;
  className?: string;
};

export function TeamAvatar({
  teamKey,
  teamNumber,
  eventKey,
  size = 20,
  className,
}: TeamAvatarProps) {
  const normalizedTeamKey = normalizeTeamKey(teamKey);
  const normalizedEventKey = normalizeEventKey(eventKey);
  const cacheKey = buildCacheKey(normalizedTeamKey, normalizedEventKey);
  const fallbackCacheKey = buildCacheKey(normalizedTeamKey);
  const cachedUrl = useMemo(() => {
    if (logoCache.has(cacheKey)) return logoCache.get(cacheKey) ?? null;
    if (normalizedEventKey && logoCache.has(fallbackCacheKey)) {
      return logoCache.get(fallbackCacheKey) ?? null;
    }
    return undefined;
  }, [cacheKey, fallbackCacheKey, normalizedEventKey]);
  const [resolvedUrls, setResolvedUrls] = useState<Record<string, string | null>>({});
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    if (cachedUrl !== undefined) {
      return () => {
        mountedRef.current = false;
      };
    }

    fetchLogo(normalizedTeamKey, normalizedEventKey || undefined).then((resolved) => {
      if (mountedRef.current) {
        setResolvedUrls((current) => {
          if (current[cacheKey] === resolved) return current;
          return {
            ...current,
            [cacheKey]: resolved,
          };
        });
      }
    });
    return () => {
      mountedRef.current = false;
    };
  }, [cacheKey, cachedUrl, normalizedEventKey, normalizedTeamKey]);

  const url = cachedUrl !== undefined ? cachedUrl : (resolvedUrls[cacheKey] ?? null);
  const loaded = cachedUrl !== undefined || Object.prototype.hasOwnProperty.call(resolvedUrls, cacheKey);

  const sizeStyle = { width: size, height: size, minWidth: size, minHeight: size };
  const cls = `team-avatar${className ? ` ${className}` : ''}`;

  if (!loaded) {
    // Placeholder while loading
    return (
      <span className={`${cls} team-avatar--loading`} style={sizeStyle} aria-hidden="true" />
    );
  }

  if (url) {
    return (
      <img
        className={cls}
        src={url}
        alt=""
        width={size}
        height={size}
        style={sizeStyle}
        loading="lazy"
        decoding="async"
        onError={(e) => {
          logoCache.set(cacheKey, null);
          setResolvedUrls((current) => ({
            ...current,
            [cacheKey]: null,
          }));
          (e.target as HTMLImageElement).style.display = 'none';
        }}
      />
    );
  }

  // No logo available — show a tiny initials circle
  const initials = String(teamNumber).slice(0, 2);
  return (
    <span
      className={`${cls} team-avatar--fallback`}
      style={{ ...sizeStyle, fontSize: size * 0.45 }}
      aria-hidden="true"
    >
      {initials}
    </span>
  );
}
