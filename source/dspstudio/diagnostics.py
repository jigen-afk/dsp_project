"""Reproducible self checks for both source and frozen distributions."""
import json
import platform
import tempfile
import time
from pathlib import Path
import numpy as np
from scipy import signal
from .dsp import SR, rms, safe_output, enhance, EchoConfig, add_echo, undo_echo, cancel_echo
from .audio import AudioEngine, read_audio, write_audio
from .speaker import SpeakerStore


def synthetic_voice(identity=0, seed=1, seconds=4):
    rng = np.random.default_rng(seed)
    t = np.arange(round(seconds*SR))/SR
    f0 = (110 if identity==0 else 205)+4*np.sin(2*np.pi*1.3*t)+rng.uniform(-3,3)
    phase = np.cumsum(f0)/SR
    source = sum(np.sin(2*np.pi*k*phase)/k for k in range(1,25))
    formants = (550,1400,2400) if identity==0 else (850,2100,3300)
    shaped = np.zeros(len(t))
    for f in formants:
        b,a = signal.iirpeak(f,6,fs=SR)
        shaped += signal.lfilter(b,a,source)
    shaped *= .5+.4*np.sin(2*np.pi*3.1*t+rng.uniform(0,1))**2
    shaped += rng.normal(0,.0001,len(t))
    return safe_output(shaped/max(rms(shaped),1e-8)*.12)


