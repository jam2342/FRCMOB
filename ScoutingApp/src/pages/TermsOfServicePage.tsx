import { Link } from 'react-router-dom';
import { SurfaceCard } from '../components/ui/SurfaceCard';
import { italicFirst } from '../components/italicFirst';
import {
  AUTHOR_NAME,
  AUTHOR_SITE_URL,
  FIRST_TRADEMARK_NOTICE,
  FONT_LICENSE_PATH,
  FRC_EVENTS_API_URL,
  FRC_EVENTS_TERMS_URL,
  LEGAL_CONTACT_LABEL,
  LEGAL_CONTACT_URL,
  LEGAL_EFFECTIVE_DATE,
  LICENSE_URL,
  SOURCE_CODE_URL,
  TBA_URL,
  THIRD_PARTY_LICENSES_PATH,
  TWITCH_TERMS_URL,
  YOUTUBE_TERMS_URL,
} from './legalContent';
import './LegalPage.css';

export function TermsOfServicePage() {
  return (
    <div className="center-page-container narrow legal-page">

        <SurfaceCard
          title="Terms of Service"
          subtitle="The rules for using FRCMOB."
          expandable={false}
          mobileCollapsible={false}
        >
          <div className="legal-meta">
            <span className="center-chip">Effective {LEGAL_EFFECTIVE_DATE}</span>
            <Link className="center-btn ghost" to="/privacy">
              Privacy Policy
            </Link>
          </div>

          <p className="legal-intro">
            FRCMOB is a scouting and match-analysis tool for <i>FIRST</i>&reg; Robotics Competition
            teams and fans. By using it, you agree to these terms. If you do not agree, please do not
            use the app.
          </p>

          <section>
            <h4>1. What FRCMOB is</h4>
            <p>
              FRCMOB collects scouting observations, ingests public FRC data, breaks down match
              recordings on the scout&apos;s own phone, and produces team ratings, picklists, and match
              predictions. It is a decision-support tool for competition strategy. It is an independent
              project, free of charge, and is not affiliated with, endorsed by, sponsored by, or
              approved by <i>FIRST</i>, The Blue Alliance, YouTube, Twitch, or any other service it
              draws on.
            </p>
          </section>

          <section>
            <h4>2. Who may use it</h4>
            <p>
              FRCMOB is intended for FRC teams, their students, mentors, and coaches, and for fans who
              follow the competition. There are no accounts: a team&apos;s private data lives in a team
              workspace, and access is controlled by the team lead who shares its join code. If you are under 18, use FRCMOB with the awareness and permission of your
              team&apos;s mentor or coach.
            </p>
          </section>

          <section>
            <h4>3. Acceptable use</h4>
            <p>You agree not to:</p>
            <ul>
              <li>
                Enter harassing, defamatory, or abusive content about any team, student, mentor,
                volunteer, or referee. Scout robots, not people.
              </li>
              <li>Upload photos of people without their permission, or any image of a minor.</li>
              <li>
                Deliberately submit false scouting data, tamper with another team&apos;s picklists or
                entries, or disrupt scouting rooms you were invited to.
              </li>
              <li>
                Attempt to break, overload, scrape at abusive volume, or gain unauthorized access to
                the app, its API, or its infrastructure.
              </li>
              <li>Use the app in a way that violates FIRST rules, event rules, or venue policy.</li>
              <li>Upload malware, or content you do not have the right to upload.</li>
            </ul>
            <p>
              We may remove content or restrict access if these rules are broken. Where a team
              workspace has leaders or co-leaders, they may manage assignments, members, and content
              within their own rooms.
            </p>
          </section>

          <section>
            <h4>4. Your content</h4>
            <p>
              You keep ownership of the scouting data, photos, and notes you submit. By submitting
              them you grant us permission to store, process, display, and analyze that content in
              order to operate the app — including computing ratings, predictions, and aggregate
              analytics, and sharing it with others in your event workspace or scouting room.
            </p>
            <p>
              Derived numeric data from phone recordings and scouting entries may be used to improve the
              models that power the app. Recorded video stays on your device and is never uploaded; only
              the robot positions it produces are synced, and your team&apos;s operator reviews them
              before they appear anywhere public.
            </p>
            <p>
              You are responsible for having the right to upload what you upload, and for the accuracy
              of what you enter.
            </p>
          </section>

          <section>
            <h4>5. Video, recording, and event rules</h4>
            <p>
              If you record a match with FRCMOB, you are responsible for complying with the
              event&apos;s and venue&apos;s recording policies. The recording is processed on your own
              device.
            </p>
            <p>
              Match videos play in players embedded from YouTube and Twitch. FRCMOB uses YouTube API
              Services to show YouTube videos; by watching YouTube videos in FRCMOB you agree to be
              bound by the{' '}
              <a href={YOUTUBE_TERMS_URL} target="_blank" rel="noopener noreferrer">YouTube Terms of Service</a>.
              Twitch streams are subject to the{' '}
              <a href={TWITCH_TERMS_URL} target="_blank" rel="noopener noreferrer">Twitch Terms of Service</a>.
              The videos belong to their owners; FRCMOB does not host or download them.
            </p>
          </section>

          <section>
            <h4>6. Accuracy — read this one</h4>
            <p>
              Ratings, alliance outlooks, pick values, picklist ordering, and match
              predictions are <strong>estimates produced by statistical and machine-learning
              models</strong>. Machine-generated scouting fields are drafts carrying a confidence
              score and require human review before they become saved entries. On-device detection from
              a phone recording is imperfect: robots get occluded, bumper numbers are misread, camera
              angles distort positions, and public data sources go down or return wrong values.
            </p>
            <p>
              Do not treat any output as fact. Verify anything that matters before you act on it in
              alliance selection or match strategy. Competition decisions you make using FRCMOB are
              yours alone.
            </p>
          </section>

          <section>
            <h4>7. Availability</h4>
            <p>
              FRCMOB is provided as-is with no uptime guarantee. It depends on third-party services
              that fail on their own schedule, and venue wifi is famously unreliable. Features may
              change or be removed. Offline mode exists precisely because connectivity at events is
              not dependable — plan for the app to be unavailable at the worst possible moment, and
              keep a paper backup for anything critical.
            </p>
          </section>

          <section>
            <h4>8. Disclaimer of warranties</h4>
            <p>
              The app is provided &ldquo;as is&rdquo; and &ldquo;as available&rdquo;, without
              warranties of any kind, express or implied, including merchantability, fitness for a
              particular purpose, accuracy, and non-infringement.
            </p>
          </section>

          <section>
            <h4>9. Limitation of liability</h4>
            <p>
              To the fullest extent permitted by law, we are not liable for any indirect, incidental,
              consequential, or special damages, or for lost data, lost matches, lost alliance
              selections, or lost competitive advantage arising from your use of FRCMOB. Some
              jurisdictions do not allow these limits, in which case they apply only as far as the law
              allows.
            </p>
          </section>

          <section>
            <h4>10. Third-party data, media, and trademarks</h4>
            <p>
              <strong>{italicFirst(FIRST_TRADEMARK_NOTICE)}</strong> These names are used only to
              describe the competition, its events, and its awards accurately.
            </p>
            <p>
              <strong>Event data.</strong>{' '}
              <a href={FRC_EVENTS_API_URL} target="_blank" rel="noopener noreferrer">
                Event Data provided by <i>FIRST</i>
              </a>
              . <i>FIRST</i> retains all copyrights and other rights in that data, which FRCMOB uses
              free of charge under the{' '}
              <a href={FRC_EVENTS_TERMS_URL} target="_blank" rel="noopener noreferrer">FRC Events API Terms of Use</a>.
              Schedules, results, rankings, team details, and media links also come from{' '}
              <a href={TBA_URL} target="_blank" rel="noopener noreferrer">The Blue Alliance</a>, an
              independent volunteer project; FRCMOB is powered by its API and is not an official
              The Blue Alliance app. Data from either source can be late or wrong; report data
              errors to the source.
            </p>
            <p>
              <strong>Videos.</strong> YouTube is a trademark of Google LLC and Twitch is a trademark of
              Twitch Interactive, Inc. Videos and streams are played in their own embedded players and
              remain subject to their owners&apos; rights and the platforms&apos; terms (see section 5).
            </p>
            <p>
              <strong>Teams and their media.</strong> Team names, numbers, logos, avatars, and robot
              photos belong to their teams and photographers. They are shown as published through{' '}
              <i>FIRST</i> and The Blue Alliance, and may load directly from the hosts that store them
              (for example Imgur).
            </p>
            <p>
              <strong>Security check.</strong> Creating a workspace uses Cloudflare Turnstile.
              Cloudflare and Turnstile are trademarks of Cloudflare, Inc.
            </p>
            <p>
              Every other trademark belongs to its owner. Their use here is descriptive only and does
              not imply any affiliation or endorsement. If you own content or a mark shown in FRCMOB and
              want it corrected or removed,{' '}
              <a href={LEGAL_CONTACT_URL} target="_blank" rel="noopener noreferrer">
                {LEGAL_CONTACT_LABEL}
              </a>{' '}
              and we will act promptly.
            </p>
          </section>

          <section>
            <h4>11. Open-source software</h4>
            <p>
              FRCMOB is free software: you can redistribute and modify it under the{' '}
              <a href={LICENSE_URL} target="_blank" rel="noopener noreferrer">GNU Affero General Public License v3.0</a>.
              Its source code is available at{' '}
              <a href={SOURCE_CODE_URL} target="_blank" rel="noopener noreferrer">{SOURCE_CODE_URL.replace('https://', '')}</a>.
              The on-device robot detector was built with Ultralytics YOLO, which is also licensed under
              AGPL-3.0.
            </p>
            <p>
              FRCMOB includes open-source packages that remain under their own licenses; their notices
              are listed in the{' '}
              <a href={THIRD_PARTY_LICENSES_PATH} target="_blank" rel="noopener">third-party licenses</a>.
              The IBM Plex Sans typeface is used under the{' '}
              <a href={FONT_LICENSE_PATH} target="_blank" rel="noopener">SIL Open Font License 1.1</a>.
            </p>
          </section>

          <section>
            <h4>12. Privacy</h4>
            <p>
              Our handling of data is described in the <Link to="/privacy">Privacy Policy</Link>, which
              forms part of these terms.
            </p>
          </section>

          <section>
            <h4>13. Changes and termination</h4>
            <p>
              We may update these terms; the effective date above will change when we do, and
              continued use means you accept the update. We may suspend or discontinue the app, or
              restrict access for anyone breaking these terms, at any time.
            </p>
          </section>

          <section>
            <h4>14. Contact</h4>
            <p>
              Questions about these terms —{' '}
              <a href={LEGAL_CONTACT_URL} target="_blank" rel="noopener noreferrer">
                {LEGAL_CONTACT_LABEL}
              </a>
              .
            </p>
            <p>
              FRCMOB is built and maintained by{' '}
              <a href={AUTHOR_SITE_URL} target="_blank" rel="noopener noreferrer">
                {AUTHOR_NAME}
              </a>
              .
            </p>
          </section>
        </SurfaceCard>

    </div>
  );
}
