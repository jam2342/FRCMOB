import { Link } from 'react-router-dom';
import { SurfaceCard, SurfaceCardGroup } from '../components/ui/SurfaceCard';
import {
  AUTHOR_NAME,
  AUTHOR_SITE_URL,
  LEGAL_CONTACT_LABEL,
  LEGAL_CONTACT_URL,
  LEGAL_EFFECTIVE_DATE,
} from './legalContent';
import './LegalPage.css';

export function PrivacyPolicyPage() {
  return (
    <div className="center-page-container narrow legal-page">
      <SurfaceCardGroup>
        <SurfaceCard
          title="Privacy Policy"
          subtitle="What FRCMOB collects, why, and what it never collects."
          expandable={false}
          mobileCollapsible={false}
        >
          <div className="legal-meta">
            <span className="center-chip">Effective {LEGAL_EFFECTIVE_DATE}</span>
            <Link className="center-btn ghost" to="/terms">
              Terms of Service
            </Link>
          </div>

          <p className="legal-intro">
            FRCMOB is a scouting and match-analysis tool for FIRST Robotics Competition teams. It is
            built around robot performance, not people. This policy describes exactly what the app
            stores, where it stores it, and how long it keeps it.
          </p>

          <section>
            <h4>No accounts, no personal profiles</h4>
            <p>
              FRCMOB has no user sign-up. We do not ask for your name, email address, phone number,
              date of birth, or postal address, and there is no account record tied to you. Because
              minors participate in FRC, this is deliberate: the app is designed so that it does not
              need personal information about students in order to work.
            </p>
            <p>
              The one identifier you may type in is a scout profile label — a short name or initials
              you choose so a team knows who submitted which scouting entry. Choose whatever your
              team is comfortable with; it is stored alongside the entries you submit and is visible
              to others using your team&apos;s data.
            </p>
          </section>

          <section>
            <h4>What is stored on your device only</h4>
            <p>
              The following stay in your browser&apos;s local storage and IndexedDB and are never
              transmitted to our servers:
            </p>
            <ul>
              <li>Display preferences — theme, density, interface mode, refresh interval.</li>
              <li>Favorite events and teams, your selected team, and recent searches.</li>
              <li>Comparison selections, per-page filters, and saved autonomous paths.</li>
              <li>Tutorial progress and dismissed hints.</li>
              <li>Scouting entries drafted offline, until you choose to submit them.</li>
              <li>
                Field calibration data for on-device analysis (a homography matrix — geometry, not
                imagery).
              </li>
            </ul>
            <p>
              You can erase all of it at any time from{' '}
              <Link to="/settings">Settings → Maintenance</Link>, or by clearing site data in your
              browser. Nothing on the server depends on it.
            </p>
          </section>

          <section>
            <h4>Video and on-device match breakdown</h4>
            <p>
              This is the part most people ask about, so it is stated plainly:{' '}
              <strong>
                video you record in FRCMOB is processed entirely on your own device and is never
                uploaded to our servers.
              </strong>{' '}
              Robot detection, tracking, and identity all run locally in your browser.
            </p>
            <p>
              When a completed breakdown syncs, what leaves the device is derived numeric data only:
              robot field positions over time, zone occupancy, and the resulting offense/defense
              measurements, all keyed to FRC team numbers. The camera frames themselves are discarded
              and are not stored, transmitted, or used to train anything.
            </p>
            <p>
              Camera access is requested only when you open a capture screen, and only for that
              session. You can deny or revoke it in your browser or OS settings; the rest of the app
              continues to work.
            </p>
          </section>

          <section>
            <h4>What is stored on our servers</h4>
            <ul>
              <li>
                <strong>Scouting data you submit</strong> — match and pit scouting observations about
                robots, tagged with the FRC team number, event, match, and your chosen scout profile
                label.
              </li>
              <li>
                <strong>Pit photos</strong> — images of robots you upload. Please photograph robots,
                not people.
              </li>
              <li>
                <strong>Scouting room activity</strong> — the room name, the display name you choose
                when joining, assignments, and role (leader, co-leader, member), so live collaboration
                works.
              </li>
              <li>
                <strong>Picklists and strategy notes</strong> your team creates.
              </li>
              <li>
                <strong>Push notification subscriptions</strong> — if you enable alerts, your browser
                gives us an anonymous push endpoint URL and its encryption keys, plus which events and
                teams you want alerts for. This identifies a browser installation, not a person, and is
                deleted when you disable notifications.
              </li>
              <li>
                <strong>Derived analytics</strong> — ratings, predictions, and match findings computed
                from the above and from public data.
              </li>
            </ul>
          </section>

          <section>
            <h4>Public data we ingest</h4>
            <p>
              FRCMOB pulls event schedules, match results, team information, rankings, and public
              match video from The Blue Alliance, the official FRC Events API, Statbotics, and public
              video platforms. That data is already public and is governed by those services&apos; own
              terms. We cache it to keep the app fast and usable on weak venue wifi.
            </p>
          </section>

          <section>
            <h4>Who can see your data</h4>
            <p>
              Scouting data is competition data — it is meant to be shared with the people you scout
              with. Anyone with access to your event workspace or scouting room can see entries,
              picklists, and the scout profile labels attached to them. Do not enter anything in a
              notes field that you would not want your alliance partners, or another team, to read.
            </p>
            <p>
              We do not sell data, and we do not share it with advertisers or data brokers. There is no
              advertising in FRCMOB and no third-party advertising or analytics trackers.
            </p>
          </section>

          <section>
            <h4>Infrastructure providers</h4>
            <p>
              The app runs on third-party infrastructure that necessarily processes data in order to
              host it: a frontend host, cloud compute for the API and analysis worker, a managed
              Postgres database, a managed Redis instance, and your browser vendor&apos;s push service
              for notifications. These providers act as processors for storage and delivery; they are
              not permitted to use the data for their own purposes.
            </p>
          </section>

          <section>
            <h4>Retention</h4>
            <p>
              Scouting and analysis data is kept for the competition season and historical comparison
              across seasons — season-over-season trends are a core feature. Push subscriptions are
              removed when you unsubscribe or when the endpoint repeatedly fails. Local device data
              persists until you clear it. If you want data about your team removed, contact us and we
              will delete it.
            </p>
          </section>

          <section>
            <h4>Security</h4>
            <p>
              Traffic to the app is served over HTTPS, and administrative actions require an
              authenticated key. That said, FRCMOB is a competition tool operated by a small team — do
              not use it to store anything sensitive, personal, or confidential. It is not built to
              hold that kind of information, and this policy does not promise otherwise.
            </p>
          </section>

          <section>
            <h4>Students and minors</h4>
            <p>
              FRC involves participants under 18. FRCMOB is intentionally built to avoid collecting
              personal information from anyone, regardless of age: there are no accounts, no contact
              details, and no behavioral tracking. If you are a parent, mentor, or coach and believe a
              student has entered personal information into a free-text field, contact us and we will
              remove it.
            </p>
          </section>

          <section>
            <h4>Your choices</h4>
            <ul>
              <li>Decline camera access — on-device recording is optional.</li>
              <li>Decline or revoke push notifications at any time.</li>
              <li>Use initials or a role instead of a full name as your scout profile.</li>
              <li>Clear all local data from Settings → Maintenance.</li>
              <li>Ask us to delete server-side data associated with your team.</li>
            </ul>
          </section>

          <section>
            <h4>Changes</h4>
            <p>
              If this policy changes in a way that materially affects what we collect, the effective
              date above will change and the update will be noted in the app. Continuing to use FRCMOB
              after that means you accept the revised policy.
            </p>
          </section>

          <section>
            <h4>Contact</h4>
            <p>
              Questions, corrections, or deletion requests —{' '}
              <a href={LEGAL_CONTACT_URL} target="_blank" rel="noopener noreferrer">
                {LEGAL_CONTACT_LABEL}
              </a>
              . Please do not include personal information in a public issue; say what you need
              removed and we will follow up.
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
      </SurfaceCardGroup>
    </div>
  );
}
