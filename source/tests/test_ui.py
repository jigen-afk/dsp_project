import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import time
import numpy as np
from PySide6.QtWidgets import QApplication
from dspstudio.app import MainWindow,STYLE
from dspstudio.dsp import SR


def pump(app,window,timeout=20):
    start=time.monotonic()
    while window.task is not None:
        app.processEvents()
        time.sleep(.01)
        assert time.monotonic()-start<timeout
    app.processEvents()


def test_ui_processing_and_state_transitions(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    window = MainWindow(tmp_path)
    errors=[]
    window.error=errors.append
    window.show()
    app.processEvents()
    assert not window.enhance_button.isEnabled()
    t=np.arange(SR*3)/SR
    audio=(.12*np.sin(2*np.pi*180*t)+.05*np.sin(2*np.pi*950*t)).astype(np.float32)
    window.set_original(audio,"自动测试合成音")
    assert window.echo_button.isEnabled() and window.export_original_button.isEnabled()
    window.apply_echo()
    assert not window.record_button.isEnabled()
    pump(app,window)
    assert window.processed is not None and window.undo_echo_button.isEnabled()
    window.apply_undo_echo()
    pump(app,window)
    assert np.max(abs(window.processed-audio))<1e-6
    assert window.play_result_button.isEnabled() and not window.undo_echo_button.isEnabled()
    window.toggle_spectrum(True)
    for index in range(window.tabs.count()):
        window.tabs.setCurrentIndex(index)
        app.processEvents()
    window.effect_combo.setCurrentText("电话音")
    window.apply_effect()
    pump(app,window)
    assert len(window.processed)==len(audio)
    window.member_name.setText("界面测试成员")
    window.enroll_member()
    pump(app,window)
    assert window.member_list.count()==1
    window.identify()
    pump(app,window)
    assert "界面测试成员" in window.speaker_result.text()
    window.set_original(audio,"新原音")
    assert window.processed is None and not window.export_button.isEnabled()
    assert not errors,errors
    window.close()
    app.processEvents()
