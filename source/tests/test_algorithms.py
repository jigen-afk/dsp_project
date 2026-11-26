import json
import numpy as np
import pytest
from scipy import signal
from dspstudio.dsp import (SR, mono_audio, safe_output, enhance, EchoConfig, add_echo,
                           undo_echo, estimate_delay, cancel_echo, effect, rms)
from dspstudio.speaker import SpeakerStore, features, DiagGMM


def synthetic_voice(identity=0, seed=1, seconds=4):
    """Synthetic voiced test fixture, explicitly not a real person's voice."""
    rng = np.random.default_rng(seed)
    t = np.arange(round(seconds*SR))/SR
    f0 = (110 if identity==0 else 205) + 4*np.sin(2*np.pi*1.3*t) + rng.uniform(-3,3)
    phase = np.cumsum(f0)/SR
    x = sum(np.sin(2*np.pi*k*phase)/k for k in range(1,25))
    formants = (550,1400,2400) if identity==0 else (850,2100,3300)
    shaped = np.zeros(len(x))
    for formant in formants:
        b,a = signal.iirpeak(formant,6,fs=SR)
        shaped += signal.lfilter(b,a,x)
    envelope = 0.5+0.4*np.sin(2*np.pi*3.1*t+rng.uniform(0,1))**2
    shaped *= envelope
    shaped += rng.normal(0,0.0001,len(x))
    return safe_output(shaped/max(rms(shaped),1e-8)*0.12)


def snr(clean, observed):
    return 10*np.log10(np.sum(clean.astype(float)**2)/max(np.sum((clean-observed)**2),1e-20))


def test_mono_resampling_and_guardrails():
    x = np.column_stack([np.ones(48000)*0.1,np.ones(48000)*0.3])
    y = mono_audio(x,48000)
    assert len(y)==SR
    assert abs(np.mean(y[100:-100])-.2)<1e-4
    assert np.isfinite(y).all()
    for invalid in ([],[float("nan")],np.zeros((2,2,2))):
        with pytest.raises(ValueError):
            mono_audio(invalid)


@pytest.mark.parametrize("config",[EchoConfig(20,0.9,8),EchoConfig(),EchoConfig(1200,0,1)])
def test_known_echo_roundtrip_and_tail(config):
    x = synthetic_voice(seconds=2)
    y,scale = add_echo(x,config)
    assert len(y)==len(x)+round(config.delay_ms*SR/1000)*config.repeats
    assert abs(y).max()<=.98001
    restored = undo_echo(y,config,scale,len(x))
    assert np.max(abs(restored-x))<2e-6


def test_echo_invalid_parameters():
    with pytest.raises(ValueError):
        add_echo(np.zeros(1000),EchoConfig(0,1,0))


