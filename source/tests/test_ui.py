import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import sys
import time
import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from dspstudio.app import MainWindow, application_root, configure_appearance
from dspstudio.diagnostics import synthetic_voice
from dspstudio.dsp import SR
from dspstudio.widgets import SignalTimeline


def test_portable_data_root_is_next_to_the_outer_exe(tmp_path, monkeypatch):
    download_dir = tmp_path / "下载目录"
    extracted_exe = tmp_path / "temp" / "DSPStudio.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(extracted_exe))
    monkeypatch.setenv("DSPSTUDIO_PORTABLE_DIR", str(download_dir))
    assert application_root() == download_dir

    monkeypatch.setenv("DSPSTUDIO_PORTABLE_DIR", str(download_dir / "app"))
    assert application_root() == download_dir

    monkeypatch.delenv("DSPSTUDIO_PORTABLE_DIR")
    assert application_root() == extracted_exe.parent


def pump(app,window,timeout=20):
    start=time.monotonic()
    while window.task is not None:
        app.processEvents()
        time.sleep(.01)
        assert time.monotonic()-start<timeout
    app.processEvents()


def test_ui_processing_and_state_transitions(tmp_path):
    app = QApplication.instance() or QApplication([])
    configure_appearance(app)
    window = MainWindow(tmp_path)
    errors=[]
    window.error=errors.append
    window.show()
    app.processEvents()
    assert not window.enhance_button.isEnabled()
    t=np.arange(SR*3)/SR
    audio=(.12*np.sin(2*np.pi*180*t)+.05*np.sin(2*np.pi*950*t)).astype(np.float32)
    window.set_original(audio,"自动测试合成音")
    assert window.echo_button.isEnabled() and window.export_original_action.isEnabled()
    assert window.audition_tabs.currentIndex() == 0
    window.apply_echo()
    assert not window.record_button.isEnabled()
    pump(app,window)
    assert window.processed is not None and window.undo_echo_button.isEnabled()
    assert window.timeline.duration == len(window.processed) / SR
    window.apply_undo_echo()
    pump(app,window)
    assert np.max(abs(window.processed-audio))<1e-6
    assert window.audition_tabs.isTabEnabled(1) and not window.undo_echo_button.isEnabled()
    window.toggle_spectrum(True)
    timeline = window.timeline
    assert timeline.spectrum_reference == max(timeline.original.spectrum_db().max(), timeline.result.spectrum_db().max())
    for index in range(window.tabs.count()):
        window.tabs.setCurrentIndex(index)
        app.processEvents()
    window.effect_choices.button(2).click()
    window.apply_effect()
    pump(app,window)
    assert len(window.processed)==len(audio)
    window.set_original(synthetic_voice(0, 10), "成员甲注册录音")
    window.member_name.setText("成员甲")
    window.enroll_member()
    pump(app,window)
    assert window.member_list.count()==1
    assert not window.identify_button.isEnabled()
    window.set_original(synthetic_voice(1, 20), "成员乙注册录音")
    window.member_name.setText("成员乙")
    window.enroll_member()
    pump(app,window)
    assert window.identify_button.isEnabled()
    window.set_original(synthetic_voice(0, 99), "成员甲独立测试")
    window.identify()
    pump(app,window)
    assert "成员甲" in window.speaker_result.text()
    window.set_original(audio,"新原音")
    assert window.processed is None and not window.export_result_action.isEnabled()
    assert not errors,errors
    window.close()
    app.processEvents()


