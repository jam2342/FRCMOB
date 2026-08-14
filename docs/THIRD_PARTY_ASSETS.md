# Third-party assets and data

The AGPL-3.0 license covers FRCMOB source code. It does not grant rights to unrelated third-party trademarks, manuals, video, datasets, or model artifacts.

## Ultralytics YOLO

The backend uses the `ultralytics` package and models derived from that toolchain. Ultralytics publishes its open-source code and models under AGPL-3.0 and offers a separate enterprise license. FRCMOB therefore uses AGPL-3.0. Preserve upstream notices and review the current [Ultralytics licensing terms](https://www.ultralytics.com/license) before redistributing weights.

FRCMOB's trained `.pt` and `.onnx` weights and training datasets are not included. Operators must provision authorized artifacts with `VIDEO_TRACKING_YOLO_MODEL_URL`/`VIDEO_TRACKING_YOLO_MODEL_SHA256` or `VITE_ONDEVICE_MODEL_URL`.

## FIRST materials and marks

FIRST game manuals, field artwork, event video, logos, and game assets are copyrighted or trademarked by FIRST and are not covered by FRCMOB's license. This repository intentionally does not include the game manual. Download current official materials from [FIRST's Game & Season page](https://www.firstinspires.org/robotics/frc/game-and-season).

FRCMOB is not affiliated with or endorsed by FIRST. Follow the current [FIRST brand and intellectual-property policy](https://www.firstinspires.org/about/brand) when publishing a deployment or derivative project.

## External data services

The Blue Alliance, Statbotics, FIRST APIs, YouTube, and other integrations retain their own terms, attribution requirements, rate limits, and data licenses. API access does not imply permission to republish bulk datasets or recordings. Contributors must use synthetic or independently licensed fixtures.
