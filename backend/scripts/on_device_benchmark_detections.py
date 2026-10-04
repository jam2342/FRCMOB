# Dense detections for the on-device benchmark: the same model, crop and decode the
# browser uses, at `--fps` over the match window, plus bumper colour fractions per box.
import argparse, json, time
import cv2, numpy as np, onnxruntime as ort

ap = argparse.ArgumentParser()
ap.add_argument("--video"); ap.add_argument("--model"); ap.add_argument("--calib"); ap.add_argument("--out")
ap.add_argument("--fps", type=float, default=10); ap.add_argument("--start", type=float, default=8); ap.add_argument("--end", type=float, default=170)
ap.add_argument("--conf", type=float, default=0.25)
a = ap.parse_args()
cal = json.load(open(a.calib)); roi = cal["roi"]; plan = cal["plan"]
so = ort.SessionOptions(); so.intra_op_num_threads = 8
sess = ort.InferenceSession(a.model, so, providers=["CPUExecutionProvider"])
inp = sess.get_inputs()[0].name

def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy; u = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / u if u > 0 else 0

def detect(frame_rgb):
    W, H, s = plan["inputW"], plan["inputH"], plan["scale"]
    crop = frame_rgb[roi["y"]:roi["y"]+roi["h"], roi["x"]:roi["x"]+roi["w"]]
    if s != 1: crop = cv2.resize(crop, (round(roi["w"]*s), round(roi["h"]*s)), interpolation=cv2.INTER_LINEAR)
    canvas = np.zeros((H, W, 3), np.uint8); canvas[:crop.shape[0], :crop.shape[1]] = crop
    x = (canvas.astype(np.float32) / 255).transpose(2, 0, 1)[None]
    out = sess.run(None, {inp: x})[0][0]  # [5, N]
    keep = out[4] >= a.conf
    cx, cy, w, h, sc = out[:, keep]
    boxes = sorted(zip(cx-w/2, cy-h/2, cx+w/2, cy+h/2, sc), key=lambda b: -b[4])
    kept = []
    for b in boxes:
        if all(iou(b, k) <= 0.45 for k in kept): kept.append(b)
    return [[float(b[0]/s+roi["x"]), float(b[1]/s+roi["y"]), float(b[2]/s+roi["x"]), float(b[3]/s+roi["y"]), float(b[4])] for b in kept]

def colour(hsv, b):
    x1, y1, x2, y2 = [int(round(v)) for v in b[:4]]
    x1, x2 = max(0, x1), min(hsv.shape[1], x2); y2 = min(hsv.shape[0], y2)
    y1 = max(0, int(y2 - 0.45 * (y2 - max(0, y1))))
    band = hsv[y1:y2, x1:x2].reshape(-1, 3)
    if len(band) == 0: return 0.0, 0.0
    hh, ss, vv = band[:, 0].astype(int), band[:, 1].astype(int), band[:, 2].astype(int)
    red = ((hh <= 8) | (hh >= 170)) & (ss >= 110) & (vv >= 70)
    blue = (hh >= 100) & (hh <= 128) & (ss >= 110) & (vv >= 60)
    return float(red.mean()), float(blue.mean())

cap = cv2.VideoCapture(a.video); fps = cap.get(cv2.CAP_PROP_FPS)
frames, idx, next_t, t0 = [], 0, a.start, time.time()
while True:
    ok, bgr = cap.read()
    if not ok: break
    t = idx / fps; idx += 1
    if t + 1e-6 < next_t: continue
    if t > a.end: break
    next_t += 1 / a.fps
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB); hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    dets = [{"bbox": b[:4], "score": b[4], "red": c[0], "blue": c[1]} for b in detect(rgb) for c in [colour(hsv, b)]]
    frames.append({"t": round(t, 4), "dets": dets})
    if len(frames) % 100 == 0: print(len(frames), round(t, 1), round(time.time() - t0), flush=True)
json.dump({"video_fps": fps, "sample_fps": a.fps, "roi": roi, "H": cal["H"], "frames": frames}, open(a.out, "w"))
print("done", len(frames), round(time.time() - t0))