def test_compact_aec_actions_and_reference_pairing(tmp_path):
    app = QApplication.instance() or QApplication([])
    configure_appearance(app)
    window = MainWindow(tmp_path)
    audio = synthetic_voice(seconds=3)
    window.reference_source = audio
    window.set_original(audio, "同步录音", synchronized_reference=audio)
    window.tabs.setCurrentIndex(1)
    window.echo_mode.setCurrentIndex(1)
    window.show()
    assert window.aec_button.font().pointSize() == 14
    for width, height, header_row in [(1180, 780, 0), (1000, 760, 0), (820, 600, 1), (820, 760, 1), (760, 560, 1)]:
        window.resize(width, height)
        app.processEvents()
        mode_item = window.header.indexOf(window.mode_rail)
        assert window.header.getItemPosition(mode_item)[0] == header_row
        for index in range(window.mode_tabs.count()):
            assert window.mode_tabs.rect().contains(window.mode_tabs.tabRect(index))
        for name in ("record_button", "reference_button", "acoustic_button", "aec_button", "timeline", "compare_tabs",
                     "link_zoom", "audition_tabs", "play_button"):
            widget = getattr(window, name)
            assert widget.isEnabled()
            assert widget.visibleRegion().boundingRect() == widget.rect(), (width, height, name)
        assert window.aec_button.height() >= 44
    window.calibration.setValue(5)
    assert "前 5 秒" in window.calibration_hint.text()
    window.set_original(audio, "另一段录音")
    assert not window.aec_button.isEnabled()
    assert window.acoustic_button.isEnabled()
    assert "原音已更换" in window.reference_label.text()
    window.toggle_spectrum(True)
    app.processEvents()
    for widget in (window.fit_db_button, window.link_zoom, window.timeline):
        assert widget.visibleRegion().boundingRect() == widget.rect()
    window.close()


