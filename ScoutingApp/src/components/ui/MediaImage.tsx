import { useState, type ImgHTMLAttributes, type ReactNode } from 'react';

type MediaImageProps = ImgHTMLAttributes<HTMLImageElement> & {
  src: string;
  // Shown instead when the image can't be loaded (team media often points at
  // hosts that have since deleted it).
  fallback: ReactNode;
};

export function MediaImage({ src, fallback, ...imgProps }: MediaImageProps) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  if (failedSrc === src) return <>{fallback}</>;
  return <img {...imgProps} src={src} onError={() => setFailedSrc(src)} />;
}
