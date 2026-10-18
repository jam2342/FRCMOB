// Single source of truth for the legal pages and the site footer so Privacy,
// Terms and the per-page credits can't drift apart. Contact is the public
// mirror's issue tracker: the main repo is private, so its issues 404 for
// visitors.
export const LEGAL_EFFECTIVE_DATE = 'October 18, 2026';
export const COPYRIGHT_YEAR = 2026;
export const SOURCE_CODE_URL = 'https://github.com/jam2342/FRCMOB';
export const LEGAL_CONTACT_URL = `${SOURCE_CODE_URL}/issues`;
export const LEGAL_CONTACT_LABEL = 'open an issue on GitHub';
export const AUTHOR_NAME = 'Jamal Mammadzada';
export const AUTHOR_SITE_URL = 'https://portfolio-wine-three-iox2jn0bx6.vercel.app/';

// Vite writes the bundled packages' licenses here at build time (vite.config.ts).
export const THIRD_PARTY_LICENSES_PATH = '/third-party-licenses.txt';
export const FONT_LICENSE_PATH = '/fonts/OFL.txt';

// FIRST's IP policy wants each mark named, its owner's legal name, and this
// exact "not overseeing" wording. ® for USPTO-registered marks, ™ for REBUILT
// (Attachment A of the policy, rev. 2025-04-19).
export const FIRST_URL = 'https://www.firstinspires.org';
export const FIRST_TRADEMARK_NOTICE =
  'FIRST®, FIRST® Robotics Competition, FRC®, Gracious Professionalism®, and REBUILT™ are trademarks of ' +
  'For Inspiration and Recognition of Science and Technology (FIRST®), which is not overseeing, involved with, ' +
  'or responsible for this activity, product, or service.';

// FRC Events API terms: a conspicuous link with this exact text on every web
// page that shows event data, pointing at the API page.
export const FRC_EVENTS_CREDIT = 'Event Data provided by FIRST';
export const FRC_EVENTS_API_URL = 'https://frc-events.firstinspires.org/services/API';
export const FRC_EVENTS_TERMS_URL = 'https://frc-events.firstinspires.org/services/api/terms';

// The Blue Alliance developer guidelines ask for this with a link back.
export const TBA_CREDIT = 'Powered by The Blue Alliance';
export const TBA_URL = 'https://www.thebluealliance.com';

// YouTube's developer policies count the embedded player as YouTube API
// Services: link its Terms everywhere a video can play, and the Google
// Privacy Policy from ours.
export const YOUTUBE_TERMS_URL = 'https://www.youtube.com/t/terms';
export const GOOGLE_PRIVACY_URL = 'https://policies.google.com/privacy';
export const TWITCH_TERMS_URL = 'https://www.twitch.tv/p/legal/terms-of-service/';
export const TWITCH_PRIVACY_URL = 'https://www.twitch.tv/p/legal/privacy-notice/';
export const CLOUDFLARE_PRIVACY_URL = 'https://www.cloudflare.com/privacypolicy/';
export const TURNSTILE_PRIVACY_URL = 'https://www.cloudflare.com/turnstile-privacy-policy/';
export const VERCEL_ANALYTICS_PRIVACY_URL = 'https://vercel.com/docs/analytics/privacy-policy';
export const LICENSE_URL = `${SOURCE_CODE_URL}/blob/main/LICENSE`;
