import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QTabWidget,
    QGroupBox, QFileDialog, QMessageBox, QLineEdit, QListWidget, QCheckBox,
    QProgressBar, QPlainTextEdit, QSplitter, QScrollArea,
)
from . import __version__
from .dsp import SR, rms, audio_stats, enhance, EchoConfig, add_echo, undo_echo, cancel_echo, effect
from .audio import AudioEngine, read_audio, write_audio
from .speaker import SpeakerStore
from .widgets import SignalPlot


def application_root():
    # Portable distribution: app/DSPStudio.exe with user data in sibling data/.
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve().parent
        return exe.parent if exe.name.lower() == "app" else exe
    return Path(__file__).resolve().parents[1]


class Task(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            result = self.function(self.progress.emit)
            self.succeeded.emit(result)
        except Exception as exc:
            logging.exception("Background task failed")
            self.failed.emit(str(exc))


STYLE = """
QWidget { background: #0b1322; color: #dce6f3; font-family: 'Microsoft YaHei UI'; font-size: 10pt; }
QMainWindow { background: #0b1322; }
QLabel#brand { font-size: 22pt; font-weight: 700; color: #f2f7ff; }
QLabel#tagline { color: #8e9fb5; }
QGroupBox { border: 1px solid #2a3b54; border-radius: 9px; margin-top: 12px; padding: 16px 12px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #91a7c4; }
QPushButton { background: #1b2b43; border: 1px solid #304763; border-radius: 6px; padding: 8px 14px; min-height: 19px; }
QPushButton:hover { background: #263c58; border-color: #5eead4; }
QPushButton:pressed { background: #34516c; }
QPushButton:disabled { color: #5d6d82; background: #142036; border-color: #1e3048; }
QPushButton#primary { background: #157f72; color: white; border-color: #299e8f; font-weight: 700; }
QPushButton#record { background: #963d55; border-color: #bb5b72; color: white; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #142237; border: 1px solid #30445f; border-radius: 5px; padding: 6px; min-height: 21px; }
QComboBox QAbstractItemView { background: #192a41; selection-background-color: #257f77; }
QTabWidget::pane { border: 1px solid #283a53; border-radius: 6px; padding: 8px; }
QTabBar::tab { background: #142137; color: #92a7c2; padding: 11px 23px; margin-right: 3px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:selected { background: #203a4b; color: #72f1d7; }
QPlainTextEdit, QListWidget { background: #101b2c; border: 1px solid #2a3b54; border-radius: 5px; padding: 5px; }
QProgressBar { background: #142237; border: none; border-radius: 4px; text-align: center; min-height: 10px; }
QProgressBar::chunk { background: #36bfa8; border-radius: 4px; }
QCheckBox { spacing: 7px; }
QStatusBar { color: #8e9fb5; }
QSplitter::handle { background: #20314a; height: 4px; }
"""


class MainWindow(QMainWindow):
    def __init__(self, root=None):
        super().__init__()
        self.root = Path(root or application_root())
        self.data_root = self.root / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.engine = AudioEngine()
        self.store = SpeakerStore(self.data_root / "speakers")
        self.original = None
        self.processed = None
        self.reference = None
        self.reference_source = None
        self.noise = None
        self.echo_metadata = None
        self.task = None
        self._closing = False
        self._task_done = None
        self._record_started = 0
        self._device_error = None
        self.setWindowTitle("数字音效实验室 · DSP Studio")
        self.resize(1200, 950)
        self.setMinimumSize(1000, 760)
        icon = QPixmap(64,64)
        icon.fill(QColor("#0b1322"))
        painter = QPainter(icon)
        painter.setPen(QColor("#5eead4"))
        painter.setFont(QFont("Segoe UI",30,QFont.Bold))
        painter.drawText(icon.rect(),Qt.AlignCenter,"∿")
        painter.end()
        self.setWindowIcon(QIcon(icon))
        self._build()
        self.refresh_devices()
        self.refresh_members()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.log("准备就绪。先录制真实语音，或导入本地音频；录音不会自动上传。")
        self.sync_controls()

    def button(self, text, slot, primary=False, name=None):
        button = QPushButton(text)
        if primary or name:
            button.setObjectName(name or "primary")
        button.clicked.connect(slot)
        return button

    def hint(self, text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setObjectName("tagline")
        return label

    def _build(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(22,16,22,12)
        outer.setSpacing(10)
        header = QHBoxLayout()
        brand = QLabel("数字音效实验室")
        brand.setObjectName("brand")
        header.addWidget(brand)
        subtitle = QLabel("DSP STUDIO  /  语音处理与声纹识别")
        subtitle.setObjectName("tagline")
        header.addWidget(subtitle)
        header.addStretch()
        header.addWidget(self.button("使用指南",self.show_help))
        outer.addLayout(header)

        devices = QHBoxLayout()
        self.input_device, self.output_device = QComboBox(), QComboBox()
        self.input_device.setMinimumWidth(190)
        self.output_device.setMinimumWidth(190)
        devices.addWidget(QLabel("麦克风"))
        devices.addWidget(self.input_device,1)
        devices.addWidget(QLabel("播放设备"))
        devices.addWidget(self.output_device,1)
        self.refresh_button = self.button("刷新设备",self.refresh_devices)
        devices.addWidget(self.refresh_button)
        self.volume = QSpinBox()
        self.volume.setRange(0,100)
        self.volume.setValue(75)
        self.volume.setSuffix(" %")
        devices.addWidget(QLabel("播放音量"))
        devices.addWidget(self.volume)
        outer.addLayout(devices)

        controls = QHBoxLayout()
        self.record_button = self.button("开始录音",self.start_recording,name="record")
        self.stop_record_button = self.button("结束录音",self.stop_recording)
        self.import_button = self.button("导入音频",self.import_audio)
        self.play_original_button = self.button("播放原音",lambda:self.play(False))
        self.play_result_button = self.button("播放结果",lambda:self.play(True))
        self.stop_play_button = self.button("停止播放",self.stop_playback)
        self.export_button = self.button("导出结果",self.export_audio)
        self.export_original_button = self.button("保存原音",lambda:self.export_audio(original=True))
        for b in (self.record_button,self.stop_record_button,self.import_button,self.play_original_button,
                  self.play_result_button,self.stop_play_button,self.export_original_button,self.export_button):
            controls.addWidget(b)
        outer.addLayout(controls)
        meter_row = QHBoxLayout()
        self.state_label = QLabel("等待输入")
        self.level_meter = QProgressBar()
        self.level_meter.setRange(0,100)
        self.level_meter.setTextVisible(False)
        self.level_meter.setMaximumWidth(180)
        self.mode_label = QLabel("16 kHz · 单声道 · 本地处理")
        self.mode_label.setObjectName("tagline")
        meter_row.addWidget(self.state_label,1)
        meter_row.addWidget(self.mode_label)
        meter_row.addWidget(QLabel("输入电平"))
        meter_row.addWidget(self.level_meter)
        outer.addLayout(meter_row)

        splitter = QSplitter(Qt.Vertical)
        plot_area = QWidget()
        pv = QVBoxLayout(plot_area)
        pv.setContentsMargins(0,0,0,0)
        plot_header = QHBoxLayout()
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(225)
        self.source_combo.addItems(["以原始语音为处理输入", "以上次结果为处理输入"])
        plot_header.addWidget(self.source_combo)
        plot_header.addStretch()
        self.spectrum_check = QCheckBox("显示频谱")
        self.spectrum_check.toggled.connect(self.toggle_spectrum)
        plot_header.addWidget(self.spectrum_check)
        pv.addLayout(plot_header)
        plots = QHBoxLayout()
        self.original_plot = SignalPlot("原始语音")
        self.result_plot = SignalPlot("处理结果", "#9daafa")
        plots.addWidget(self.original_plot)
        plots.addWidget(self.result_plot)
        pv.addLayout(plots)
        splitter.addWidget(plot_area)

        self.tabs = QTabWidget()
        for page,title in ((self.build_enhancement(),"01  语音增强"),(self.build_echo(),"02  回声实验"),
                           (self.build_speaker(),"03  成员识别"),(self.build_effects(),"04  创意音效")):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.tabs.addTab(scroll,title)
        splitter.addWidget(self.tabs)
        splitter.setSizes([225,360])
        outer.addWidget(splitter,1)

        footer = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0,100)
        self.progress.setValue(0)
        self.progress.setMaximumWidth(180)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(120)
        self.log_view.setMaximumHeight(85)
        footer.addWidget(self.log_view,1)
        footer.addWidget(self.progress)
        outer.addLayout(footer)
        self.setCentralWidget(central)
        self.statusBar().showMessage(f"DSP Studio {__version__}  |  最长 300 秒 / 段  |  录音后处理")

    def build_enhancement(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self.hint("维纳谱增益降噪：录音开头先安静片刻，再说话。也可以单独录制纯环境噪声作为噪声样本。"))
        group = QGroupBox("降噪参数")
        form = QGridLayout(group)
        self.noise_strength = QDoubleSpinBox()
        self.noise_strength.setRange(0,3)
        self.noise_strength.setSingleStep(0.1)
        self.noise_strength.setValue(1.2)
        self.noise_duration = QDoubleSpinBox()
        self.noise_duration.setRange(0.1,3)
        self.noise_duration.setSingleStep(0.1)
        self.noise_duration.setValue(0.4)
        self.noise_duration.setSuffix(" 秒")
        form.addWidget(QLabel("降噪强度"),0,0)
        form.addWidget(self.noise_strength,0,1)
        form.addWidget(QLabel("开头纯噪声时长"),0,2)
        form.addWidget(self.noise_duration,0,3)
        self.noise_button = self.button("导入纯噪声样本",self.import_noise)
        self.clear_noise_button = self.button("改用语音开头估计",self.clear_noise)
        form.addWidget(self.noise_button,1,0,1,2)
        form.addWidget(self.clear_noise_button,1,2,1,2)
        self.noise_label = self.hint("噪声来源：输入音频开头 0.4 秒（请确保这段没有说话）")
        self.noise_duration.valueChanged.connect(self.update_noise_label)
        form.addWidget(self.noise_label,2,0,1,4)
        layout.addWidget(group)
        self.enhance_button = self.button("执行语音增强",self.apply_enhance,True)
        layout.addWidget(self.enhance_button)
        layout.addWidget(self.hint("建议强度 0.8～1.5。过强可能造成失真；讲话声、突发敲击和混响通常难以完全去除。"))
        layout.addStretch()
        return page

    def build_echo(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(7)
        group = QGroupBox("可控回声 · 产生与还原")
        grid = QGridLayout(group)
        self.echo_delay = QSpinBox()
        self.echo_delay.setRange(20,1200)
        self.echo_delay.setValue(240)
        self.echo_delay.setSuffix(" ms")
        self.echo_decay = QDoubleSpinBox()
        self.echo_decay.setRange(0,0.9)
        self.echo_decay.setSingleStep(0.05)
        self.echo_decay.setValue(0.45)
        self.echo_repeats = QSpinBox()
        self.echo_repeats.setRange(1,8)
        self.echo_repeats.setValue(3)
        grid.addWidget(QLabel("延时"),0,0)
        grid.addWidget(self.echo_delay,0,1)
        grid.addWidget(QLabel("衰减"),0,2)
        grid.addWidget(self.echo_decay,0,3)
        grid.addWidget(QLabel("重复次数"),0,4)
        grid.addWidget(self.echo_repeats,0,5)
        self.echo_button = self.button("产生回声",self.apply_echo,True)
        self.undo_echo_button = self.button("还原本次生成的回声",self.apply_undo_echo)
        grid.addWidget(self.echo_button,1,0,1,3)
        grid.addWidget(self.undo_echo_button,1,3,1,3)
        layout.addWidget(group)

        acoustic = QGroupBox("参考信号回声消除 · NLMS 自适应滤波")
        ag = QGridLayout(acoustic)
        self.reference_button = self.button("导入远端参考音频",self.import_reference)
        self.acoustic_button = self.button("播放参考并采集麦克风",self.start_acoustic)
        self.reference_label = self.hint("参考：尚未导入。待处理输入应为含回声的麦克风录音。")
        ag.addWidget(self.reference_button,0,0,1,2)
        ag.addWidget(self.acoustic_button,0,2,1,2)
        ag.addWidget(self.reference_label,1,0,1,4)
        self.auto_delay = QCheckBox("自动估计延时")
        self.auto_delay.setChecked(True)
        self.aec_delay = QDoubleSpinBox()
        self.aec_delay.setRange(0,2000)
        self.aec_delay.setSuffix(" ms")
        self.aec_delay.setEnabled(False)
        self.auto_delay.toggled.connect(lambda value:self.aec_delay.setEnabled(not value))
        self.aec_taps = QComboBox()
        for n in (128,256,512,1024,2048):
            self.aec_taps.addItem(f"{n} taps ({n/SR*1000:g} ms)",n)
        self.aec_taps.setCurrentIndex(2)
        self.calibration = QDoubleSpinBox()
        self.calibration.setRange(0.5,15)
        self.calibration.setValue(2.0)
        self.calibration.setSuffix(" 秒")
        ag.addWidget(self.auto_delay,2,0)
        ag.addWidget(self.aec_delay,2,1)
        ag.addWidget(QLabel("滤波器长度"),2,2)
        ag.addWidget(self.aec_taps,2,3)
        ag.addWidget(QLabel("仅远端发声的校准时长"),3,0,1,2)
        ag.addWidget(self.calibration,3,2)
        self.aec_button = self.button("执行 NLMS 回声消除",self.apply_aec,True)
        ag.addWidget(self.aec_button,3,3)
        layout.addWidget(acoustic)
        layout.addWidget(self.hint("声学实验请用扬声器：前 2 秒本地保持安静，之后再说话。校准后冻结滤波器以保留本地语音；移动设备需重新校准。自动对齐范围 0～1200 ms。"))
        layout.addStretch()
        return page

    def build_speaker(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        left = QVBoxLayout()
        left.addWidget(QLabel("已注册项目成员"))
        self.member_list = QListWidget()
        self.member_list.setMinimumWidth(235)
        left.addWidget(self.member_list,1)
        self.delete_member_button = self.button("删除选中成员及声纹",self.delete_member)
        left.addWidget(self.delete_member_button)
        layout.addLayout(left,1)
        right = QVBoxLayout()
        right.addWidget(self.hint("使用上方“开始录音”采集成员语音。每人建议录制至少 3 段，每段 5～15 秒，读不同内容；同名注册会追加样本。注册与识别均使用左侧原始语音。"))
        row = QHBoxLayout()
        self.member_name = QLineEdit()
        self.member_name.setPlaceholderText("输入项目成员姓名")
        self.member_name.setMaxLength(30)
        row.addWidget(self.member_name,1)
        self.enroll_button = self.button("注册当前原音",self.enroll_member,True)
        row.addWidget(self.enroll_button)
        right.addLayout(row)
        self.reject_unknown = QCheckBox("启用实验性未知人拒识（阈值需用本组独立录音验证）")
        right.addWidget(self.reject_unknown)
        self.identify_button = self.button("识别当前原音中的说话人",self.identify,True)
        right.addWidget(self.identify_button)
        self.speaker_result = QLabel("等待注册与识别")
        self.speaker_result.setStyleSheet("font-size: 18pt; color: #5eead4; padding: 10px 0;")
        self.speaker_result.setWordWrap(True)
        right.addWidget(self.speaker_result)
        self.speaker_scores = QPlainTextEdit()
        self.speaker_scores.setReadOnly(True)
        self.speaker_scores.setMaximumHeight(125)
        right.addWidget(self.speaker_scores)
        right.addWidget(self.hint("算法：MFCC + Δ特征 / 对角协方差 GMM。模型分数为平均对数似然，并非准确率或身份概率。默认在本组已注册成员中选择。"))
        right.addStretch()
        layout.addLayout(right,3)
        return page

    def build_effects(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self.hint("附加音效可使用“上次结果”串联。所有操作保留原始语音，导出时保存当前处理结果。"))
        row = QHBoxLayout()
        self.effect_combo = QComboBox()
        self.effect_combo.addItems(["变速 / 变调","机器人音","电话音","音量调整"])
        self.effect_amount = QDoubleSpinBox()
        self.effect_amount_label = QLabel()
        row.addWidget(QLabel("音效"))
        row.addWidget(self.effect_combo,1)
        row.addWidget(self.effect_amount_label)
        row.addWidget(self.effect_amount)
        self.effect_button = self.button("应用音效",self.apply_effect,True)
        row.addWidget(self.effect_button)
        layout.addLayout(row)
        self.effect_description = self.hint("")
        layout.addWidget(self.effect_description)
        self.effect_combo.currentTextChanged.connect(self.effect_changed)
        self.effect_changed(self.effect_combo.currentText())
        layout.addStretch()
        return page

    def effect_changed(self, name):
        self.effect_amount.setEnabled(True)
        if name == "变速 / 变调":
            self.effect_amount_label.setText("播放倍率")
            self.effect_amount.setRange(0.5,2.0)
            self.effect_amount.setSingleStep(0.05)
            self.effect_amount.setSuffix(" ×")
            self.effect_amount.setValue(1.2)
            self.effect_description.setText("重采样同时改变音高与时长：倍率越高，声音越高、播放越快；不属于保持音高的变速。")
        elif name == "机器人音":
            self.effect_amount_label.setText("载波频率")
            self.effect_amount.setRange(20,200)
            self.effect_amount.setSingleStep(5)
            self.effect_amount.setSuffix(" Hz")
            self.effect_amount.setValue(70)
            self.effect_description.setText("正弦环形调制产生机械质感，可与回声串联试听。")
        elif name == "音量调整":
            self.effect_amount_label.setText("增益")
            self.effect_amount.setRange(-24,18)
            self.effect_amount.setSingleStep(1)
            self.effect_amount.setSuffix(" dB")
            self.effect_amount.setValue(3)
            self.effect_description.setText("调节信号增益，必要时统一衰减以避免数字削波；顶部音量仅影响试听。")
        else:
            self.effect_amount_label.setText("300～3400 Hz 带通")
            self.effect_amount.setEnabled(False)
            self.effect_amount.setSuffix("")
            self.effect_description.setText("五阶巴特沃斯带通滤波，模拟传统电话的窄带听感。")

    def log(self,text):
        self.log_view.appendPlainText(time.strftime("%H:%M:%S")+"  "+text)
        logging.info(text)

    def error(self,text):
        self.log("操作失败："+str(text))
        QMessageBox.warning(self,"操作未完成",str(text))

    def refresh_devices(self):
        old_in, old_out = self.input_device.currentData(), self.output_device.currentData()
        self.input_device.clear()
        self.output_device.clear()
        self.input_device.addItem("系统默认输入",None)
        self.output_device.addItem("系统默认输出",None)
        try:
            devices = self.engine.devices()
            for i,d in enumerate(devices):
                label = f"{i} · {d['name']}"
                if d["max_input_channels"]>0:
                    self.input_device.addItem(label,i)
                if d["max_output_channels"]>0:
                    self.output_device.addItem(label,i)
            for box,previous in ((self.input_device,old_in),(self.output_device,old_out)):
                index = box.findData(previous)
                if index>=0:
                    box.setCurrentIndex(index)
            self._device_error = None
        except Exception as exc:
            self._device_error = str(exc)
            logging.exception("Device enumeration failed")

    def sync_controls(self):
        busy = self.task is not None
        recording = self.engine.stream is not None
        idle = not busy and not recording
        has = self.original is not None
        self.record_button.setEnabled(idle)
        self.stop_record_button.setEnabled(recording)
        self.import_button.setEnabled(idle)
        self.input_device.setEnabled(idle)
        self.output_device.setEnabled(idle)
        self.refresh_button.setEnabled(idle)
        self.source_combo.setEnabled(idle)
        self.volume.setEnabled(idle)
        self.play_original_button.setEnabled(idle and has)
        self.play_result_button.setEnabled(idle and self.processed is not None)
        self.export_button.setEnabled(idle and self.processed is not None)
        self.export_original_button.setEnabled(idle and has)
        for b in (self.enhance_button,self.echo_button,self.effect_button,self.enroll_button):
            b.setEnabled(idle and has)
        self.undo_echo_button.setEnabled(idle and self.echo_metadata is not None)
        self.aec_button.setEnabled(idle and has and self.reference is not None)
        self.acoustic_button.setEnabled(idle and self.reference is not None)
        self.reference_button.setEnabled(idle)
        self.noise_button.setEnabled(idle)
        self.clear_noise_button.setEnabled(idle)
        self.identify_button.setEnabled(idle and has and bool(self.store.entries))
        self.delete_member_button.setEnabled(idle and bool(self.store.entries))

    def set_original(self,audio,title="原始语音"):
        self.original = audio
        self.processed = None
        self.echo_metadata = None
        self.source_combo.setCurrentIndex(0)
        self.original_plot.set_audio(audio,title)
        self.result_plot.set_audio(None,"处理结果")
        self.speaker_result.setText("等待识别当前原音")
        self.speaker_scores.clear()
        stats = audio_stats(audio)
        self.state_label.setText(f"{len(audio)/SR:.2f} 秒  ·  RMS {stats['rms_db']:.1f} dBFS  ·  峰值 {stats['peak']:.3f}")
        self.log(f"已载入 {title}，{len(audio)/SR:.2f} 秒")
        if rms(audio)<0.002:
            self.log("输入音量很低，请检查麦克风或靠近一些说话。")
        if stats['peak']>=0.999:
            self.log("输入峰值达到满幅，可能发生削波；建议降低麦克风增益。")
        self.sync_controls()

    def set_result(self,audio,title,metadata=None):
        self.processed = audio
        self.echo_metadata = metadata
        self.result_plot.set_audio(audio,title)
        self.log(f"{title}完成，{len(audio)/SR:.2f} 秒；可试听或导出 WAV。")
        self.sync_controls()

    def selected_audio(self):
        audio = self.processed if self.source_combo.currentIndex()==1 else self.original
        if audio is None:
            raise ValueError("所选输入为空，请先录音、导入或产生处理结果")
        return audio.copy()

    def run_task(self,label,function,done):
        if self.task or self.engine.stream:
            return
        self.stop_playback()
        self.log(label+"…")
        self.progress.setRange(0,0)
        self._task_done = done
        task = Task(function,self)
        self.task = task
        task.succeeded.connect(self._task_success)
        task.failed.connect(self._task_failure)
        task.progress.connect(self._task_progress)
        task.finished.connect(self._task_finished)
        task.start()
        self.sync_controls()

    def _task_progress(self,value):
        self.progress.setRange(0,100)
        self.progress.setValue(value)

    def _task_success(self,result):
        try:
            self._task_done(result)
        except Exception as exc:
            logging.exception("Task completion failed")
            self.error(str(exc))

    def _task_failure(self,message):
        self.error(message)

    def _task_finished(self):
        task = self.task
        self.task = None
        self._task_done = None
        if task:
            task.deleteLater()
        self.progress.setRange(0,100)
        self.progress.setValue(100)
        self.refresh_members()
        self.sync_controls()
        if self._closing:
            self.close()

    def start_recording(self):
        try:
            self.engine.start_recording(self.input_device.currentData())
            self._record_started = time.monotonic()
            self.log("录音中；降噪实验请先保持约 0.4 秒安静，随后说话。")
            self.sync_controls()
        except Exception as exc:
            self.error("无法开启麦克风："+str(exc)+"\n请检查 Windows 麦克风权限、设备连接或更换输入设备。")

    def stop_recording(self):
        if not self.engine.stream:
            return
        mode = self.engine.mode
        try:
            audio = self.engine.stop_recording()
            if mode == "acoustic":
                self.reference = self.engine.reference.copy()
                self.reference_label.setText("参考：本次同步播放信号（含预留静音）；可直接执行 NLMS。")
            self.set_original(audio,"声学实验麦克风录音" if mode=="acoustic" else "麦克风实录")
            if self.engine.warning:
                self.log("音频设备提示："+self.engine.warning+"；可能存在丢帧，请检查设备或重录。")
        except Exception as exc:
            self.error(str(exc))
        finally:
            self.level_meter.setValue(0)
            self.sync_controls()

    def start_acoustic(self):
        if self.reference is None:
            return
        try:
            playback = self.reference_source if self.reference_source is not None else self.reference
            self.engine.start_acoustic(playback,self.input_device.currentData(),self.output_device.currentData(),self.volume.value()/100)
            self._record_started = time.monotonic()
            self.log("开始播放并录音。请在校准阶段保持安静，之后再说话；播放结束自动停止。")
            self.sync_controls()
        except Exception as exc:
            self.error("声学实验无法开始："+str(exc))

    def tick(self):
        if self.engine.stream:
            elapsed = self.engine.frames/self.engine.rate
            level = self.engine.level
            self.level_meter.setValue(int(np.clip((20*np.log10(max(level,1e-5))+60)/60*100,0,100)))
            self.state_label.setText(f"● 正在采集 {elapsed:.1f} 秒  ·  输入 {20*np.log10(max(level,1e-5)):.1f} dBFS")
            if self.engine.done:
                self.stop_recording()

    def import_audio(self):
        path,_ = QFileDialog.getOpenFileName(self,"导入语音",str(self.root),"音频 (*.wav *.flac *.ogg *.aiff *.aif);;所有文件 (*)")
        if path:
            self.run_task("读取音频",lambda _:read_audio(path),lambda x:self.set_original(x,Path(path).name))

    def play(self,result):
        try:
            x = self.processed if result else self.original
            if x is None:
                return
            self.engine.play(x,self.output_device.currentData(),self.volume.value()/100)
            self.log("正在播放"+("处理结果" if result else "原始语音"))
        except Exception as exc:
            self.error("无法播放："+str(exc))

    def stop_playback(self):
        import sounddevice as sd
        sd.stop()

    def export_audio(self, checked=False, original=False):
        audio = self.original if original else self.processed
        if audio is None:
            return
        folder = self.data_root / "exports"
        folder.mkdir(parents=True,exist_ok=True)
        prefix = "原音_" if original else "音效_"
        path,_ = QFileDialog.getSaveFileName(self,"保存原音" if original else "导出处理结果",str(folder/(prefix+time.strftime("%Y%m%d_%H%M%S")+".wav")),"WAV 音频 (*.wav)")
        if path:
            try:
                if not path.lower().endswith(".wav"):
                    path += ".wav"
                write_audio(path,audio)
                self.log("已导出："+path)
            except Exception as exc:
                self.error(str(exc))

    def toggle_spectrum(self,checked):
        for plot in (self.original_plot,self.result_plot):
            plot.spectrum = checked
            plot.update()

    def import_noise(self):
        path,_ = QFileDialog.getOpenFileName(self,"导入纯环境噪声",str(self.root),"音频 (*.wav *.flac *.ogg)")
        if path:
            def done(x):
                self.noise = x
                self.noise_label.setText(f"独立噪声样本：{Path(path).name}（{len(x)/SR:.2f} 秒）")
                self.log("已设置独立噪声样本；请确认样本中没有说话。")
            self.run_task("载入噪声样本",lambda _:read_audio(path),done)

    def clear_noise(self):
        self.noise = None
        self.update_noise_label()

    def update_noise_label(self):
        if self.noise is None:
            self.noise_label.setText(f"噪声来源：输入音频开头 {self.noise_duration.value():.1f} 秒（请确保这段没有说话）")

    def apply_enhance(self):
        try:
            x = self.selected_audio()
            strength,duration = self.noise_strength.value(),self.noise_duration.value()
            noise = self.noise.copy() if self.noise is not None else None
            if noise is None and len(x)/SR < duration+0.1:
                raise ValueError("输入音频需长于噪声估计时长，请缩短噪声时长或导入独立噪声样本")
            self.run_task("执行语音增强",lambda _:enhance(x,strength,duration,noise),lambda out:self.set_result(out,"语音增强"))
        except Exception as exc:
            self.error(str(exc))

    def apply_echo(self):
        try:
            x = self.selected_audio()
            config = EchoConfig(self.echo_delay.value(),self.echo_decay.value(),self.echo_repeats.value())
            def done(result):
                out,scale = result
                self.set_result(out,"回声生成",{"config":config,"scale":scale,"length":len(x)})
            self.run_task("生成回声",lambda _:add_echo(x,config),done)
        except Exception as exc:
            self.error(str(exc))

    def apply_undo_echo(self):
        if not self.echo_metadata or self.processed is None:
            return
        meta,x = self.echo_metadata.copy(),self.processed.copy()
        self.run_task("按本次生成参数还原回声",lambda _:undo_echo(x,meta["config"],meta["scale"],meta["length"]),lambda out:self.set_result(out,"已知回声逆滤波还原"))

    def import_reference(self):
        path,_ = QFileDialog.getOpenFileName(self,"导入远端播放参考",str(self.root),"音频 (*.wav *.flac *.ogg)")
        if path:
            def done(x):
                self.reference = x
                self.reference_source = x.copy()
                self.reference_label.setText(f"参考：{Path(path).name} · {len(x)/SR:.2f} 秒")
                self.log("远端参考已载入；声学实验前请选好扬声器和麦克风。")
            self.run_task("读取远端参考",lambda _:read_audio(path),done)

    def apply_aec(self):
        try:
            x,reference = self.selected_audio(),self.reference.copy()
            delay = None if self.auto_delay.isChecked() else self.aec_delay.value()
            taps,cal = self.aec_taps.currentData(),self.calibration.value()
            def done(result):
                out,stats = result
                self.set_result(out,"NLMS 回声消除")
                self.log(f"对齐延时 {stats['delay_ms']:.2f} ms；校准后能量下降 {stats['energy_reduction_db']:.2f} dB（含本地讲话时不等同于 ERLE）。")
            self.run_task("自适应回声消除",lambda p:cancel_echo(x,reference,delay,taps,0.35,cal,p),done)
        except Exception as exc:
            self.error(str(exc))

    def refresh_members(self):
        self.member_list.clear()
        for name in self.store.members():
            entries = [e for e in self.store.entries if e["name"]==name]
            item = f"{name}  ·  {len(entries)} 段 / {sum(e['seconds'] for e in entries):.1f} 秒"
            self.member_list.addItem(item)
            self.member_list.item(self.member_list.count()-1).setData(Qt.UserRole,name)

    def enroll_member(self):
        name = self.member_name.text().strip()
        if not name:
            self.error("请先填写成员姓名")
            return
        audio = self.original.copy()
        def register(_):
            entry = self.store.enroll(name,audio)
            self.store.train()
            return entry
        def done(entry):
            self.speaker_result.setText("已注册："+entry["name"])
            self.log(f"已为 {entry['name']} 追加 {entry['seconds']:.1f} 秒声纹样本。")
        self.run_task("提取声纹并训练模型",register,done)

    def identify(self):
        audio = self.original.copy()
        reject = self.reject_unknown.isChecked()
        def done(result):
            self.speaker_result.setText("识别结果："+result["name"])
            scores = [f"{name}：{score:.3f}" for name,score in result["scores"]]
            if result["margin"] is not None:
                scores.append(f"前两名得分差：{result['margin']:.3f}")
            else:
                scores.append("当前仅注册 1 人，无法比较不同成员；请继续注册其他成员。")
            if result["uncertain"]:
                scores.append("匹配较弱或候选接近，建议补充不同句子的录音再测试。")
            scores.append("请用独立测试录音评估识别率；本结果不是认证凭据。")
            self.speaker_scores.setPlainText("\n".join(scores))
            self.log("说话人识别完成："+result["name"])
        self.run_task("识别说话人",lambda _:self.store.identify(audio,reject),done)

    def delete_member(self):
        item = self.member_list.currentItem()
        if not item:
            return
        name = item.data(Qt.UserRole)
        if QMessageBox.question(self,"删除成员",f"删除 {name} 的全部本地声纹样本？",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
            return
        try:
            self.store.remove(name)
            self.refresh_members()
            self.sync_controls()
            self.log("已删除成员："+name)
        except Exception as exc:
            self.error(str(exc))

    def apply_effect(self):
        try:
            x = self.selected_audio()
            name,amount = self.effect_combo.currentText(),self.effect_amount.value()
            self.run_task("应用"+name,lambda _:effect(x,name,amount),lambda out:self.set_result(out,name))
        except Exception as exc:
            self.error(str(exc))

    def show_help(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("使用指南")
        dialog.setText("数字音效实验室 · 快速开始")
        dialog.setInformativeText(
            "1. 选择麦克风和输出设备，点击开始录音；结束后可试听原音。\n"
            "2. 降噪：先安静 0.4 秒，再说话；或导入纯环境噪声样本。\n"
            "3. 回声：产生回声后可直接还原；真实声学消除需提供远端参考。\n"
            "4. 声学实验：导入参考，使用扬声器播放并录音；前 2 秒本地不要说话。\n"
            "5. 成员识别：录制不同句子，输入姓名并注册；每人建议至少 3 段。测试用新的录音。\n"
            "6. 创意音效支持变速变调、机器人、电话音和音量调整。\n"
            "7. 默认处理原音；选择上次结果可串联音效。导出为 16 kHz / 16 bit WAV。\n\n"
            "本软件采用录音后处理，处理任务在后台运行。麦克风输入会转成 16 kHz 单声道。\n"
            "音频不会自动保存；需保留原音请点击保存原音。声纹特征保存在 data/speakers。\n"
            "详细说明、实验方法和测试结果见软件目录中的《使用说明.md》。")
        dialog.exec()

    def closeEvent(self,event):
        if self.task is not None:
            self._closing = True
            self.log("等待当前计算任务结束后关闭。")
            event.ignore()
            return
        self.engine.close()
        event.accept()


def main():
    root = application_root()
    data = root / "data"
    try:
        data.mkdir(parents=True,exist_ok=True)
    except OSError:
        # Keep a clear startup error rather than silently writing personal data elsewhere.
        app = QApplication(sys.argv)
        QMessageBox.critical(None,"启动失败","软件目录不可写，请将整个软件文件夹放在可写位置，例如 D:/dsp。")
        return 1
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s",
        handlers=[RotatingFileHandler(data/"dspstudio.log",maxBytes=2_000_000,backupCount=2,encoding="utf-8")])
    app = QApplication(sys.argv)
    app.setApplicationName("DSPStudio")
    app.setOrganizationName("DSPLab")
    app.setFont(QFont("Microsoft YaHei UI",10))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    def exception_hook(kind,value,tb):
        logging.error("Unhandled exception",exc_info=(kind,value,tb))
        QMessageBox.critical(None,"程序错误",str(value)+"\n详情已写入 data/dspstudio.log。")
    sys.excepthook = exception_hook
    try:
        window = MainWindow(root)
    except Exception as exc:
        logging.exception("Startup failed")
        QMessageBox.critical(None,"无法启动",str(exc)+"\n如声纹数据损坏，请备份 data/speakers 后重新建立。")
        return 1
    window.show()
    return app.exec()
