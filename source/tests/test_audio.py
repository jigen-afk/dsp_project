import numpy as np
import soundfile as sf
from dspstudio.audio import read_audio, write_audio, AudioEngine
from dspstudio.dsp import SR


def test_real_wav_roundtrip_and_resample(tmp_path):
    t = np.arange(SR)/SR
    x = (.2*np.sin(2*np.pi*440*t)).astype(np.float32)
    path = tmp_path/"中文原音.wav"
    write_audio(path,x)
    info = sf.info(path)
    assert info.samplerate==SR and info.channels==1 and info.subtype=="PCM_16"
    assert np.max(abs(read_audio(path)-x))<4e-5
    stereo = tmp_path/"stereo.wav"
    sf.write(stereo,np.column_stack([np.ones(48000)*.1,np.ones(48000)*.3]),48000)
    assert abs(read_audio(stereo)[100:-100].mean()-.2)<1e-4


def test_capture_callback_copies_buffers_and_enforces_limit():
    engine = AudioEngine()
    engine.rate=16000
    block = np.ones((320,1),dtype=np.float32)*.1
    engine._capture(block,False)
    block.fill(0)
    assert np.all(engine.chunks[0]==np.float32(.1))
    assert engine.frames==320
    engine.frames=16000*300-10
    engine._capture(np.zeros((320,1),dtype=np.float32),False)
    assert engine.frames==16000*300 and engine.done