def test_enhancement_improves_known_noise_fixture():
    rng = np.random.default_rng(99)
    clean = np.pad(synthetic_voice(seconds=4),(SR//2,0))
    noise = rng.normal(0,.045,len(clean)).astype(np.float32)
    observed = clean+noise
    out = enhance(observed,1.2,.4)
    assert len(out)==len(observed)
    assert snr(clean[SR:],out[SR:]) > snr(clean[SR:],observed[SR:])+2
    assert rms(out[:SR//3]) < rms(observed[:SR//3])*.6
    assert np.array_equal(enhance(observed,0),observed)
    separate = enhance(observed,1.2,profile=noise[:SR])
    assert snr(clean[SR:],separate[SR:]) > snr(clean[SR:],observed[SR:])+2


def test_silence_and_short_noise():
    assert np.isfinite(enhance(np.zeros(SR))).all()
    assert rms(enhance(np.zeros(SR)))<1e-8
    with pytest.raises(ValueError):
        enhance(np.ones(32))
    with pytest.raises(ValueError):
        enhance(np.ones(SR),profile=np.zeros(10))


def test_nlms_alignment_echo_reduction_and_near_end_preserved():
    rng = np.random.default_rng(5)
    reference = rng.normal(0,.12,SR*5).astype(np.float32)
    delay = 1237
    h = np.zeros(80)
    h[[0,25,70]] = [.5,.15,-.08]
    echo = np.pad(signal.lfilter(h,[1],reference),(delay,0))[:len(reference)]
    near = np.zeros(len(reference),dtype=np.float32)
    near[SR*3:] = synthetic_voice(seconds=2)
    mic = (echo+near).astype(np.float32)
    assert abs(estimate_delay(mic,reference)-delay)<=1
    out,stats = cancel_echo(mic,reference,taps=128,calibration_seconds=2)
    start = SR*3
    before = rms((mic-near)[start:])
    after = rms((out-near)[start:])
    assert 20*np.log10(before/after)>35
    assert np.corrcoef(out[start:],near[start:])[0,1]>.99
    assert abs(stats["delay_ms"]-delay/SR*1000)<.1
    assert stats["calibration_reduction_db"] > 20


def test_aec_rejects_silent_reference():
    with pytest.raises(ValueError):
        cancel_echo(np.ones(SR),np.zeros(SR))


@pytest.mark.parametrize("delay_ms", [None, 50])
def test_aec_rejects_unrelated_reference(delay_ms):
    rng = np.random.default_rng(720)
    mic, reference = rng.normal(0, 0.05, (2, SR * 6)).astype(np.float32)
    with pytest.raises(ValueError, match="校准验证未测到足够的回声降低"):
        cancel_echo(mic, reference, delay_ms=delay_ms)


def test_aec_can_validate_before_a_pause_at_the_end_of_calibration():
    rng = np.random.default_rng(91)
    reference = np.zeros(SR * 3, dtype=np.float32)
    reference[:3 * SR // 2] = rng.normal(0, 0.08, 3 * SR // 2)
    _, stats = cancel_echo(reference * 0.5, reference, delay_ms=0, taps=128)
    assert stats["calibration_reduction_db"] > 20


def test_effects_duration_bandwidth_and_finite_output():
    x = synthetic_voice()
    assert abs(len(effect(x,"变速 / 变调",2))-len(x)//2)<=1
    assert abs(len(effect(x,"变速 / 变调",.5))-2*len(x))<=1
    for name,amount in [("机器人音",70),("电话音",0),("音量调整",18)]:
        out = effect(x,name,amount)
        assert len(out)==len(x)
        assert np.isfinite(out).all() and abs(out).max()<=.9801


def test_features_and_silence_rejection():
    f = features(synthetic_voice())
    assert f.shape[1]==26 and len(f)>200 and np.isfinite(f).all()
    for x in [np.zeros(SR*3),np.ones(200)]:
        with pytest.raises(ValueError):
            features(x)


def test_gmm_separates_gaussian_distributions():
    rng = np.random.default_rng(24)
    a = rng.normal(-2,1,(600,4))
    b = rng.normal(2,1,(600,4))
    ga,gb = DiagGMM().fit(a),DiagGMM().fit(b)
    heldout = rng.normal(-2,1,(100,4))
    assert ga.score(heldout)>gb.score(heldout)


def test_speaker_enrollment_holdout_restart_and_deletion(tmp_path):
    store = SpeakerStore(tmp_path)
    for identity,name in [(0,"成员甲"),(1,"成员乙")]:
        for seed in (10,20):
            store.enroll(name,synthetic_voice(identity,seed))
    assert store.train()==2
    assert store.identify(synthetic_voice(0,99))["name"]=="成员甲"
    assert store.identify(synthetic_voice(1,98))["name"]=="成员乙"
    restored = SpeakerStore(tmp_path)
    assert restored.identify(synthetic_voice(1,77))["name"]=="成员乙"
    restored.remove("成员甲")
    assert restored.members()==["成员乙"]
    assert len(list(tmp_path.glob("*.npz")))==2
    assert len(json.loads((tmp_path/"members.json").read_text(encoding="utf-8")))==2


def test_path_traversal_is_rejected(tmp_path):
    (tmp_path/"members.json").write_text(json.dumps([{"file":"../outside.npz","name":"bad"}]),encoding="utf-8")
    with pytest.raises(ValueError):
        SpeakerStore(tmp_path)
