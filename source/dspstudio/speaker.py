"""Local MFCC + diagonal GMM speaker identification; no network or pickle models."""
import json
import os
import uuid
from pathlib import Path
import numpy as np
from scipy.fft import dct
from scipy.special import logsumexp
from .dsp import SR, mono_audio, rms


def features(audio):
    x = mono_audio(audio).astype(np.float64)
    if len(x) < SR * 2 or rms(x) < 0.002:
        raise ValueError("请提供至少 2 秒清晰语音；当前音频太短或音量过低")
    x = x / max(rms(x), 0.01)
    pre = np.r_[x[0], x[1:] - 0.97*x[:-1]]
    frame, hop = 400, 160
    windows = np.lib.stride_tricks.sliding_window_view(pre, frame)[::hop].copy()
    energy_windows = np.lib.stride_tricks.sliding_window_view(x, frame)[::hop]
    energy = np.mean(energy_windows**2, axis=1)
    active = energy > max(0.08, np.max(energy) * 0.012)
    if active.sum() < 80:
        raise ValueError("有效语音不足，请录制 5～15 秒连续说话的样本")
    power = np.abs(np.fft.rfft(windows * np.hamming(frame), n=512)) ** 2 / 512
    hz_to_mel = lambda f: 2595*np.log10(1+np.asarray(f)/700)
    mel_to_hz = lambda m: 700*(10**(np.asarray(m)/2595)-1)
    points = mel_to_hz(np.linspace(hz_to_mel(80), hz_to_mel(7600), 28))
    frequencies = np.fft.rfftfreq(512, 1/SR)
    bank = np.zeros((26, len(frequencies)))
    for i in range(26):
        left = (frequencies-points[i])/(points[i+1]-points[i])
        right = (points[i+2]-frequencies)/(points[i+2]-points[i+1])
        bank[i] = np.maximum(0, np.minimum(left, right))
    cepstra = dct(np.log(power @ bank.T + 1e-10), type=2, norm="ortho", axis=1)[:, 1:14]
    pad = np.pad(cepstra, ((2,2),(0,0)), mode="edge")
    delta = (pad[3:-1] - pad[1:-3] + 2*(pad[4:] - pad[:-4])) / 10
    return np.hstack((cepstra, delta))[active].astype(np.float32)


class DiagGMM:
    def fit(self, x, components=4):
        rng = np.random.default_rng(42)
        k = min(components, max(1, len(x)//50))
        self.mean = x[rng.choice(len(x), k, replace=False)].copy()
        self.var = np.tile(np.var(x, axis=0) + 0.1, (k, 1))
        self.weight = np.ones(k)/k
        last = -np.inf
        for _ in range(70):
            logp = self.log_components(x)
            norm = logsumexp(logp, axis=1)
            resp = np.exp(logp-norm[:,None])
            counts = resp.sum(axis=0) + 1e-6
            self.weight = counts/counts.sum()
            self.mean = resp.T @ x / counts[:,None]
            self.var = np.maximum(resp.T @ (x*x)/counts[:,None] - self.mean**2, 0.05)
            score = norm.mean()
            if abs(score-last) < 1e-4:
                break
            last = score
        return self

    def log_components(self, x):
        return np.stack([np.log(w+1e-20)-0.5*np.sum(np.log(2*np.pi*v)+(x-m)**2/v, axis=1)
                         for w,m,v in zip(self.weight, self.mean, self.var)], axis=1)

    def score(self, x):
        return float(logsumexp(self.log_components(x), axis=1).mean())


class SpeakerStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest = self.root / "members.json"
        self.entries = []
        self.models = {}
        self.mean = self.std = None
        self.thresholds = {}
        if self.manifest.exists():
            self.entries = json.loads(self.manifest.read_text(encoding="utf-8"))
            if not isinstance(self.entries, list):
                raise ValueError("声纹目录格式错误")
            for entry in self.entries:
                self._path(entry)

    def _path(self, entry):
        path = (self.root / entry["file"]).resolve()
        if path.parent != self.root.resolve() or path.suffix != ".npz":
            raise ValueError("声纹样本路径无效")
        return path

    def save(self):
        temp = self.manifest.with_suffix(".tmp")
        temp.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.manifest)

    def enroll(self, name, audio):
        name = name.strip()
        if not name or len(name) > 30:
            raise ValueError("姓名需要 1～30 个字符")
        f = features(audio)
        filename = uuid.uuid4().hex + ".npz"
        np.savez_compressed(self.root / filename, features=f)
        entry = {"name": name, "file": filename, "seconds": round(len(audio)/SR, 2), "frames":len(f)}
        self.entries.append(entry)
        try:
            self.save()
        except Exception:
            self.entries.pop()
            (self.root / filename).unlink(missing_ok=True)
            raise
        self.models.clear()
        return entry

    def remove(self, name):
        old = self.entries
        self.entries = [e for e in old if e["name"] != name]
        try:
            self.save()
        except Exception:
            self.entries = old
            raise
        for e in old:
            if e["name"] == name:
                self._path(e).unlink(missing_ok=True)
        self.models.clear()

    def members(self):
        return sorted(set(e["name"] for e in self.entries))

    def train(self):
        if not self.entries:
            raise ValueError("请先注册项目成员")
        groups = {}
        for e in self.entries:
            with np.load(self._path(e), allow_pickle=False) as data:
                f = data["features"]
            if f.ndim != 2 or f.shape[1] != 26 or not np.isfinite(f).all():
                raise ValueError("声纹特征文件损坏，请删除并重新注册该成员")
            groups.setdefault(e["name"], []).append(f)
        groups = {name:np.concatenate(items) for name,items in groups.items()}
        all_f = np.concatenate(list(groups.values()))
        self.mean = all_f.mean(axis=0)
        self.std = np.maximum(all_f.std(axis=0), 0.1)
        self.models = {}
        self.thresholds = {}
        for name, f in groups.items():
            f = ((f-self.mean)/self.std).astype(np.float64)
            # Cap deterministic samples to keep registration responsive for long recordings.
            if len(f) > 12000:
                f = f[np.linspace(0, len(f)-1, 12000).astype(int)]
            model = DiagGMM().fit(f)
            self.models[name] = model
            chunks = [model.score(c) for c in np.array_split(f, min(10, max(1,len(f)//80)))]
            self.thresholds[name] = float(np.percentile(chunks, 10)-3.0)
        return len(self.models)

    def identify(self, audio, reject=False):
        if not self.models:
            self.train()
        f = (features(audio)-self.mean)/self.std
        scores = sorted(((name,model.score(f)) for name,model in self.models.items()), key=lambda v:v[1], reverse=True)
        name, score = scores[0]
        margin = score-scores[1][1] if len(scores)>1 else None
        uncertain = score < self.thresholds[name] or (margin is not None and margin < 0.3)
        # These are log likelihoods, deliberately not labelled as probabilities.
        return {"name":"未知 / 待确认" if reject and uncertain else name, "candidate":name,
                "scores":scores, "margin":margin, "uncertain":uncertain,
                "members":len(scores), "threshold":self.thresholds[name]}