def test_playback_and_failed_task_return_to_idle(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.show()
    calls = []
    monkeypatch.setattr(window.engine, "play", lambda audio, *_, start_sample: calls.append(("play", len(audio), start_sample)))
    monkeypatch.setattr(window.engine, "stop_playback", lambda: calls.append(("stop", None)))
    monkeypatch.setattr(window.engine, "is_playing", lambda: False)
    window.set_original(synthetic_voice(seconds=3), "原音")
    window.set_result(synthetic_voice(seconds=2), "结果")
    window.play_button.click()
    assert window.play_button.text() == "停止"
    window.tick()
    assert window.play_button.text() == "播放"
    assert calls == [("play", 2 * SR, 0), ("stop", None)]
    assert window.timeline.position == 2

    def fail(_):
        raise ValueError("模拟处理错误")

    window.run_task("执行处理", fail, lambda _: None)
    pump(app, window)
    assert not window.progress.isVisible()
    assert window.operation.property("error")
    assert "模拟处理错误" in window.operation.text()
    window.close()


def test_longer_echo_replays_from_start_after_original_finishes(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    played = []
    monkeypatch.setattr(window.engine, "play", lambda audio, *_, start_sample: played.append(start_sample))
    monkeypatch.setattr(window.engine, "stop_playback", lambda: None)
    monkeypatch.setattr(window.engine, "is_playing", lambda: False)
    window.set_original(synthetic_voice(seconds=2), "原音")
    window.play_button.click()
    window.tick()
    assert window.timeline.position == 2

    window.apply_echo()
    pump(app, window)
    assert len(window.processed) > 2 * SR and window.audition_tabs.currentIndex() == 1
    window.play_button.click()
    assert played == [0, 0]
    window.stop_playback()

    window.seek_audition(2.25)
    window.play_button.click()
    assert played[-1] == round(2.25 * SR)
    window.close()


def test_timeline_zoom_scrub_and_play_from_position(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    configure_appearance(app)
    window = MainWindow(tmp_path)
    window.resize(1180, 780)
    window.set_original(synthetic_voice(seconds=4), "原音")
    window.set_result(synthetic_voice(seconds=3), "结果")
    window.show()
    app.processEvents()

    timeline = window.timeline
    ruler = timeline._geometry().ruler
    upper_track = timeline._track(timeline._geometry().lanes[0])
    point = upper_track.center()
    right_axis = QPointF(upper_track.right() + 16, point.y())

    def scroll_at(pointer, modifiers=Qt.KeyboardModifier.NoModifier):
        event = QWheelEvent(pointer, timeline.mapToGlobal(pointer), QPoint(), QPoint(0, 120),
                            Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(timeline, event)

    x, y = timeline.view
    assert not window.link_zoom.isChecked()
    scroll_at(point)
    zoomed_x, original_y = timeline.view
    assert zoomed_x.span < x.span and original_y == y
    assert zoomed_x.at(0.5) == pytest.approx(x.at(0.5))
    scroll_at(point, Qt.KeyboardModifier.ShiftModifier)
    zoomed_x = timeline.view[0]
    assert timeline.view[1] == y
    scroll_at(right_axis)
    zoomed_y = timeline.view[1]
    assert zoomed_y.span < y.span and timeline.view[0] == zoomed_x

    # Panning either lane uses the same ranges without moving the playhead.
    lower_track = timeline._track(timeline._geometry().lanes[1])
    origin = lower_track.center().toPoint()
    destination = origin + QPoint(-42, 10)
    QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=origin)
    QTest.mouseMove(timeline, destination)
    QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=destination)
    assert timeline.view[0].start > zoomed_x.start and timeline.view[1] == zoomed_y
    assert timeline.position == 0

    # Dragging the overview's left edge tightens only the time range.
    overview = timeline._geometry().overview
    view = timeline._overview_view(overview)
    old_x, old_y = timeline.view
    handle = QPoint(round(view.left()), round(view.center().y()))
    destination = handle + QPoint(23, 0)
    QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=handle)
    QTest.mouseMove(timeline, destination)
    QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=destination)
    assert timeline.view[0].start > old_x.start
    assert timeline.view[0].end == pytest.approx(old_x.end)
    assert timeline.view[1] == old_y

    # The right-hand overview also has independent, draggable scale handles.
    rail = timeline._vertical_rail(upper_track)
    selection = timeline._vertical_selection(rail, timeline._vertical_domain(old_y), old_y)
    handle = QPoint(round(rail.center().x()), round(selection.top() + 1))
    destination = handle + QPoint(0, 13)
    x_before = timeline.view[0]
    QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=handle)
    QTest.mouseMove(timeline, destination)
    QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=destination)
    assert timeline.view[0] == x_before and timeline.view[1].end < old_y.end
    assert timeline.view[1].start == pytest.approx(old_y.start)

    x_before, y_before = timeline.view
    window.link_zoom.click()
    scroll_at(right_axis)
    assert timeline.view[0].span < x_before.span and timeline.view[1].span < y_before.span
    assert timeline.view[0].span / x_before.span == pytest.approx(timeline.view[1].span / y_before.span)
    window.link_zoom.click()

    # Only the selected navigation item uses the solid blue fill.
    def tab_background(tabs, index):
        rectangle = tabs.tabRect(index)
        shot = tabs.grab()
        scale = shot.devicePixelRatio()
        return shot.toImage().pixelColor(round(scale * (rectangle.left() + 4)), round(scale * rectangle.center().y()))

    assert window.mode_tabs.font().bold() and not window.record_button.font().bold()
    assert window.audition_tabs.font().pointSize() == window.play_button.font().pointSize() == 14
    assert tab_background(window.audition_tabs, 1) == QColor("#005ac6")
    assert tab_background(window.audition_tabs, 0) != QColor("#005ac6")
    assert tab_background(window.mode_tabs, 0) == QColor("#005ac6")
    assert tab_background(window.mode_tabs, 1) != QColor("#005ac6")
    window.mode_tabs.setCurrentIndex(1)
    assert tab_background(window.mode_tabs, 1) == QColor("#005ac6")
    assert tab_background(window.mode_tabs, 0) != QColor("#005ac6")

    saved = timeline.view
    QTest.mouseClick(window.compare_tabs, Qt.MouseButton.LeftButton, pos=window.compare_tabs.tabRect(1).center())
    assert timeline.overlay and len(timeline._geometry().lanes) == 1 and timeline.view == saved
    window.toggle_spectrum(True)
    assert timeline.spectrum and timeline.overlay and timeline.view != saved
    assert tab_background(window.display_tabs, 1) == QColor("#005ac6")
    assert tab_background(window.display_tabs, 0) != QColor("#005ac6")
    window.toggle_spectrum(False)
    assert timeline.view == saved
    assert tab_background(window.display_tabs, 0) == QColor("#005ac6")

    # The ruler and position entry both seek the preview, including a seek during playback.
    QTest.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=ruler.center().toPoint())
    assert timeline.position == pytest.approx(saved[0].at(0.5), abs=0.009)
    position = window.audition_position
    position.setFocus()
    position.setText("00:01.250")
    position.setModified(True)
    QTest.keyClick(position, Qt.Key.Key_Return)
    assert timeline.position == 1.25 and timeline.view[0].start <= 1.25 <= timeline.view[0].end
    calls = []
    monkeypatch.setattr(window.engine, "play", lambda audio, *_, start_sample: calls.append(start_sample))
    monkeypatch.setattr(window.engine, "stop_playback", lambda: None)
    window.play_button.click()
    assert calls == [round(1.25 * SR)]
    window.seek_audition(2.125)
    assert calls == [round(1.25 * SR), round(2.125 * SR)]
    window.stop_playback()

    old_width = timeline.width()
    window.full_view_button.click()
    app.processEvents()
    assert not window.controls.isVisible() and timeline.width() > old_width
    window.full_view_button.click()
    app.processEvents()
    assert window.controls.isVisible()
    window.set_original(synthetic_voice(seconds=2), "新原音")
    assert timeline.view == timeline.fit and timeline.position == 0
    window.close()


