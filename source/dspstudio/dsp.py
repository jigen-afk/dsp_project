"""Signal processing algorithms. All public audio arrays are mono float32 at 16 kHz."""
from dataclasses import dataclass
from math import gcd
import numpy as np
from . import SR, MAX_SECONDS


def mono_audio(data, sr=SR):
    x = np.asarray(data, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=1)
    if x.ndim != 1 or not len(x):
        raise ValueError("音频为空或声道格式不支持")
    if not np.all(np.isfinite(x)):
        raise ValueError("音频包含无效数值")
    if sr <= 0:
        raise ValueError("采样率无效")
    if len(x) / sr > MAX_SECONDS:
        raise ValueError(f"单段音频最多 {MAX_SECONDS} 秒，请先裁剪")
    if int(sr) != SR:
        from scipy.signal import resample_poly

        g = gcd(int(sr), SR)
        x = resample_poly(x, SR // g, int(sr) // g)
    # Preserve amplitude; hard clipping here would corrupt acoustic echo references.
    return x.astype(np.float32)


def rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2) + 1e-20))


def safe_output(x):
    """Only attenuate when necessary; never normalize silence into noise."""
    x = np.nan_to_num(np.asarray(x, dtype=np.float32))
    peak = float(np.max(np.abs(x), initial=0))
    return x / max(1.0, peak / 0.98)


def audio_stats(x):
    return {"seconds": len(x) / SR, "rms_db": 20 * np.log10(max(rms(x), 1e-10)),
            "peak": float(np.max(np.abs(x), initial=0))}


def enhance(x, strength=1.0, noise_seconds=0.4, profile=None):
    """STFT Wiener spectral gain; use quiet prefix or separately recorded noise."""
    x = mono_audio(x)
    if len(x) < 512:
        raise ValueError("降噪需要至少 0.032 秒音频")
    if not 0 <= strength <= 3:
        raise ValueError("降噪强度应在 0～3 之间")
    if strength == 0:
        return x.copy()
    from scipy import signal
    from scipy.ndimage import gaussian_filter

    _, _, z = signal.stft(x, fs=SR, nperseg=512, noverlap=384, boundary="zeros")
    p = np.abs(z) ** 2
    if profile is not None:
        n = mono_audio(profile)
        if len(n) < 512:
            raise ValueError("噪声样本太短，请录制至少 0.1 秒纯环境噪声")
        _, _, nz = signal.stft(n, fs=SR, nperseg=512, noverlap=384)
        noise = np.mean(np.abs(nz[:, 1:-1]) ** 2, axis=1, keepdims=True) if nz.shape[1] > 2 else np.mean(abs(nz)**2, axis=1, keepdims=True)
    else:
        # Exclude padded boundary frame; the user explicitly chooses noise-only prefix.
        frames = min(max(2, int(noise_seconds * SR / 128)), p.shape[1])
        noise = np.mean(p[:, 1:frames], axis=1, keepdims=True)
    post_snr = p / (strength * noise + 1e-12)
    gain = np.sqrt(np.maximum(1.0 - 1.0 / (post_snr + 1e-12), 0.012))
    gain = gaussian_filter(gain, sigma=(1.0, 1.0))
    _, out = signal.istft(z * gain, fs=SR, nperseg=512, noverlap=384)
    return safe_output(out[:len(x)])


@dataclass(frozen=True)
class EchoConfig:
    delay_ms: int = 240
    decay: float = 0.45
    repeats: int = 3

    def validate(self):
        if not 20 <= self.delay_ms <= 1200 or not 0 <= self.decay < 0.95 or not 1 <= self.repeats <= 8:
            raise ValueError("回声参数越界")


def echo_kernel(config):
    config.validate()
    delay = round(config.delay_ms * SR / 1000)
    h = np.zeros(delay * config.repeats + 1)
    for k in range(config.repeats + 1):
        h[k * delay] = config.decay ** k
    return h


def add_echo(x, config=EchoConfig()):
    from scipy.signal import fftconvolve

    x = mono_audio(x)
    h = echo_kernel(config)
    if (len(x) + len(h) - 1) / SR > MAX_SECONDS:
        raise ValueError("加入回声后超过 300 秒，请缩短原音或回声尾音")
    out = fftconvolve(x, h)
    scale = max(1.0, float(np.max(np.abs(out))) / 0.98)
    return (out / scale).astype(np.float32), scale


def undo_echo(x, config, scale=1.0, original_length=None):
    """Exact stable inverse of the finite geometric echo, using its known parameters."""
    config.validate()
    x = mono_audio(x).astype(np.float64) * scale
    d = round(config.delay_ms * SR / 1000)
    # (1-a z^-D)/(1-a^(K+1) z^(-(K+1)D)); sparse recurrence avoids huge lfilter FIR cost.
    out = x.copy()
    out[d:] -= config.decay * x[:-d]
    lag = (config.repeats + 1) * d
    a = config.decay ** (config.repeats + 1)
    for start in range(lag, len(out), lag):
        end = min(start + lag, len(out))
        out[start:end] += a * out[start-lag:end-lag]
    if original_length is not None:
        out = out[:original_length]
    return safe_output(out)


