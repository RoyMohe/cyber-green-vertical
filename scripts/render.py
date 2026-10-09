#!/usr/bin/env python3
"""赛博绿竖版口播视频渲染器.

用法:
  python3 render.py spec.json -o out.mp4 [--audio voice.mp3] [--bgm bgm.mp3] [--srt voice.srt] [--fps 30] [--workers N]
  python3 render.py spec.json --stills stills_dir/      # 每个场景出一张预览 PNG + contact.png
依赖: playwright(chromium), ffmpeg/ffprobe
画面流程: 浏览器逐帧 seek → JPEG → ffmpeg(镜头后期: 暗角边缘模糊 + 胶片颗粒) → H.264
"""
import argparse, json, os, re, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "assets" / "template.html"
W, H = 1080, 1920
# 镜头后期: 画面边缘逐渐模糊 (遮罩外圈=模糊), 再加动态颗粒
LENS = "[0:v]split[a][b];[b]gblur=sigma={blur}[bl];[bl][1:v]alphamerge[bm];[a][bm]overlay=format=auto,noise=alls={grain}:allf=t+u,format=yuv420p[v]"
GRAIN = "[0:v]noise=alls={grain}:allf=t+u,format=yuv420p[v]"


def ffprobe_dur(p):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=nw=1:nk=1", str(p)])
    return float(out.strip())


def lens_mask(path):
    """边缘白(模糊)、中心黑(清晰) 的径向遮罩."""
    if Path(path).exists():
        return path
    expr = "255*clip((hypot((X-540)/(0.74*540),(Y-880)/(0.62*960))-0.55)/0.5,0,1)"
    subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:d=1",
                           "-vf", f"format=gray,geq=lum='{expr}'", "-frames:v", "1", str(path)])
    return path


def prepare_cam(spec, base, fps):
    """口播素材 → 逐帧 JPEG (缓存在 spec 同目录 .cam_frames_<name>/), 供模板按时间取帧."""
    cam = spec["cam"]
    src = (base / cam["src"]).resolve() if not Path(cam["src"]).is_absolute() else Path(cam["src"])
    cam["src"] = str(src)
    out = base / f".cam_frames_{src.stem}_{fps}"
    if not (out / "done").exists():
        out.mkdir(exist_ok=True)
        print("extracting camera frames ...", flush=True)
        vf = f"fps={fps},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        if cam.get("grade", True):  # 轻度调色: 略降饱和, 压暗高光, 偏冷绿, 与赛博绿画面统一
            vf += ",eq=saturation=0.88:contrast=1.06:brightness=-0.03,colorbalance=gs=0.04:bs=0.03:rh=-0.03"
        subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", vf, "-q:v", "3", str(out / "%05d.jpg")])
        (out / "done").write_text("ok")
    if cam.get("track", False):  # 默认关闭: 固定缩放+遮罩更稳, 不抖
        cam["track"] = face_track(out, fps)
    cam["frames"] = out.resolve().as_uri() + "/"
    cam["count"] = len(list(out.glob("*.jpg")))
    cam["fps"] = fps