def test_spectrum_fits_both_curves_and_can_restore_manual_y(tmp_path):
    app = QApplication.instance() or QApplication([])
    configure_appearance(app)
    window = MainWindow(tmp_path)
    window.show()
    window.toggle_spectrum(True)
    assert not window.fit_db_button.isEnabled()
    signal = np.random.default_rng(42).normal(0, 0.04, SR * 2).astype(np.float32)
    window.set_original(signal, "宽带输入")
    timeline = window.timeline
    original_scale = timeline.view[1]
    window.set_result(signal * 0.1, "降 20 dB")
    full_x, fitted = timeline.view
    levels = np.concatenate((timeline.original.spectrum_db(), timeline.result.spectrum_db()))
    levels -= timeline.spectrum_reference
    assert fitted.start < levels.min() and fitted.end > levels.max()
    assert 20 < fitted.span < 55 and fitted.start < original_scale.start

    timeline.zoom("x", 4, 2000)
    x, y = timeline.view
    assert x.span < full_x.span and y == fitted
    timeline.zoom("y", 2)
    manual = timeline.view[1]
    window.set_result(signal * 0.03, "更低电平")
    window.toggle_spectrum(False)
    window.toggle_spectrum(True)
    assert timeline.view == (x, manual)
    window.fit_db_button.click()
    assert timeline.view[0] == x and timeline.view[1].span > manual.span
    assert timeline.view[1].start < fitted.start
    window.set_result(signal * 0.2, "重新生成")
    assert timeline.view[0] == x and timeline.view[1].start > fitted.start
    timeline.reset_view()
    assert timeline.view == timeline.fit and timeline.view[0] == full_x
    window.close()


def test_overlay_mixes_equal_traces_regardless_of_drawing_order():
    app = QApplication.instance() or QApplication([])
    window = SignalTimeline()
    window.resize(640, 320)
    audio = np.full(400, 0.12, dtype=np.float32)
    window.set_overlay(True)
    window.set_original(audio, "测试")
    window.show()
    app.processEvents()
    track = window._track(window._geometry().lanes[0])
    x = track.center().x()
    y = window._map_y(track, window.view[1], float(audio[0]))

    def stroke():
        shot = window.grab()
        scale = shot.devicePixelRatio()
        return shot.toImage().pixelColor(round(scale * x), round(scale * y)).getRgb()[:3]

    original_color = stroke()
    window.set_result(audio, "同一条波形")
    mixed_color = stroke()
    assert mixed_color[0] > original_color[0] + 15
    assert mixed_color[1] >= max(mixed_color[0], mixed_color[2])
    window.original.color, window.result.color = window.result.color, window.original.color
    window.redraw()
    assert max(abs(a - b) for a, b in zip(stroke(), mixed_color)) <= 2
    window.close()