def estimate_delay(mic, reference, max_delay_ms=1200):
    """Positive delay of mic relative to reference, measured from first 8 seconds."""
    n = min(len(mic), len(reference), SR * 8)
    if n < 256 or rms(reference[:n]) < 1e-5:
        raise ValueError("参考信号太短或接近静音")
    from scipy.signal import correlate, resample_poly

    a = resample_poly(mic[:n], 1, 4)
    b = resample_poly(reference[:n], 1, 4)
    corr = correlate(a, b, mode="full", method="fft")
    zero = len(b) - 1
    limit = min(round(max_delay_ms * SR / 4000), len(corr) - zero - 1)
    lag = int(np.argmax(np.abs(corr[zero:zero+limit+1]))) * 4
    # Refine the estimate at original sample rate (important for white/noisy references).
    candidates = range(max(0, lag-4), lag+5)
    best = max(candidates, key=lambda k: abs(float(np.dot(mic[k:n], reference[:n-k]))))
    return best


def _aec_validation_window(aligned, start, end):
    length = min(SR // 2, (end - start) // 4)
    minimum_level = max(1e-5, 0.1 * rms(aligned[start:end]))
    for stop in (end, end - length):
        begin = stop - length
        if rms(aligned[begin:stop]) >= minimum_level:
            return begin, stop
    raise ValueError("校准后半段缺少参考声音。请使用连续有声的参考音频，或调整校准时长后重试。")


def cancel_echo(mic, reference, delay_ms=None, taps=512, mu=0.35, calibration_seconds=2.0, progress=None):
    """Time-domain NLMS; adapt on far-end-only prefix, then freeze for double talk.

    This is a controlled lab AEC, not a production full-duplex echo canceller.
    A stationary acoustic path and a far-end-only calibration interval are required.
    """
    mic, reference = mono_audio(mic), mono_audio(reference)
    if not 32 <= taps <= 2048 or not 0 < mu <= 1:
        raise ValueError("NLMS 参数无效")
    delay = estimate_delay(mic, reference) if delay_ms is None else round(delay_ms * SR / 1000)
    if delay < 0 or delay >= len(mic):
        raise ValueError("参考延时超出录音长度")
    aligned = np.zeros(len(mic), dtype=np.float64)
    take = min(len(reference), len(mic)-delay)
    aligned[delay:delay+take] = reference[:take]
    if rms(aligned) < 1e-6:
        raise ValueError("参考信号在有效区间内为静音")
    padded = np.pad(aligned, (taps-1, 0))
    weights = np.zeros(taps)
    out = np.empty(len(mic), dtype=np.float32)
    cal_end = min(len(mic), delay + round(calibration_seconds * SR))
    if cal_end - delay < SR // 2:
        raise ValueError("至少需要 0.5 秒有效校准音频")
    check_start, check_end = _aec_validation_window(aligned, delay, cal_end)
    from scipy.signal import lfilter

    calibration_reduction = 0.0
    floor = max(1e-9, rms(aligned) ** 2 * taps * 0.001)
    for i in range(len(mic)):
        if i == check_start:
            # Check the current filter on samples it has not learned from yet.
            history = max(0, i - taps + 1)
            predicted = lfilter(weights, [1], aligned[history:check_end])[-(check_end - i):]
            observed = mic[i:check_end]
            level = rms(observed)
            if level < 1e-5:
                raise ValueError("校准片段几乎静音。请检查扬声器音量、麦克风和参考音频。")
            calibration_reduction = float(20 * np.log10(level / max(rms(observed - predicted), 1e-10)))
            if calibration_reduction < 0.5:
                raise ValueError(f"校准验证未测到足够的回声降低（{calibration_reduction:.2f} dB）。"
                                 "请确认参考对应这次播放，并在校准时保持安静后重录。")
        u = padded[i:i+taps][::-1]
        error = float(mic[i]) - float(weights @ u)
        out[i] = error
        power = float(u @ u)
        if i < cal_end and power > floor:
            weights += mu * error * u / (power + 1e-8)
        if progress and i % 16000 == 0:
            progress(int(100 * i / len(mic)))
    start = min(cal_end, len(mic)-1)
    # Reduction is only an energy ratio; near-end speech makes it different from true ERLE.
    reduction = 20*np.log10(max(rms(mic[start:]), 1e-10) / max(rms(out[start:]), 1e-10))
    return safe_output(out), {"delay_ms": delay * 1000 / SR, "energy_reduction_db": float(reduction),
                              "calibration_reduction_db": calibration_reduction,
                              "calibration_seconds": calibration_seconds, "taps": taps}


def effect(x, name, amount):
    x = mono_audio(x)
    if name == "音量调整":
        return safe_output(x * (10 ** (amount / 20)))
    if name == "变速 / 变调":
        # Resampling changes pitch AND duration; UI names both explicitly.
        rate = float(amount)
        if not 0.5 <= rate <= 2:
            raise ValueError("倍率应在 0.5～2 之间")
        if len(x) / SR / rate > MAX_SECONDS:
            raise ValueError("变速后超过 300 秒")
        from scipy.signal import resample_poly

        return safe_output(resample_poly(x, 100, round(rate*100)))
    if name == "机器人音":
        carrier = np.sin(2*np.pi*float(amount)*np.arange(len(x))/SR)
        return safe_output(x * carrier)
    if name == "电话音":
        from scipy.signal import butter, sosfilt

        sos = butter(5, [300, 3400], btype="bandpass", fs=SR, output="sos")
        return safe_output(sosfilt(sos, x))
    raise ValueError("未知音效")
