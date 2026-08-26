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

export function TermsOfServicePage() {
  return (
    <div className="center-page-container narrow legal-page">
      <SurfaceCardGroup>
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
            FRCMOB is a scouting and match-analysis tool for FIRST Robotics Competition teams. By
            using it, you agree to these terms. If you do not agree, please do not use the app.
          </p>

          <section>
            <h4>1. What FRCMOB is</h4>
            <p>
              FRCMOB collects scouting observations, ingests public FRC data, analyzes match video,
              and produces team ratings, picklists, and match predictions. It is a decision-support
              tool for competition strategy. It is not affiliated with, endorsed by, or sponsored by
              FIRST, The Blue Alliance, Statbotics, or any video platform.
            </p>
          </section>

          <section>
            <h4>2. Who may use it</h4>
            <p>
              FRCMOB is intended for FRC teams, their students, mentors, and coaches. There are no
              accounts, so access is controlled by whoever shares the app and its event workspaces
              with you. If you are under 18, use FRCMOB with the awareness and permission of your
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
              Derived data from match video and scouting entries may be used to improve the
              analysis models that power the app. This applies to numeric performance data, never to
              recorded video, which stays on your device and is never uploaded.
            </p>
            <p>
              You are responsible for having the right to upload what you upload, and for the accuracy
              of what you enter.
            </p>
          </section>

          <section>
            <h4>5. Video, recording, and event rules</h4>
            <p>
              If you record match video with FRCMOB, you are responsible for complying with the
              event&apos;s and venue&apos;s recording policies. Analysis of recorded video happens on
              your own device. Public match video ingested from third-party platforms remains subject
              to those platforms&apos; terms.
            </p>
          </section>

          <section>
            <h4>6. Accuracy — read this one</h4>
            <p>
              Ratings, role classifications, alliance synergy scores, picklist ordering, and match
              predictions are <strong>estimates produced by statistical and machine-learning
              models</strong>. Machine-generated scouting fields are drafts carrying a confidence
              score and require human review before they become saved entries. Computer vision on
              competition video is imperfect: robots get occluded, bumper numbers are misread, camera
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
            <h4>10. Third-party data and trademarks</h4>
            <p>
              FRCMOB displays data from The Blue Alliance, the FRC Events API, Statbotics, and public
              video platforms, subject to their terms. FIRST&reg;, FIRST&reg; Robotics Competition, and
              related marks are trademarks of the United States Foundation for Inspiration and
              Recognition of Science and Technology (FIRST). Team names, numbers, and logos belong to
              their respective teams. Use here is descriptive and does not imply endorsement.
            </p>
          </section>

          <section>
            <h4>11. Privacy</h4>
            <p>
              Our handling of data is described in the <Link to="/privacy">Privacy Policy</Link>, which
              forms part of these terms.
            </p>
          </section>

          <section>
            <h4>12. Changes and termination</h4>
            <p>
              We may update these terms; the effective date above will change when we do, and
              continued use means you accept the update. We may suspend or discontinue the app, or
              restrict access for anyone breaking these terms, at any time.
            </p>
          </section>

          <section>
            <h4>13. Contact</h4>
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
      </SurfaceCardGroup>
    </div>
  );
}
