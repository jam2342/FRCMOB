# Third-party assets and data

The AGPL-3.0 license covers FRCMOB source code. It does not grant rights to unrelated third-party trademarks, manuals, video, datasets, or model artifacts.

## Ultralytics YOLO

The detector training scripts use the `ultralytics` package, and the on-device detector is derived from that toolchain. Ultralytics publishes its open-source code and models under AGPL-3.0 and offers a separate enterprise license. FRCMOB therefore uses AGPL-3.0. Preserve upstream notices and review the current [Ultralytics licensing terms](https://www.ultralytics.com/license) before redistributing weights.

FRCMOB's trained `.pt` and `.onnx` weights and training datasets are not included. Operators must provision an authorized ONNX artifact with `VITE_ONDEVICE_MODEL_URL`.

## FIRST materials and marks

FIRST game manuals, field artwork, event video, logos, and game assets are copyrighted or trademarked by FIRST and are not covered by FRCMOB's license. This repository intentionally does not include the game manual. Download current official materials from [FIRST's Game & Season page](https://www.firstinspires.org/robotics/frc/game-and-season).

FRCMOB is not affiliated with or endorsed by FIRST. Follow the current [FIRST brand and intellectual-property policy](https://www.firstinspires.org/about/brand) when publishing a deployment or derivative project.

## External data services

The Blue Alliance, FIRST APIs, YouTube, and other integrations retain their own terms, attribution requirements, rate limits, and data licenses.

## Required notices in the app (checked 2026-10-18)

The site footer (`ScoutingApp/src/components/SiteFooter.tsx`, on every page) and the Terms/Privacy pages carry these; `SiteFooter.test.tsx` fails if the footer loses one. Wording lives in `ScoutingApp/src/pages/legalContent.ts`.

| Source | Requirement | Where |
| --- | --- | --- |
| [FIRST trademark policy](https://info.firstinspires.org/hubfs/web/about/policy/ip-policy-trademarks-copyrighted-materials-first-lego.pdf) (rev. 2025-04-19) | Name each mark used (FIRST®, FIRST® Robotics Competition, FRC®, Gracious Professionalism®, REBUILT™), owner "For Inspiration and Recognition of Science and Technology", and the "not overseeing, involved with, or responsible for" wording; FIRST in italics. Add new marks (next season's game name) to `FIRST_TRADEMARK_NOTICE`. | Footer, Terms §10 |
| [FRC Events API terms](https://frc-events.firstinspires.org/services/api/terms) | A conspicuous "Event Data provided by FIRST" link to the API page on **each page** that shows event data. **No commercial use: nothing that generates revenue** (selling data or access, or putting it in a paid product). | Footer |
| [The Blue Alliance developer guidelines](https://www.thebluealliance.com/apidocs) | "Powered by The Blue Alliance" with a link back; don't use TBA's name or lamp logo in our branding. | Footer |
| [YouTube developer policies](https://developers.google.com/youtube/terms/developer-policies) | The embedded player counts as YouTube API Services: link the YouTube ToS, say in our terms that users agree to it, say in our privacy policy that we use YouTube API Services and link the Google Privacy Policy. | Footer, Terms §5, Privacy |
| Vercel Web Analytics, Cloudflare Turnstile | Disclose in the privacy policy (we are the controller). Analytics is off under DNT/GPC like our own timings. | Privacy |
| AGPL-3.0 | Offer network users the source: footer "Source code" links the public mirror, so keep it in sync with what's deployed. | Footer, Terms §11 |
| Bundled npm packages, IBM Plex (OFL) | Ship their notices: Vite writes `third-party-licenses.txt` at build (`build.license`); the font license is `public/fonts/OFL.txt`. | Footer, Terms §11 |

Written permission from FIRST to use "FRC" in the FRCMOB name is being requested (October 2026, via customerservice@firstinspires.org for Marketing and Development); record the answer here. API access does not imply permission to republish bulk datasets or recordings. Contributors must use synthetic or independently licensed fixtures.
