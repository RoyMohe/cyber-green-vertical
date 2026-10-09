#!/usr/bin/env python3
"""离线中文语音识别 → 带时间戳的字幕 (SenseVoice ONNX, 纯 numpy 前端, 无需 HF).

用法: python3 asr.py input.(mp4|wav) -m <sense-voice 模型目录> -o out.srt [--json words.json] [--max 14]
模型: https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2
依赖: onnxruntime, numpy, ffmpeg
"""
import argparse, json, re, subprocess
from pathlib import Path
import numpy as np


def load_audio(path, sr=16000):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"])
    return np.frombuffer(raw, np.int16).astype(np.float32)  # int16 scale (kaldi convention)


def mel_banks(n_mels=80, n_fft=512, sr=16000, lo=20.0, hi=None):
    hi = hi or sr / 2
    mel = lambda f: 1127.0 * np.log(1 + f / 700.0)
    mlo, mhi = mel(lo), mel(hi)
    centers = np.linspace(mlo, mhi, n_mels + 2)
    freqs = mel(np.arange(n_fft // 2 + 1) * sr / n_fft)
    W = np.zeros((n_mels, n_fft // 2 + 1), np.float32)
    for m in range(n_mels):
        l, c, r = centers[m:m + 3]
        up = (freqs - l) / (c - l); down = (r - freqs) / (r - c)
        W[m] = np.maximum(0, np.minimum(up, down))
    return W


def fbank(x, sr=16000):
    fl, fs, nfft = 400, 160, 512
    n = 1 + (len(x) - fl) // fs
    idx = np.arange(fl)[None, :] + fs * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
    fr -= fr.mean(1, keepdims=True)
    fr = np.concatenate([fr[:, :1] - .97 * fr[:, :1], fr[:, 1:] - .97 * fr[:, :-1]], 1)
    fr *= np.hamming(fl)[None, :]  # SenseVoice frontend: hamming
    spec = np.abs(np.fft.rfft(fr, nfft)) ** 2
    return np.log(np.maximum(spec @ mel_banks().T.astype(np.float64), 1.19e-7)).astype(np.float32)


def lfr(f, m=7, n=6):
    T = f.shape[0]; pad = (m - 1) // 2
    f = np.concatenate([np.repeat(f[:1], pad, 0), f], 0)
    out = []
    for i in range(0, T, n):
        seg = f[i:i + m]
        if seg.shape[0] < m:
            seg = np.concatenate([seg, np.repeat(f[-1:], m - seg.shape[0], 0)], 0)
        out.append(seg.reshape(-1))
    return np.stack(out)


def recognize(path, model_dir):
    import onnxruntime as ort
    md = Path(model_dir)
    sess = ort.InferenceSession(str(md / ("model.int8.onnx" if (md / "model.int8.onnx").exists() else "model.onnx")))
    meta = sess.get_modelmeta().custom_metadata_map
    neg_mean = np.array(meta["neg_mean"].split(","), np.float32); inv_std = np.array(meta["inv_stddev"].split(","), np.float32)
    toks = {}
    for line in (md / "tokens.txt").read_text(encoding="utf-8").splitlines():
        t, i = line.rsplit(" ", 1); toks[int(i)] = t
    audio = load_audio(path)
    words = []
    CH = 16000 * 25  # 25s chunks with 0.5s overlap handling by simple cut
    for c0 in range(0, len(audio), CH):
        seg = audio[c0:c0 + CH]
        if len(seg) < 4000:
            break
        feats = (lfr(fbank(seg)) + neg_mean) * inv_std
        logits = sess.run(None, {"x": feats[None], "x_length": np.array([feats.shape[0]], np.int32),
                                 "language": np.array([int(meta["lang_zh"])], np.int32),
                                 "text_norm": np.array([int(meta["with_itn"])], np.int32)})[0][0]
        ids = logits.argmax(-1); prev = 0
        for f, k in enumerate(ids):
            if f >= 4 and k != 0 and k != prev:  # first 4 frames: lang/emo/event/itn tags
                t = toks.get(int(k), "")
                if not re.match(r"^<\|.*\|>$", t):
                    words.append({"t": c0 / 16000 + (f - 4) * 0.06, "w": t.replace("▁", " ")})
            prev = k
    end = len(audio) / 16000
    for i, w in enumerate(words):
        w["e"] = min(words[i + 1]["t"] if i + 1 < len(words) else end, w["t"] + 0.6)
    return words, end


def to_lines(words, maxc=14):
    lines, cur = [], []
    def flush():
        if cur:
            txt = "".join(x["w"] for x in cur).strip()
            txt = re.sub(r"[，。,.！？!?、；;：:]+$", "", txt)
            if txt:
                lines.append({"text": txt, "start": cur[0]["t"], "end": cur[-1]["e"]})
        cur.clear()
    for i, w in enumerate(words):
        if re.fullmatch(r"\s*[，。,.！？!?、；;：:]\s*", w["w"]):
            flush(); continue
        gap = i and w["t"] - words[i - 1]["e"] > 0.45
        if cur and (gap or len("".join(x["w"] for x in cur)) >= maxc):
            flush()
        cur.append(w)
    flush()
    return lines


def srt(lines):
    f = lambda s: f"{int(s//3600):02d}:{int(s%3600//60):02d}:{int(s%60):02d},{int(s*1000%1000):03d}"
    return "".join(f"{i+1}\n{f(l['start'])} --> {f(l['end'])}\n{l['text']}\n\n" for i, l in enumerate(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input"); ap.add_argument("-m", "--model", required=True)
    ap.add_argument("-o", "--out", default="out.srt"); ap.add_argument("--json"); ap.add_argument("--max", type=int, default=14)
    a = ap.parse_args()
    words, end = recognize(a.input, a.model)
    lines = to_lines(words, a.max)
    Path(a.out).write_text(srt(lines), encoding="utf-8")
    if a.json:
        Path(a.json).write_text(json.dumps({"duration": end, "words": words, "lines": lines}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("".join(w["w"] for w in words))
    print(f"{len(lines)} lines -> {a.out}")