def run_checks(hardware=False, demo_dir=None):
    report = {"software":"DSP Studio 1.0.0", "platform":platform.platform(),
              "test_material":"Synthetic deterministic signals; NOT real-member accuracy evaluation.",
              "checks":{},"passed":False}
    rng = np.random.default_rng(99)
    clean = np.pad(synthetic_voice(),(SR//2,0))
    noise = rng.normal(0,.045,len(clean)).astype(np.float32)
    noisy = clean+noise
    enhanced = enhance(noisy,1.2,.4)
    snr = lambda target,x:float(10*np.log10(np.sum(target**2)/max(np.sum((x-target)**2),1e-20)))
    before,after = snr(clean[SR:],noisy[SR:]),snr(clean[SR:],enhanced[SR:])
    report["checks"]["enhancement"]={"snr_before_db":before,"snr_after_db":after,"improvement_db":after-before,"passed":after-before>2}
    echoed,scale = add_echo(clean,EchoConfig())
    restored = undo_echo(echoed,EchoConfig(),scale,len(clean))
    max_error = float(np.max(abs(restored-clean)))
    report["checks"]["known_echo_inverse"]={"max_absolute_error":max_error,"passed":max_error<2e-6}
    reference = rng.normal(0,.12,SR*5).astype(np.float32)
    h = np.zeros(80)
    h[[0,25,70]]=[.5,.15,-.08]
    delay=1237
    acoustic_echo=np.pad(signal.lfilter(h,[1],reference),(delay,0))[:len(reference)]
    near=np.zeros(len(reference),dtype=np.float32)
    near[3*SR:]=synthetic_voice(seconds=2)
    mic=(acoustic_echo+near).astype(np.float32)
    cancelled,stats=cancel_echo(mic,reference,taps=128,calibration_seconds=2)
    suppression=20*np.log10(rms(acoustic_echo[3*SR:])/rms((cancelled-near)[3*SR:]))
    report["checks"]["nlms"]={**stats,"synthetic_echo_suppression_db":float(suppression),"passed":bool(suppression>35)}
    with tempfile.TemporaryDirectory(prefix="dsp_check_") as temp:
        store=SpeakerStore(Path(temp)/"speakers")
        for identity,name in [(0,"合成测试甲"),(1,"合成测试乙")]:
            for seed in (10,20):
                store.enroll(name,synthetic_voice(identity,seed))
        results=[]
        for identity,name in [(0,"合成测试甲"),(1,"合成测试乙")]:
            prediction=store.identify(synthetic_voice(identity,99))["name"]
            results.append({"expected":name,"prediction":prediction})
        reloaded=SpeakerStore(Path(temp)/"speakers")
        persisted=reloaded.identify(synthetic_voice(1,98))["name"]=="合成测试乙"
        report["checks"]["speaker_pipeline"]={"results":results,"restart_persistence":persisted,
            "passed":all(r["expected"]==r["prediction"] for r in results) and persisted,
            "note":"Pipeline smoke test on synthetic fixtures, not a real-voice accuracy claim."}
        path=Path(temp)/"中文路径.wav"
        write_audio(path,clean)
        error=float(np.max(abs(read_audio(path)-clean)))
        report["checks"]["wav_io"]={"max_pcm16_error":error,"passed":error<4e-5}
    # Loading algorithms alone misses failures in the frozen Qt dependency chain.
    from PySide6.QtWidgets import QApplication,QWidget
    from PySide6.QtCore import qVersion
    from sounddevice import get_portaudio_version

    audio_version, audio_version_text = get_portaudio_version()
    report["checks"]["portaudio_runtime"] = {"version": audio_version_text, "passed": audio_version > 0}
    qt_app=QApplication.instance() or QApplication(["DSPSelfCheck","-platform","offscreen"])
    widget=QWidget()
    widget.resize(320,160)
    rendered=not widget.grab().isNull()
    report["checks"]["qt_runtime"]={"qt_version":qVersion(),"widget_rendered":rendered,"passed":rendered}
    widget.close()
    qt_app.processEvents()
    if demo_dir:
        folder=Path(demo_dir)
        folder.mkdir(parents=True,exist_ok=True)
        for filename,data in [("01_合成测试原音.wav",clean),("02_合成测试含噪.wav",noisy),
                              ("03_合成测试降噪.wav",enhanced),("04_合成测试回声.wav",echoed),
                              ("05_NLMS远端参考.wav",reference),("06_NLMS麦克风混合_合成.wav",mic),
                              ("07_NLMS处理结果_合成.wav",cancelled)]:
            write_audio(folder/filename,data)
        (folder/"样例说明.txt").write_text(
            "这些文件全部是合成测试信号，不是真人语音，也不能替代真实环境采集。\n"
            "01-04 可用于试听和降噪/回声实验。\n"
            "NLMS 演示：导入 06 作为原音，导入 05 作为远端参考；自动估计延时、128 taps、校准 2 秒。\n"
            "本组说话人识别请通过麦克风录制每位实际成员的语音并注册。\n",encoding="utf-8")
    if hardware:
        engine=AudioEngine()
        try:
            devices=engine.devices()
            engine.start_recording()
            time.sleep(1.2)
            capture=engine.stop_recording()
            t=np.arange(SR//2)/SR
            tone=(.025*np.sin(2*np.pi*440*t)*np.sin(np.pi*np.arange(len(t))/len(t))**2).astype(np.float32)
            engine.play(tone,volume=.5)
            time.sleep(.7)
            engine.start_acoustic(np.tile(tone,4),volume=.3)
            started=time.monotonic()
            while not engine.done and time.monotonic()-started<8:
                time.sleep(.05)
            duplex=engine.stop_recording()
            report["checks"]["hardware"]={"device_count":len(devices),"captured_samples":len(capture),
                "capture_rms":rms(capture),"input_stream_opened":True,"output_stream_opened":True,
                "duplex_samples":len(duplex),"duplex_reference_samples":len(engine.reference),
                "passed":len(capture)>SR//2 and len(duplex)>SR*2,
                "note":"Device access verified. Does not prove audibility or real-speaker accuracy; captured audio is not saved."}
        except Exception as exc:
            report["checks"]["hardware"]={"passed":False,"error":str(exc)}
        finally:
            engine.close()
    report["passed"]=all(c["passed"] for c in report["checks"].values())
    return report


def main(args):
    import argparse
    parser=argparse.ArgumentParser(description="DSP Studio 自检（不会保存麦克风录音）")
    parser.add_argument("--report",default="self_check.json")
    parser.add_argument("--hardware",action="store_true")
    parser.add_argument("--make-demo")
    options=parser.parse_args(args)
    try:
        result=run_checks(options.hardware,options.make_demo)
    except Exception as exc:
        import traceback
        result={"passed":False,"error":str(exc),"traceback":traceback.format_exc()}
    report_path=Path(options.report)
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return 0 if result["passed"] else 1
