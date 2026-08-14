# ML production runbook

Detector weights are intentionally excluded from Git. A deploy is ready only when the backend and browser model artifacts are explicitly provisioned.

## Required release inputs

1. Publish the validated `frc_robot_detector_v2.pt` to HTTPS object storage and record its SHA-256:

   ```sh
   shasum -a 256 frc_robot_detector_v2.pt
   ```

2. Configure the backend deployment with `VIDEO_TRACKING_YOLO_MODEL_URL` and `VIDEO_TRACKING_YOLO_MODEL_SHA256`. Production compose refuses to start without them. The backend downloads the artifact atomically to its shared media volume and rejects a size or checksum mismatch.
3. Publish the matching ONNX export at a public, HTTPS, CORS-enabled URL and set `VITE_ONDEVICE_MODEL_URL` in the frontend build environment. Do not use an admin URL or a URL requiring browser credentials. A production frontend build fails if neither this URL nor a deliberately supplied local ONNX artifact is available.
4. Before release, run the locked detector evaluation against the exact deployed `.pt` artifact:

   ```sh
   cd backend
   PYTHONPATH=. python scripts/verify_holdout.py --model /path/to/frc_robot_detector_v2.pt
   ```

5. Deploy backend migrations before or with the backend image, then verify `GET /health/deep` returns `ok: true`, `ml_primary_detector.present: true`, and `ml_primary_detector.checksum_verified: true`.

## Shadow-model release policy

Automatic training and activation are off by default. Train a candidate with `activate=false`, evaluate its time-split metrics and calibration, materialize predictions for a representative event, and only then activate it through an intentional admin release. Do not promote a model solely because it trained successfully.

Model artifacts are written atomically, so a worker can never load a partially written candidate. Keep the backend media volume persistent; it holds active shadow artifacts as well as the provisioned detector.

## Release checks

- `docker compose -f docker/docker-compose.prod.yml config` with the required non-secret variables available.
- Backend tests in a Python 3.11 environment with `backend/requirements.txt` installed.
- `cd ScoutingApp && npm run lint && npm run test -- --run && npm run build`.
- Verify one real match through video analysis, auto-scout draft review, and on-device sync before enabling the new model for scouts.