def face_track(frames_dir, fps, step=3):
    """逐帧人脸位置 [cx, cy, h] (原画 1080x1920 坐标), 平滑后缓存. 画中画按它居中并按脸大小缩放, 保证整颗头在框里."""
    cache = Path(frames_dir) / "track.json"
    if cache.exists():
        return json.loads(cache.read_text())
    try:
        import cv2, numpy as np
    except ImportError:
        print("opencv not installed: face tracking off (pip install opencv-python-headless)"); return None
    det = cv2.FaceDetectorYN.create(str(HERE.parent / "assets" / "models" / "yunet.onnx"), "", (360, 640), 0.6, 0.3, 50)
    files = sorted(Path(frames_dir).glob("*.jpg")); n = len(files); k = 3
    pts = {}
    for i in range(0, n, step):
        im = cv2.resize(cv2.imread(str(files[i])), (1080 // k, 1920 // k))
        _, f = det.detect(im)
        if f is not None and len(f):
            x, y, w, h = max(f[:, :4], key=lambda r: r[2] * r[3])
            pts[i] = ((x + w / 2) * k, (y + h / 2) * k, h * k)
    if not pts:
        print("no face found: tracking off"); return None
    idx = sorted(pts); arr = np.array([pts[i] for i in idx], float)
    full = np.stack([np.interp(np.arange(n), idx, arr[:, j]) for j in range(3)], 1)
    win = max(1, int(fps * .6)); ker = np.ones(win) / win  # 0.6s 平滑, 防抖
    pad = np.pad(full, ((win, win), (0, 0)), mode="edge")
    sm = np.stack([np.convolve(pad[:, j], ker, mode="same")[win:-win] for j in range(3)], 1)
    track = [[int(a), int(b), int(c)] for a, b, c in sm]
    cache.write_text(json.dumps(track))
    print(f"face track: {len(pts)} detections / {n} frames")
    return track


def clean_len(s):
    s = re.sub(r"[\[\]\s，。、！？,.!?：:；;“”\"'（）()…—-]", "", s)
    return max(1, len(s))


def parse_srt(path):
    txt = Path(path).read_text(encoding="utf-8-sig")
    ts = re.findall(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", txt)
    f = lambda h, m, s, ms: int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    return [(f(*t[:4]), f(*t[4:])) for t in ts]


def build_timeline(spec, audio_dur=None, srt=None):
    cps = float(spec.get("cps", 4.6))
    lines = []
    for si, sc in enumerate(spec["scenes"]):
        for l in (sc.get("lines") or [""]):
            lines.append((si, {"text": l} if isinstance(l, str) else dict(l)))
    lead = float(spec.get("lead", 0.25))
    if srt:
        times = parse_srt(srt)
        for k, (si, d) in enumerate(lines):
            if k < len(times):
                d["start"], d["end"] = times[k]
            else:
                prev = lines[k - 1][1]["end"] if k else 0
                d["start"], d["end"] = prev, prev + clean_len(d["text"]) / cps + .3
        total = max(audio_dur or 0, lines[-1][1]["end"] + .6)
    else:
        base = [float(d["dur"]) if "dur" in d else max(1.1, clean_len(d["text"]) / cps + .25) for si, d in lines]
        if audio_dur:
            fixed = sum(b for b, (si, d) in zip(base, lines) if "dur" in d)
            flex = sum(b for b, (si, d) in zip(base, lines) if "dur" not in d)
            avail = audio_dur - lead - .4 - fixed
            if flex > 0 and avail > 0:
                base = [b if "dur" in d else b * avail / flex for b, (si, d) in zip(base, lines)]
        t = lead
        for b, (si, d) in zip(base, lines):
            d["start"], d["end"] = t, t + b
            t += b
        total = max(audio_dur or 0, t + .4)
    for si, sc in enumerate(spec["scenes"]):
        ls = [d for s, d in lines if s == si]
        sc["start"] = 0.0 if si == 0 else ls[0]["start"]
    for si, sc in enumerate(spec["scenes"]):
        sc["end"] = spec["scenes"][si + 1]["start"] if si + 1 < len(spec["scenes"]) else total
        ls = [d for s, d in lines if s == si]
        for j, d in enumerate(ls):
            d["end"] = max(d["end"], ls[j + 1]["start"]) if j + 1 < len(ls) else sc["end"]
        sc["lines"] = ls
    spec["total"] = total
    return spec


def cutout(path, tol=None):
    """产品图去底 → 透明 PNG. 从四角做"浮动容差"泛洪(跟随渐变底色, 在产品边缘的突变处停下),
    适用于白底/灰底/黑底/渐变底; 软阴影一并去掉. 已带透明通道的图原样返回."""
    import numpy as np
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    if im.getextrema()[3][0] < 250:
        return path
    out = Path(path).with_name(Path(path).stem + ".cutout.png")
    if out.exists() and out.stat().st_mtime > Path(path).stat().st_mtime:
        return out
    rgb = np.array(im.convert("RGB"))
    h, w = rgb.shape[:2]
    try:
        import cv2
        img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy()
        seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0)]
        def fill(t, fixed):
            mask = np.zeros((h + 2, w + 2), np.uint8)
            for sx, sy in seeds:
                if mask[sy + 1, sx + 1] == 0:
                    fl = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8) | (cv2.FLOODFILL_FIXED_RANGE if fixed else 0)
                    cv2.floodFill(img, mask, (sx, sy), 0, (t,) * 3, (t,) * 3, fl)
            return mask[1:-1, 1:-1] > 0
        bg = fill(tol or 4, False)          # 渐变底: 浮动容差
        if bg.mean() > .9:                  # 漏进产品(浅色产品+浅色底): 改为相对底色的固定容差
            bg = fill(tol or 16, True)
        bg = cv2.morphologyEx(bg.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)) > 0
        alpha = np.where(bg, 0, 255).astype(np.uint8)
        alpha = cv2.erode(alpha, np.ones((3, 3), np.uint8))
        alpha = cv2.GaussianBlur(alpha, (0, 0), 1.0)
    except ImportError:  # PIL fallback: fixed-threshold fill from corners
        from PIL import ImageDraw
        m = im.convert("RGB")
        for p in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
            ImageDraw.floodfill(m, p, (255, 0, 255), thresh=tol or 14)
        a = np.array(m)
        alpha = np.where((a[..., 0] == 255) & (a[..., 1] == 0) & (a[..., 2] == 255), 0, 255).astype(np.uint8)
    res = np.dstack([rgb, alpha])
    ys, xs = np.where(alpha > 10)
    if len(xs):
        res = res[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    Image.fromarray(res, "RGBA").save(out)
    return out


def resolve_images(spec, base):
    def fix(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "image" and isinstance(v, str) and not re.match(r"^(https?|data|file):", v):
                    f = (base / v).resolve()
                    if o.get("cutout", True) and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                        f = Path(cutout(f)).resolve()
                    o[k] = f.as_uri()
                else:
                    fix(v)
        elif isinstance(o, list):
            for v in o:
                fix(v)
    fix(spec)


def open_page(spec):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    br = pw.chromium.launch(args=["--allow-file-access-from-files", "--force-color-profile=srgb"])
    pg = br.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
    pg.goto(TEMPLATE.as_uri())
    pg.evaluate("s => build(s)", spec)
    pg.wait_for_timeout(300)
    return pw, br, pg


def lens_args(mask, blur, grain):
    """背景景深模糊已在浏览器里做(只作用于背景层). 这里 blur>0 时再对整帧边缘额外加模糊(会影响字幕, 默认关)."""
    if blur and blur > 0:
        return ["-loop", "1", "-i", str(mask), "-filter_complex", LENS.format(blur=blur, grain=grain), "-map", "[v]"]
    return ["-filter_complex", GRAIN.format(grain=grain), "-map", "[v]"]


def stills(spec, outdir, blur, grain, mask):
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    pw, br, pg = open_page(spec)
    files = []
    for i, sc in enumerate(spec["scenes"]):
        dur = sc["end"] - sc["start"]
        t = sc["start"] + max(min(dur - .3, 2.8), min(1.5, dur * .8))
        pg.evaluate(f"seek({t})")
        raw = outdir / f"_raw_{i}.png"; pg.screenshot(path=str(raw))
        f = outdir / f"scene_{i+1:02d}_{sc['kind']}.png"
        subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw)] + lens_args(mask, blur, grain)
                              + ["-frames:v", "1", "-pix_fmt", "rgb24", str(f)])
        raw.unlink(); files.append(f)
    br.close(); pw.stop()
    try:
        from PIL import Image
        cols = min(4, len(files)); rows = (len(files) + cols - 1) // cols; tw, th = 360, 640
        sheet = Image.new("RGB", (cols * tw + (cols + 1) * 12, rows * th + (rows + 1) * 12), (40, 40, 40))
        for k, f in enumerate(files):
            sheet.paste(Image.open(f).convert("RGB").resize((tw, th)), (12 + (k % cols) * (tw + 12), 12 + (k // cols) * (th + 12)))
        sheet.save(outdir / "contact.png")
    except Exception as e:
        print("contact sheet skipped:", e)
    print(f"{len(files)} stills -> {outdir}  (total {spec['total']:.1f}s)")


def render_segment(spec, out, fps, f0, f1, blur, grain, mask, crf, tag=""):
    enc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-"]
                           + lens_args(mask, blur, grain)
                           + ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-r", str(fps), "-frames:v", str(f1 - f0), str(out)],
                           stdin=subprocess.PIPE)
    pw, br, pg = open_page(spec)
    for f in range(f0, f1):
        pg.evaluate(f"seek({f / fps})")
        enc.stdin.write(pg.screenshot(type="jpeg", quality=93))
        if (f - f0) % (fps * 4) == 0:
            print(f"{tag} frame {f - f0}/{f1 - f0}", flush=True)
    br.close(); pw.stop()
    enc.stdin.close(); enc.wait()


def render(spec, out, fps, workers, audio=None, bgm=None, crf=18, blur=12, grain=7, mask=None):
    total = spec["total"]; n = int(round(total * fps))
    tmpd = Path(tempfile.mkdtemp(prefix="cgv_", dir=Path(out).resolve().parent))
    workers = max(1, min(workers, n // fps or 1))
    bounds = [round(n * k / workers) for k in range(workers + 1)]
    segs = [tmpd / f"seg{k}.mp4" for k in range(workers)]
    if workers == 1:
        render_segment(spec, segs[0], fps, 0, n, blur, grain, mask, crf)
    else:
        sp = tmpd / "spec.json"; sp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        procs = [subprocess.Popen([sys.executable, __file__, str(sp), "--_seg", f"{bounds[k]},{bounds[k+1]}", "-o", str(segs[k]),
                                   "--fps", str(fps), "--blur", str(blur), "--grain", str(grain), "--crf", str(crf), "--_mask", str(mask)])
                 for k in range(workers)]
        if any(p.wait() for p in procs):
            sys.exit("a render worker failed")
    lst = tmpd / "list.txt"; lst.write_text("".join(f"file '{s}'\n" for s in segs))
    video = tmpd / "video.mp4"
    subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(video)])
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video)]
    fade = f"afade=t=out:st={max(0, total - 1.5)}:d=1.5"
    if audio and bgm:
        cmd += ["-i", audio, "-stream_loop", "-1", "-i", bgm, "-filter_complex",
                f"[2:a]volume=0.12,{fade}[b];[1:a][b]amix=inputs=2:duration=first:normalize=0[a]", "-map", "0:v", "-map", "[a]"]
    elif audio:
        cmd += ["-i", audio, "-map", "0:v", "-map", "1:a"]
    elif bgm:
        cmd += ["-stream_loop", "-1", "-i", bgm, "-filter_complex", f"[1:a]volume=0.35,{fade}[a]", "-map", "0:v", "-map", "[a]"]
    if audio or bgm:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "copy", "-t", f"{total:.3f}", "-movflags", "+faststart", out]
    subprocess.check_call(cmd)
    for f in tmpd.iterdir():
        f.unlink()
    tmpd.rmdir()
    print(f"done -> {out}  ({total:.1f}s, {W}x{H}@{fps}, {workers} workers)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("-o", "--out", default="out.mp4")
    ap.add_argument("--audio"); ap.add_argument("--bgm"); ap.add_argument("--srt")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 2))))
    ap.add_argument("--blur", type=float, default=0, help="整帧边缘额外模糊 (默认0; 会糊到字幕)")
    ap.add_argument("--grain", type=int, default=3, help="胶片颗粒强度 (0 关闭)")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--stills")
    ap.add_argument("--dump", help="write resolved timeline json here")
    ap.add_argument("--_seg"); ap.add_argument("--_mask")
    a = ap.parse_args()
    sp = Path(a.spec).resolve()
    spec = json.loads(sp.read_text(encoding="utf-8"))
    mask = a._mask or lens_mask(Path(tempfile.gettempdir()) / "cgv_lens_mask.png")
    if a._seg:  # internal worker: spec already resolved
        f0, f1 = map(int, a._seg.split(","))
        render_segment(spec, a.out, a.fps, f0, f1, a.blur, a.grain, mask, a.crf, tag=f"[{f0}-{f1}]")
        return
    resolve_images(spec, sp.parent)
    if spec.get("cam", {}).get("src"):
        prepare_cam(spec, sp.parent, a.fps)
        if not a.audio and not spec["cam"].get("mute"):
            a.audio = spec["cam"]["src"]
    build_timeline(spec, ffprobe_dur(a.audio) if a.audio else None, a.srt)
    if a.dump:
        Path(a.dump).write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.stills:
        stills(spec, a.stills, a.blur, a.grain, mask)
    else:
        render(spec, a.out, a.fps, a.workers, a.audio, a.bgm, a.crf, a.blur, a.grain, mask)


if __name__ == "__main__":
    main()
