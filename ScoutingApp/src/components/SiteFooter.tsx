import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  AUTHOR_NAME,
  COPYRIGHT_YEAR,
  FIRST_TRADEMARK_NOTICE,
  FRC_EVENTS_API_URL,
  SOURCE_CODE_URL,
  TBA_CREDIT,
  TBA_URL,
  THIRD_PARTY_LICENSES_PATH,
  YOUTUBE_TERMS_URL,
} from '../pages/legalContent';
import { italicFirst } from './italicFirst';
import './SiteFooter.css';

function External({ href, children }: { href: string; children: ReactNode }) {
  return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
}

// On every page: the FRC Events API terms want their credit on each page that
// shows event data, YouTube wants its Terms linked wherever a video can play,
// and the AGPL wants network users offered the source.
export function SiteFooter() {
  return (
    <footer className="site-footer">
      <p className="site-footer__credits">
        <External href={FRC_EVENTS_API_URL}>Event Data provided by <i>FIRST</i></External>
        <External href={TBA_URL}>{TBA_CREDIT}</External>
      </p>
      <p className="site-footer__notice">{italicFirst(FIRST_TRADEMARK_NOTICE)}</p>
      <nav className="site-footer__links" aria-label="Legal">
        <Link to="/privacy">Privacy</Link>
        <Link to="/terms">Terms</Link>
        <External href={YOUTUBE_TERMS_URL}>YouTube Terms</External>
        <External href={SOURCE_CODE_URL}>Source code</External>
        <a href={THIRD_PARTY_LICENSES_PATH} target="_blank" rel="noopener">Licenses</a>
      </nav>
      <p className="site-footer__notice">© {COPYRIGHT_YEAR} {AUTHOR_NAME} and contributors. Free software under the GNU AGPL v3.0.</p>
    </footer>
  );
}
