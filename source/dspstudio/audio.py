"""Microphone, speaker and synchronized acoustic-echo capture."""
import threading
from math import gcd
from . import SR, MAX_SECONDS


def read_audio(path):
    import soundfile as sf
    from .dsp import mono_audio

    info = sf.info(str(path))
    if info.duration > MAX_SECONDS:
        raise ValueError("单段音频最多 300 秒")
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return mono_audio(data, sr)


def write_audio(path, data):
    import soundfile as sf
    from .dsp import safe_output

    sf.write(str(path), safe_output(data), SR, subtype="PCM_16", format="WAV")


def resample(data, source, target):
    source, target = int(source), int(target)
    if source == target:
        import numpy as np

        return np.asarray(data, dtype=np.float32)
    from scipy.signal import resample_poly

    g = gcd(source, target)
    return resample_poly(data, target//g, source//g).astype("float32")


class AudioEngine:
    def __init__(self):
        self.stream = None
        self.lock = threading.Lock()
        self.chunks = []
        self.frames = 0
        self.level = 0.0
        self.mode = None
        self.done = False
        self.warning = ""
        self.rate = SR
        self.reference = None
        self.preview_stream = None

    @staticmethod
    def devices():
        import sounddevice as sd

        return list(sd.query_devices())

    @staticmethod
    def host_apis():
        import sounddevice as sd

        return [api["name"] for api in sd.query_hostapis()]

    def _capture(self, data, status):
        import numpy as np

        if status:
            self.warning = str(status)
        with self.lock:
            remaining = max(0, int(MAX_SECONDS*self.rate)-self.frames)
            block = data[:remaining, 0].copy()
            self.chunks.append(block)
            self.frames += len(block)
            self.level = float(np.sqrt(np.mean(block**2))) if len(block) else 0.0
            if self.frames >= MAX_SECONDS*self.rate:
                self.done = True

    def _prepare(self, mode):
        if self.stream:
            raise ValueError("请先停止当前录音")
        self.stop_playback()
        self.chunks = []
        self.frames = 0
        self.level = 0
        self.warning = ""
        self.done = False
        self.mode = mode

    def start_recording(self, device=None):
        import sounddevice as sd

        self._prepare("record")
        try:
            self.rate = int(sd.query_devices(device, "input")["default_samplerate"])
            def callback(indata, frames, time_info, status):
                self._capture(indata, status)
                if self.done:
                    raise sd.CallbackStop
            self.stream = sd.InputStream(device=device, samplerate=self.rate, channels=1,
                                         dtype="float32", callback=callback)
            self.stream.start()
        except Exception:
            self._cleanup_failed()
            raise

    def start_acoustic(self, reference, input_device=None, output_device=None, volume=0.6):
        import numpy as np
        import sounddevice as sd
        from .dsp import safe_output

        self._prepare("acoustic")
        try:
            # Try the preferred rate then common rates shared by both devices.
            preferred = int(sd.query_devices(input_device,"input")["default_samplerate"])
            chosen = None
            for rate in dict.fromkeys([preferred, 48000, 44100, 16000]):
                try:
                    sd.check_input_settings(device=input_device, channels=1, dtype="float32", samplerate=rate)
                    sd.check_output_settings(device=output_device, channels=1, dtype="float32", samplerate=rate)
                    chosen = rate
                    break
                except sd.PortAudioError:
                    continue
            if chosen is None:
                raise ValueError("输入输出设备没有共同采样率，请改用同一声卡")
            self.rate = chosen
            # Headroom and identical reference amplitude are essential to AEC.
            native = resample(safe_output(reference)*volume, SR, self.rate)
            lead = int(0.3*self.rate)
            tail = int(0.5*self.rate)
            playback = np.pad(native, (lead, tail))
            if len(playback)/self.rate > MAX_SECONDS:
                raise ValueError("声学实验参考音频需小于 299 秒")
            self.reference = resample(playback, self.rate, SR)
            cursor = 0
            def callback(indata, outdata, frames, time_info, status):
                nonlocal cursor
                count = min(frames, len(playback)-cursor)
                outdata.fill(0)
                if count > 0:
                    outdata[:count,0] = playback[cursor:cursor+count]
                    self._capture(indata[:count], status)
                cursor += count
                if cursor >= len(playback):
                    self.done = True
                    raise sd.CallbackStop
            self.stream = sd.Stream(device=(input_device,output_device), samplerate=self.rate,
                                    channels=(1,1), dtype="float32", callback=callback)
            self.stream.start()
        except Exception:
            self._cleanup_failed()
            raise

    def _cleanup_failed(self):
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
        self.stream = None
        self.mode = None

    def stop_recording(self):
        import numpy as np
        from .dsp import mono_audio

        if not self.stream:
            raise ValueError("当前没有录音")
        stream = self.stream
        try:
            stream.stop()
        finally:
            stream.close()
            self.stream = None
            self.mode = None
        with self.lock:
            if not self.chunks or self.frames == 0:
                raise ValueError("没有采集到音频，请检查麦克风权限和输入设备")
            data = np.concatenate(self.chunks)
        return mono_audio(data, self.rate)

    def play(self, data, device=None, volume=0.8, *, start_sample=0):
        import sounddevice as sd
        from .dsp import safe_output

        if self.stream:
            raise ValueError("请先停止录音再试听")
        rate = int(sd.query_devices(device,"output")["default_samplerate"])
        self.stop_playback()
        audio = safe_output(data)[start_sample:] * volume
        sd.play(resample(audio, SR, rate), samplerate=rate, device=device)
        self.preview_stream = sd.get_stream()

    def is_playing(self):
        if self.preview_stream is None:
            return False
        import sounddevice as sd

        try:
            return bool(self.preview_stream.active)
        except sd.PortAudioError:
            return False

    def stop_playback(self):
        if self.preview_stream is None:
            return
        import sounddevice as sd

        sd.stop()
        self.preview_stream = None

    def close(self):
        if self.stream:
            self.stream.abort()
            self.stream.close()
            self.stream = None
        self.mode = None
        self.stop_playback()
