import logging
import math
import sys
import time
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QTabBar,
    QBoxLayout, QButtonGroup, QDialog, QFrame, QFormLayout, QGridLayout, QFileDialog, QMessageBox, QLineEdit,
    QListWidget, QCheckBox, QMenu, QSizePolicy, QProgressBar, QPlainTextEdit,
    QScrollArea, QStackedWidget, QToolButton,
)
from .dsp import SR, rms, audio_stats, enhance, EchoConfig, add_echo, undo_echo, cancel_echo, effect
from .audio import AudioEngine, read_audio, write_audio
from .speaker import SpeakerStore
from .widgets import SignalTimeline


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
QMainWindow, QDialog { background: #f5f5f7; }
QWidget { color: #1d1d1f; }
QFrame#toolbar, QFrame#controls {
    background: #ffffff; border: 1px solid #dedee3; border-radius: 15px;
}
QFrame#monitor { background: #20222a; border-radius: 15px; }
QFrame#segments { background: #e9e9ed; border-radius: 12px; }
QFrame#monitorSegments { background: #35353c; border-radius: 11px; }
QLabel#brand { font-size: 23pt; font-weight: 600; }
QLabel#sectionTitle { font-size: 20pt; font-weight: 500; }
QLabel#sectionLabel { color: #414148; }
QLabel#hint, QLabel#metadata { color: #515159; font-size: 13pt; }
QLabel#monitorMeta { color: #c7c7ce; font-size: 12pt; }
QLabel#axisLabel { color: #515159; font-size: 12pt; }
QLabel#operation { color: #414148; font-size: 13pt; }
QLabel#operation[error="true"] { color: #a51e2b; }
QLabel#speakerResult { color: #0054b8; font-size: 17pt; }
QPushButton, QToolButton {
    background: #f4f4f6; border: 1px solid #e1e1e5; border-radius: 10px;
    padding: 7px 12px; min-height: 28px;
}
QPushButton:hover, QToolButton:hover { background: #eaeaf0; border-color: #d0d0d7; }
QPushButton:pressed, QToolButton:pressed { background: #dfdfe6; }
QPushButton:focus, QToolButton:focus { border-color: #005abc; }
QPushButton:disabled, QToolButton:disabled { background: #efeff2; color: #62626a; border-color: #e5e5e8; }
QPushButton#effectChoice:checked { background: #e8f1ff; color: #004b9e; border-color: #8bb6ee; }
QPushButton#primary { background: #005ac6; color: #ffffff; border-color: #005ac6; }
QPushButton#primary:hover { background: #004da9; border-color: #004da9; }
QPushButton#primary:pressed { background: #003d88; }
QPushButton#primary:focus { border-color: #002c62; }
QPushButton#primary:disabled { background: #e9ebef; color: #60616a; border-color: #e0e1e5; }
QPushButton#record { background: #fff0f0; color: #a51e2b; border-color: #f0cfd0; }
QPushButton#record:hover, QPushButton#record:focus { background: #fbe4e5; border-color: #bf4952; }
QPushButton#record:disabled { background: #efeff2; color: #62626a; border-color: #e5e5e8; }
QPushButton#recording { color: white; background: #aa2332; border-color: #aa2332; }
QPushButton#recording:hover, QPushButton#recording:focus { background: #8d1927; border-color: #70141f; }
QToolButton#textButton { background: transparent; border-color: transparent; color: #0054b8; padding: 3px 6px; }
QToolButton#textButton:hover, QToolButton#textButton:focus { background: #e8f1ff; border-color: #a2c4ee; }
QToolButton#exportMenu { padding-right: 25px; }
QToolButton#exportMenu::menu-indicator { subcontrol-position: right center; right: 9px; }
QMenu { background: #ffffff; border: 1px solid #dedee3; padding: 5px; }
QMenu::item { padding: 8px 20px; }
QMenu::item:selected { background: #e8f1ff; color: #003e85; }
QMenu::item:disabled { color: #62626a; }
QToolButton#plotFocus {
    background: #30323c; color: #e1e3eb; border-color: #444753;
    min-height: 24px; padding: 4px 9px; font-size: 12pt;
}
QToolButton#plotFocus:hover, QToolButton#plotFocus:checked { background: #494d60; border-color: #7883a0; }
QToolButton#axisTool { min-height: 22px; padding: 4px 7px; font-size: 13pt; }
QToolButton#spectrumFit { min-height: 22px; padding: 4px 3px; font-size: 12pt; color: #0054ac; }
QToolButton#zoomLink { min-height: 22px; padding: 4px 6px; font-size: 12pt; }
QToolButton#zoomLink:checked { background: #e0edff; color: #004786; border-color: #84ade2; }
QPushButton#timelinePlay {
    color: white; background: #005ac6; border-color: #005ac6;
    min-height: 24px; padding: 4px 11px;
}
QPushButton#timelinePlay:hover { background: #004da9; }
QPushButton#timelinePlay:focus { border-color: #002c62; }
QPushButton#timelinePlay:disabled { background: #e9ebef; color: #60616a; border-color: #e0e1e5; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #ffffff; border: 1px solid #c9c9d1; border-radius: 9px;
    padding: 5px 9px; min-height: 25px; selection-background-color: #dbeaff;
    selection-color: #1d1d1f;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #005abc; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { background: #f0f0f3; color: #60616a; }
QLineEdit#timelinePosition { padding: 3px 6px; min-height: 23px; font-size: 12pt; }
QComboBox { padding-right: 10px; }
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: top right; width: 25px;
    background: #f4f4f6; border-top-right-radius: 8px; border-bottom-right-radius: 8px;
}
QComboBox::down-arrow {
    image: url(:/qt-project.org/styles/commonstyle/images/arrow-down-16.png);
    width: 12px; height: 12px;
}
QSpinBox, QDoubleSpinBox { padding-right: 9px; }
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: padding; subcontrol-position: top right; width: 23px;
    background: #f4f4f6; border-top-right-radius: 8px; border-bottom: 1px solid #dedee3;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: padding; subcontrol-position: bottom right; width: 23px;
    background: #f4f4f6; border-bottom-right-radius: 8px;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url(:/qt-project.org/styles/commonstyle/images/arrow-up-16.png);
    width: 10px; height: 10px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url(:/qt-project.org/styles/commonstyle/images/arrow-down-16.png);
    width: 10px; height: 10px;
}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled,
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled { image: none; }
QComboBox::drop-down:hover, QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background: #e5e5ea; }
QComboBox QAbstractItemView { background: #ffffff; selection-background-color: #e8f1ff; selection-color: #003e85; }
QTabBar::tab {
    background: transparent; color: #44444c; border: 1px solid transparent;
    border-radius: 9px; padding: 5px 11px; min-height: 26px;
}
QTabBar#modeTabs { font-weight: 700; }
QTabBar#modeTabs::tab { padding: 6px 13px; min-height: 30px; }
QTabBar::tab:hover { background: #dedee4; }
QTabBar::tab:selected { background: #ffffff; color: #004fa9; border-color: #d1d1d8; }
QTabBar::tab:focus { border-color: #005abc; }
QTabBar#modeTabs::tab:selected { background: #005ac6; color: #ffffff; border-color: #005ac6; }
QTabBar#modeTabs::tab:selected:focus { border-color: #002c62; }
QTabBar::tab:disabled { color: #60616a; }
QTabBar::tab:selected:disabled { background: transparent; border-color: transparent; color: #60616a; }
QTabBar#auditionTabs::tab { padding: 3px 7px; min-height: 22px; }
QTabBar#auditionTabs::tab:selected { background: #005ac6; color: #ffffff; border-color: #005ac6; }
QTabBar#auditionTabs::tab:selected:disabled { background: transparent; color: #60616a; border-color: transparent; }
QTabBar#displayTabs::tab, QTabBar#compareTabs::tab {
    padding: 3px 7px; min-height: 23px; color: #e1e1e8; font-size: 12pt;
}
QTabBar#displayTabs::tab { font-size: 13pt; }
QTabBar#displayTabs::tab:hover, QTabBar#compareTabs::tab:hover { background: #474850; }
QTabBar#displayTabs::tab:selected { background: #005ac6; color: #ffffff; border-color: #005ac6; }
QTabBar#compareTabs::tab:selected {
    background: #60636f; color: #ffffff; border-color: #777a88;
}
QTabBar#displayTabs::tab:focus, QTabBar#compareTabs::tab:focus { border-color: #a0c6ff; }
QPlainTextEdit, QListWidget {
    background: #f5f5f7; border: 1px solid #dedee3; border-radius: 9px; padding: 5px;
    selection-background-color: #dbeaff; selection-color: #003e85;
}
QListWidget::item { padding: 6px; }
QProgressBar { background: #e2e2e8; border: 0; border-radius: 5px; text-align: center; min-height: 10px; }
QProgressBar::chunk { background: #005ac6; border-radius: 5px; }
QProgressBar#inputMeter::chunk { background: #aa2332; }
QCheckBox { spacing: 8px; min-height: 28px; }
QCheckBox::indicator { width: 17px; height: 17px; }
QScrollArea { border: none; background: transparent; }
QWidget#controlForm { background: #ffffff; }
QScrollBar:vertical { background: #f1f1f4; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #a1a1ac; min-height: 29px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


def configure_appearance(app):
    app.setFont(QFont("Microsoft YaHei UI", 14))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)


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
        self.reference_is_synchronized = False
        self.noise = None
        self.noise_name = ""
        self.original_title = ""
        self.echo_metadata = None
        self.task = None
        self._playing = False
        self._preview_started = 0.0
        self._preview_offset = 0
        self._preview_length = 0
        self._preview_finished = False
        self._follow_preview = False
        self._closing = False
        self._task_done = None
        self._device_error = None
        self.setWindowTitle("数字音效实验室 · DSP Studio")
        available = self.screen().availableGeometry().size()
        self.resize(min(1240, available.width() - 24), min(800, available.height() - 48))
        self.setMinimumSize(760, 560)
        icon = QPixmap(64,64)
        icon.fill(QColor("#223f9e"))
        painter = QPainter(icon)
        painter.setPen(QColor("#ffffff"))
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
        self.log("准备就绪，可以开始录音或导入音频。")
        self.sync_controls()

    def button(self, text, slot, primary=False, name=None):
        button = QPushButton(text)
        if primary or name:
            button.setObjectName(name or "primary")
        button.clicked.connect(slot)
        return button

    def hint(self, text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setObjectName("hint")
        return label

    @staticmethod
    def tab_bar(labels, name="choiceTabs"):
        tabs = QTabBar()
        tabs.setObjectName(name)
        tabs.setExpanding(False)
        tabs.setDrawBase(False)
        tabs.setUsesScrollButtons(False)
        tabs.setElideMode(Qt.TextElideMode.ElideNone)
        for label in labels:
            tabs.addTab(label)
        return tabs

    @staticmethod
    def segmented(tabs):
        dark = tabs.objectName() in ("displayTabs", "compareTabs")
        rail = MainWindow.frame("monitorSegments" if dark else "segments")
        if dark:
            rail.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(rail)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.addWidget(tabs)
        return rail

    @staticmethod
    def frame(name):
        frame = QFrame()
        frame.setObjectName(name)
        return frame

    @staticmethod
    def section_label(text):
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _build(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(20, 14, 20, 9)
        outer.setSpacing(10)

        self.header_bar = QWidget()
        self.header_bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.header = QGridLayout(self.header_bar)
        self.header.setContentsMargins(0, 0, 0, 0)
        self.header.setHorizontalSpacing(12)
        self.header.setVerticalSpacing(7)
        self.header.setColumnStretch(1, 1)
        self.brand = QLabel("数字音效实验室")
        self.brand.setObjectName("brand")
        self.header.addWidget(self.brand, 0, 0)
        self.mode_tabs = self.tab_bar(("语音增强", "回声处理", "成员识别", "附加音效"), "modeTabs")
        self.mode_rail = self.segmented(self.mode_tabs)
        self._compact_header = None

        self.build_device_dialog()
        self.header_actions = QWidget()
        header_actions = QHBoxLayout(self.header_actions)
        header_actions.setContentsMargins(0, 0, 0, 0)
        header_actions.setSpacing(7)
        self.device_button = self.button("音频设备", self.device_dialog.exec)
        header_actions.addWidget(self.device_button)
        header_actions.addWidget(self.button("指南", self.show_help))
        self.header.addWidget(self.header_actions, 0, 2)
        outer.addWidget(self.header_bar)

        capture = self.frame("toolbar")
        capture_layout = QHBoxLayout(capture)
        capture_layout.setContentsMargins(10, 7, 10, 7)
        capture_layout.setSpacing(8)
        self.record_button = self.button("开始录音", self.start_recording, name="record")
        self.stop_record_button = self.button("结束录音", self.stop_recording, name="recording")
        self.record_controls = QStackedWidget()
        self.record_controls.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.record_controls.addWidget(self.record_button)
        self.record_controls.addWidget(self.stop_record_button)
        capture_layout.addWidget(self.record_controls)
        self.import_button = self.button("导入音频", self.import_audio)
        capture_layout.addWidget(self.import_button)
        self.state_label = QLabel("尚未载入音频")
        self.state_label.setObjectName("metadata")
        self.state_label.setTextFormat(Qt.TextFormat.PlainText)
        self.state_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        capture_layout.addWidget(self.state_label, 1)
        self.level_meter = QProgressBar()
        self.level_meter.setObjectName("inputMeter")
        self.level_meter.setRange(0, 100)
        self.level_meter.setTextVisible(False)
        self.level_meter.setFixedSize(88, 11)
        self.level_meter.setAccessibleName("麦克风输入电平")
        self.level_meter.hide()
        capture_layout.addWidget(self.level_meter)

        self.export_button = QToolButton()
        self.export_button.setObjectName("exportMenu")
        self.export_button.setText("保存与导出")
        self.export_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        export_menu = QMenu(self.export_button)
        self.export_original_action = export_menu.addAction("保存原音 WAV", lambda: self.export_audio(original=True))
        self.export_result_action = export_menu.addAction("导出结果 WAV", self.export_audio)
        self.export_button.setMenu(export_menu)
        capture_layout.addWidget(self.export_button)
        outer.addWidget(capture)

        self.workspace = QHBoxLayout()
        self.workspace.setSpacing(12)
        self.workspace.addWidget(self.build_monitor(), 1)

        self.controls = self.frame("controls")
        self.controls.setFixedWidth(359)
        panel = QVBoxLayout(self.controls)
        panel.setContentsMargins(13, 10, 13, 11)
        panel.setSpacing(8)
        self.mode_title = QLabel("语音增强")
        self.mode_title.setObjectName("sectionTitle")
        panel.addWidget(self.mode_title)
        self.source_row = QWidget()
        input_row = QHBoxLayout(self.source_row)
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.addWidget(self.section_label("处理输入"))
        self.source_combo = QComboBox()
        self.source_combo.setAccessibleName("处理输入")
        self.source_combo.addItems(("原始录音", "接着处理上次结果"))
        input_row.addWidget(self.source_combo, 1)
        panel.addWidget(self.source_row)
        self.tabs = QStackedWidget()
        for page in (self.build_enhancement(), self.build_echo(), self.build_speaker(), self.build_effects()):
            self.tabs.addWidget(page)
        panel.addWidget(self.tabs, 1)
        self.mode_tabs.currentChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.mode_tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.sync_controls)
        self.workspace.addWidget(self.controls)
        outer.addLayout(self.workspace, 1)

        footer = QHBoxLayout()
        self.operation = QLabel()
        self.operation.setObjectName("operation")
        self.operation.setTextFormat(Qt.TextFormat.PlainText)
        self.operation.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.operation.setWordWrap(True)
        self.operation.setMaximumHeight(48)
        footer.addWidget(self.operation, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(100)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("处理进度")
        self.progress.hide()
        footer.addWidget(self.progress)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(120)
        self.log_view.setFixedHeight(83)
        footer.addWidget(self.disclosure("日志", self.log_view))
        outer.addLayout(footer)
        outer.addWidget(self.log_view)
        self.setCentralWidget(central)
        self.source_combo.currentIndexChanged.connect(self.sync_controls)
        self.update_layout()

    def update_layout(self):
        header_width = sum(widget.sizeHint().width() for widget in (self.brand, self.mode_rail, self.header_actions))
        compact_header = self.width() < header_width + 40 + 2 * self.header.horizontalSpacing()
        if compact_header != self._compact_header:
            self._compact_header = compact_header
            self.header.removeWidget(self.mode_rail)
            if compact_header:
                self.header.addWidget(self.mode_rail, 1, 0, 1, 3, Qt.AlignmentFlag.AlignHCenter)
            else:
                self.header.addWidget(self.mode_rail, 0, 1, Qt.AlignmentFlag.AlignHCenter)
            self.header_bar.setFixedHeight(self.header.sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "workspace"):
            self.update_layout()

    def build_device_dialog(self):
        self.device_dialog = QDialog(self)
        self.device_dialog.setWindowTitle("音频设备")
        self.device_dialog.setMinimumWidth(480)
        layout = QVBoxLayout(self.device_dialog)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        form = QFormLayout()
        self.input_device, self.output_device = QComboBox(), QComboBox()
        for box in (self.input_device, self.output_device):
            box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            box.setMinimumContentsLength(28)
            box.currentIndexChanged.connect(self.update_device_summary)
        form.addRow("麦克风", self.input_device)
        form.addRow("播放设备", self.output_device)
        self.volume = QSpinBox()
        self.volume.setRange(0, 100)
        self.volume.setValue(75)
        self.volume.setSuffix(" %")
        self.volume.setToolTip("调整试听和参考播放的音量")
        form.addRow("播放音量", self.volume)
        layout.addLayout(form)
        self.device_info = self.hint("")
        layout.addWidget(self.device_info)
        actions = QHBoxLayout()
        self.refresh_button = self.button("刷新设备", self.refresh_devices)
        actions.addWidget(self.refresh_button)
        actions.addStretch()
        actions.addWidget(self.button("完成", self.device_dialog.accept, True))
        layout.addLayout(actions)

    def build_monitor(self):
        monitor = QWidget()
        layout = QVBoxLayout(monitor)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        panel = self.frame("monitor")
        signals = QVBoxLayout(panel)
        signals.setContentsMargins(9, 7, 9, 6)
        signals.setSpacing(6)
        header = QHBoxLayout()
        header.setSpacing(6)
        self.display_tabs = self.tab_bar(("波形", "频谱"), "displayTabs")
        self.display_tabs.setAccessibleName("信号显示")
        header.addWidget(self.segmented(self.display_tabs))
        header.addStretch(1)
        self.monitor_meta = QLabel("16 kHz · 双轨同步")
        self.monitor_meta.setObjectName("monitorMeta")
        self.monitor_meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.monitor_meta)
        header.addStretch(1)
        self.compare_tabs = self.tab_bar(("上下", "叠加"), "compareTabs")
        self.compare_tabs.setAccessibleName("原音与结果的对比方式")
        self.compare_tabs.setTabToolTip(0, "上下两轨共用坐标和播放位置")
        self.compare_tabs.setTabToolTip(1, "两条信号共用一块画布，重合处混色")
        header.addWidget(self.segmented(self.compare_tabs))
        self.full_view_button = QToolButton()
        self.full_view_button.setObjectName("plotFocus")
        self.full_view_button.setText("展开")
        self.full_view_button.setToolTip("展开图表，收起功能导航和参数面板")
        self.full_view_button.setCheckable(True)
        self.full_view_button.toggled.connect(self.toggle_full_view)
        header.addWidget(self.full_view_button)
        signals.addLayout(header)
        self.timeline = SignalTimeline()
        self.timeline.positionChanged.connect(self.display_position)
        self.timeline.viewChanged.connect(self.pause_preview_following)
        self.timeline.seekRequested.connect(self.seek_audition)
        self.timeline.playToggleRequested.connect(self.toggle_playback)
        self.display_tabs.currentChanged.connect(lambda index: self.toggle_spectrum(index == 1))
        self.compare_tabs.currentChanged.connect(lambda index: self.timeline.set_overlay(index == 1))
        signals.addWidget(self.timeline, 1)
        layout.addWidget(panel, 1)

        footer = self.frame("toolbar")
        footer.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.timeline_tools = QBoxLayout(QBoxLayout.Direction.TopToBottom, footer)
        self.timeline_tools.setContentsMargins(9, 5, 9, 5)
        self.timeline_tools.setSpacing(6)

        self.zoom_controls = QWidget()
        zoom_layout = QHBoxLayout(self.zoom_controls)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        zoom_layout.setSpacing(3)
        self.x_axis_label = self.add_zoom_buttons(zoom_layout, "时间", "x")
        zoom_layout.addSpacing(4)
        self.y_axis_label = self.add_zoom_buttons(zoom_layout, "幅度", "y")
        self.fit_db_button = QToolButton()
        self.fit_db_button.setObjectName("spectrumFit")
        self.fit_db_button.setText("自动")
        self.fit_db_button.setAccessibleName("自动适配频谱纵轴")
        self.fit_db_button.setToolTip("用可见频段内两条频谱的范围，自动适配共同的 dB 纵轴。")
        self.fit_db_button.clicked.connect(self.timeline.fit_spectrum_y)
        self.fit_db_button.hide()
        zoom_layout.insertWidget(zoom_layout.indexOf(self.y_axis_label), self.fit_db_button)
        zoom_layout.addSpacing(4)
        fit = QToolButton()
        fit.setObjectName("axisTool")
        fit.setText("全览")
        fit.setToolTip("同时复位横轴和纵轴，显示全段")
        fit.clicked.connect(lambda: self.timeline.reset_view())
        zoom_layout.addWidget(fit)
        self.link_zoom = QToolButton()
        self.link_zoom.setObjectName("zoomLink")
        self.link_zoom.setText("联动")
        self.link_zoom.setAccessibleName("联动缩放")
        self.link_zoom.setToolTip("联动缩放。开启后调任一轴时，两轴一起缩放。")
        self.link_zoom.setCheckable(True)
        self.link_zoom.toggled.connect(self.timeline.set_zoom_linked)
        zoom_layout.addWidget(self.link_zoom)
        zoom_layout.addStretch(1)
        self.timeline_tools.addWidget(self.zoom_controls)

        self.audition_controls = QWidget()
        audition_layout = QHBoxLayout(self.audition_controls)
        audition_layout.setContentsMargins(0, 0, 0, 0)
        audition_layout.setSpacing(5)
        self.audition_tabs = self.tab_bar(("原音", "结果"), "auditionTabs")
        self.audition_tabs.setAccessibleName("试听对象")
        self.audition_tabs.currentChanged.connect(self.audition_changed)
        audition_layout.addWidget(self.segmented(self.audition_tabs))
        self.play_button = self.button("播放", self.toggle_playback, name="timelinePlay")
        self.play_button.setMinimumWidth(66)
        audition_layout.addWidget(self.play_button)
        self.audition_position = QLineEdit("00:00.000")
        self.audition_position.setObjectName("timelinePosition")
        self.audition_position.setAccessibleName("播放位置，可输入时间跳转")
        self.audition_position.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.audition_position.setMaxLength(14)
        self.audition_position.setFixedWidth(99)
        self.audition_position.editingFinished.connect(self.commit_position)
        audition_layout.addWidget(self.audition_position)
        self.timeline_tools.addWidget(self.audition_controls, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(footer)
        self.timeline.resized.connect(self.update_timeline_tools)
        return monitor

    def add_zoom_buttons(self, layout, text, axis):
        title = QLabel(text)
        title.setObjectName("axisLabel")
        layout.addWidget(title)
        axis_name = "横轴" if axis == "x" else "纵轴"
        for symbol, change, description in (("−", 1 / 1.4, "缩小"), ("+", 1.4, "放大")):
            button = QToolButton()
            button.setObjectName("axisTool")
            button.setText(symbol)
            button.setAccessibleName(axis_name + description)
            button.setToolTip(axis_name + description)
            button.setFixedWidth(27)
            button.clicked.connect(lambda checked=False, factor=change: self.timeline.zoom(axis, factor))
            layout.addWidget(button)
        return title

    def update_timeline_tools(self, width):
        required = (self.audition_controls.sizeHint().width() + self.zoom_controls.sizeHint().width()
                    + self.timeline_tools.spacing())
        direction = QBoxLayout.Direction.LeftToRight if width >= required else QBoxLayout.Direction.TopToBottom
        if self.timeline_tools.direction() != direction:
            self.timeline_tools.setDirection(direction)
        self.monitor_meta.setVisible(width >= 475)

    def toggle_full_view(self, expanded):
        self.controls.setVisible(not expanded)
        self.header_bar.setVisible(not expanded)
        self.full_view_button.setText("还原" if expanded else "展开")
        self.full_view_button.setToolTip("恢复功能导航和参数面板" if expanded else "展开图表，收起功能导航和参数面板")

    @staticmethod
    def disclosure(title, widget):
        button = QToolButton()
        button.setText(title)
        button.setObjectName("textButton")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setArrowType(Qt.ArrowType.RightArrow)
        button.setCheckable(True)
        widget.hide()
        button.toggled.connect(widget.setVisible)
        button.toggled.connect(lambda opened: button.setArrowType(
            Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow))
        return button

    @staticmethod
    def mode_page():
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        return page, layout

    @staticmethod
    def scroll_form(layout):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(48)
        body = QWidget()
        body.setObjectName("controlForm")
        container = QVBoxLayout(body)
        container.setContentsMargins(1, 3, 5, 4)
        form = QVBoxLayout()
        form.setSpacing(7)
        container.addLayout(form)
        container.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        return form

    def build_enhancement(self):
        page, layout = self.mode_page()
        form = self.scroll_form(layout)
        form.addWidget(self.hint("用纯噪声估计背景，减弱风扇和空调等稳定噪声。"))
        form.addWidget(self.section_label("噪声来源"))
        self.noise_mode = self.tab_bar(("录音开头", "独立噪声"))
        form.addWidget(self.segmented(self.noise_mode), 0, Qt.AlignmentFlag.AlignLeft)
        self.noise_options = QStackedWidget()

        prefix = QWidget()
        prefix_layout = QFormLayout(prefix)
        prefix_layout.setContentsMargins(0, 0, 0, 0)
        self.noise_duration = QDoubleSpinBox()
        self.noise_duration.setRange(0.1, 3)
        self.noise_duration.setSingleStep(0.1)
        self.noise_duration.setValue(0.4)
        self.noise_duration.setSuffix(" 秒")
        self.noise_duration.setMaximumWidth(132)
        prefix_layout.addRow("开头纯噪声时长", self.noise_duration)
        self.noise_options.addWidget(prefix)

        profile = QWidget()
        profile_layout = QHBoxLayout(profile)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        self.noise_button = self.button("导入纯噪声样本", self.import_noise)
        profile_layout.addWidget(self.noise_button)
        profile_layout.addStretch()
        self.noise_options.addWidget(profile)
        form.addWidget(self.noise_options)
        self.noise_label = self.hint("录音开始时，前 0.4 秒保持安静。")
        form.addWidget(self.noise_label)

        parameters = QFormLayout()
        self.noise_strength = QDoubleSpinBox()
        self.noise_strength.setRange(0, 3)
        self.noise_strength.setSingleStep(0.1)
        self.noise_strength.setValue(1.2)
        self.noise_strength.setMaximumWidth(132)
        self.noise_strength.setToolTip("建议 0.8～1.5，强度过高可能损伤部分语音")
        parameters.addRow("降噪强度", self.noise_strength)
        form.addLayout(parameters)
        self.noise_mode.currentChanged.connect(self.noise_options.setCurrentIndex)
        self.noise_mode.currentChanged.connect(self.update_noise_label)
        self.noise_mode.currentChanged.connect(self.sync_controls)
        self.noise_duration.valueChanged.connect(self.update_noise_label)
        self.enhance_button = self.button("执行语音增强", self.apply_enhance, True)
        layout.addWidget(self.enhance_button)
        return page

    def build_echo(self):
        page, layout = self.mode_page()
        self.echo_mode = self.tab_bar(("回声音效", "声学回声消除"))
        layout.addWidget(self.segmented(self.echo_mode), 0, Qt.AlignmentFlag.AlignLeft)
        modes = QStackedWidget()
        effect_page = QWidget()
        effect_layout = QVBoxLayout(effect_page)
        effect_layout.setContentsMargins(0, 0, 0, 0)
        effect_layout.setSpacing(6)
        form = self.scroll_form(effect_layout)
        form.addWidget(self.hint("把延时、衰减后的语音叠加到原音，随后可以按本次参数还原。"))
        params = QFormLayout()
        self.echo_delay = QSpinBox()
        self.echo_delay.setRange(20, 1200)
        self.echo_delay.setValue(240)
        self.echo_delay.setSuffix(" ms")
        self.echo_delay.setMaximumWidth(132)
        params.addRow("延时", self.echo_delay)
        self.echo_decay = QDoubleSpinBox()
        self.echo_decay.setRange(0, 0.9)
        self.echo_decay.setSingleStep(0.05)
        self.echo_decay.setValue(0.45)
        self.echo_decay.setMaximumWidth(132)
        params.addRow("衰减", self.echo_decay)
        self.echo_repeats = QSpinBox()
        self.echo_repeats.setRange(1, 8)
        self.echo_repeats.setValue(3)
        self.echo_repeats.setMaximumWidth(132)
        params.addRow("重复次数", self.echo_repeats)
        form.addLayout(params)
        actions = QHBoxLayout()
        self.echo_button = self.button("产生回声", self.apply_echo, True)
        self.undo_echo_button = self.button("还原本次回声", self.apply_undo_echo)
        actions.addWidget(self.echo_button, 1)
        actions.addWidget(self.undo_echo_button)
        effect_layout.addLayout(actions)
        modes.addWidget(effect_page)

        acoustic = QWidget()
        acoustic_layout = QVBoxLayout(acoustic)
        acoustic_layout.setContentsMargins(0, 0, 0, 0)
        acoustic_layout.setSpacing(6)
        self.reference_button = self.button("导入远端参考", self.import_reference)
        acoustic_layout.addWidget(self.reference_button)
        form = self.scroll_form(acoustic_layout)
        self.reference_label = self.hint("导入扬声器的参考音频，再同步录制或使用对应的麦克风录音。")
        form.addWidget(self.reference_label)

        self.calibration = QDoubleSpinBox()
        self.calibration.setRange(0.5, 15)
        self.calibration.setValue(2.0)
        self.calibration.setSuffix(" 秒")
        self.calibration.setMaximumWidth(132)
        self.auto_delay = QCheckBox("自动估计参考延时")
        self.auto_delay.setChecked(True)
        self.aec_delay = QDoubleSpinBox()
        self.aec_delay.setRange(0, 2000)
        self.aec_delay.setSuffix(" ms")
        self.aec_delay.setMaximumWidth(132)
        self.aec_delay.setEnabled(False)
        self.auto_delay.toggled.connect(lambda auto: self.aec_delay.setEnabled(not auto))
        self.aec_taps = QComboBox()
        for taps in (128, 256, 512, 1024, 2048):
            self.aec_taps.addItem(f"{taps} 抽头 · {taps / SR * 1000:g} ms", taps)
        self.aec_taps.setCurrentIndex(2)
        self.aec_taps.setMaximumWidth(212)
        self.calibration_hint = self.hint("")
        self.calibration.valueChanged.connect(self.update_calibration_hint)
        self.update_calibration_hint()
        form.addWidget(self.calibration_hint)
        params = QFormLayout()
        params.addRow("校准时长", self.calibration)
        params.addRow("滤波器长度", self.aec_taps)
        params.addRow(self.auto_delay)
        params.addRow("手动延时", self.aec_delay)
        form.addLayout(params)
        self.acoustic_button = self.button("播放并录音", self.start_acoustic)
        self.acoustic_button.setAccessibleName("播放远端参考并录音")
        self.acoustic_button.setToolTip("通过扬声器播放参考，同时采集麦克风")
        self.aec_button = self.button("执行 NLMS 消除", self.apply_aec, True)
        actions = QHBoxLayout()
        actions.addWidget(self.acoustic_button)
        actions.addWidget(self.aec_button, 1)
        acoustic_layout.addLayout(actions)
        modes.addWidget(acoustic)
        self.echo_mode.currentChanged.connect(modes.setCurrentIndex)
        layout.addWidget(modes, 1)
        return page

    def update_calibration_hint(self):
        self.calibration_hint.setText(f"使用扬声器，前 {self.calibration.value():g} 秒保持安静。")

    def build_speaker(self):
        page, layout = self.mode_page()
        form = self.scroll_form(layout)
        self.member_title = self.section_label("")
        roster = QHBoxLayout()
        roster.addWidget(self.member_title)
        roster.addStretch()
        self.delete_member_button = self.button("删除所选成员", self.delete_member)
        roster.addWidget(self.delete_member_button)
        form.addLayout(roster)
        self.speaker_next = self.hint("")
        form.addWidget(self.speaker_next)
        self.member_list = QListWidget()
        self.member_list.setAccessibleName("已注册成员与样本段数")
        self.member_list.setFixedHeight(102)
        self.member_list.itemSelectionChanged.connect(self.sync_controls)
        form.addWidget(self.member_list)

        register = QHBoxLayout()
        self.member_name = QLineEdit()
        self.member_name.setPlaceholderText("成员姓名")
        self.member_name.setMaxLength(30)
        self.member_name.setAccessibleName("成员姓名")
        self.member_name.textChanged.connect(self.sync_controls)
        register.addWidget(self.member_name, 1)
        self.enroll_button = self.button("注册原音", self.enroll_member)
        register.addWidget(self.enroll_button)
        layout.addLayout(register)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 1, 0, 0)
        self.reject_unknown = QCheckBox("实验性未知人拒识")
        self.reject_unknown.setToolTip("阈值需要用本组独立录音验证")
        details_layout.addWidget(self.reject_unknown)
        self.speaker_scores = QPlainTextEdit()
        self.speaker_scores.setReadOnly(True)
        self.speaker_scores.setFixedHeight(92)
        self.speaker_scores.setPlaceholderText("识别后显示各成员的平均对数似然")
        details_layout.addWidget(self.speaker_scores)
        form.addWidget(self.disclosure("似然分数与拒识选项", details), 0, Qt.AlignmentFlag.AlignLeft)
        form.addWidget(details)
        self.speaker_result = QLabel()
        self.speaker_result.setObjectName("speakerResult")
        self.speaker_result.setTextFormat(Qt.TextFormat.PlainText)
        self.speaker_result.setWordWrap(True)
        self.speaker_result.hide()
        layout.addWidget(self.speaker_result)
        self.identify_button = self.button("识别原音中的说话人", self.identify, True)
        layout.addWidget(self.identify_button)
        return page

    def build_effects(self):
        page, layout = self.mode_page()
        form = self.scroll_form(layout)
        self.effect_names = ("变速 / 变调", "机器人音", "电话音", "音量调整")
        self.effect_choices = QButtonGroup(self)
        choices = QGridLayout()
        choices.setSpacing(6)
        for index, name in enumerate(self.effect_names):
            button = QPushButton(name)
            button.setObjectName("effectChoice")
            button.setCheckable(True)
            self.effect_choices.addButton(button, index)
            choices.addWidget(button, index // 2, index % 2)
        form.addLayout(choices)
        self.effect_amount = QDoubleSpinBox()
        self.effect_amount.setMaximumWidth(132)
        self.effect_amount_label = QLabel()
        params = QFormLayout()
        params.addRow(self.effect_amount_label, self.effect_amount)
        form.addLayout(params)
        self.effect_description = self.hint("")
        form.addWidget(self.effect_description)
        self.effect_choices.idClicked.connect(lambda index: self.effect_changed(self.effect_names[index]))
        self.effect_choices.button(0).setChecked(True)
        self.effect_changed(self.effect_names[0])
        self.effect_button = self.button("应用音效", self.apply_effect, True)
        layout.addWidget(self.effect_button)
        return page

    def effect_changed(self, name):
        self.effect_amount.setVisible(name != "电话音")
        self.effect_amount_label.setVisible(name != "电话音")
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
            self.effect_description.setText("调整处理结果的音量。试听音量可在音频设备中单独调节。")
        else:
            self.effect_description.setText("保留 300～3400 Hz，模拟传统电话的窄带听感。")

    def log(self, text, *, error=False):
        self.log_view.appendPlainText(time.strftime("%H:%M:%S") + "  " + text)
        logging.info(text)
        self.operation.setText(text.splitlines()[0])
        self.operation.setToolTip(text)
        self.operation.setProperty("error", error)
        self.operation.style().unpolish(self.operation)
        self.operation.style().polish(self.operation)

    def error(self, text):
        self.log("操作失败 · " + str(text), error=True)

    def refresh_devices(self):
        old_in, old_out = self.input_device.currentData(), self.output_device.currentData()
        self.input_device.clear()
        self.output_device.clear()
        self.input_device.addItem("系统默认输入",None)
        self.output_device.addItem("系统默认输出",None)
        try:
            devices = self.engine.devices()
            host_apis = self.engine.host_apis()
            for i,d in enumerate(devices):
                label = f"{i} · {d['name']} ({host_apis[d['hostapi']]})"
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
        self.update_device_summary()

    def update_device_summary(self):
        if self._device_error:
            summary = "无法读取设备列表，请刷新或检查系统的麦克风权限。"
        else:
            summary = f"麦克风 · {self.input_device.currentText()}\n播放 · {self.output_device.currentText()}"
        self.device_info.setText(summary)
        self.device_button.setToolTip(summary)

    def sync_controls(self):
        busy = self.task is not None
        recording = self.engine.stream is not None
        idle = not busy and not recording
        has = self.original is not None
        has_result = self.processed is not None
        has_input = has_result if self.source_combo.currentIndex() == 1 else has
        member_count = self.member_list.count()
        self.record_controls.setCurrentWidget(self.stop_record_button if recording else self.record_button)
        self.record_button.setEnabled(idle)
        self.stop_record_button.setEnabled(recording)
        self.import_button.setEnabled(idle)
        self.device_button.setEnabled(idle)
        self.input_device.setEnabled(idle)
        self.output_device.setEnabled(idle)
        self.refresh_button.setEnabled(idle)
        self.source_combo.setEnabled(idle)
        self.source_combo.model().item(1).setEnabled(has_result)
        self.source_row.setVisible(self.tabs.currentIndex() != 2)
        self.mode_title.setText(self.mode_tabs.tabText(self.tabs.currentIndex()))
        self.volume.setEnabled(idle)
        self.level_meter.setVisible(recording)
        self.audition_tabs.setEnabled(idle)
        self.audition_tabs.setTabEnabled(0, has)
        self.audition_tabs.setTabEnabled(1, has_result)
        self.fit_db_button.setEnabled(has or has_result)
        self.update_player()
        self.export_button.setEnabled(idle and (has or has_result))
        self.export_original_action.setEnabled(idle and has)
        self.export_result_action.setEnabled(idle and has_result)
        self.enhance_button.setEnabled(idle and has_input and (self.noise_mode.currentIndex() == 0 or self.noise is not None))
        self.echo_button.setEnabled(idle and has_input)
        self.effect_button.setEnabled(idle and has_input)
        self.enroll_button.setEnabled(idle and has and bool(self.member_name.text().strip()))
        self.undo_echo_button.setEnabled(idle and self.echo_metadata is not None)
        self.aec_button.setEnabled(idle and has_input and self.reference is not None)
        self.acoustic_button.setEnabled(idle and (self.reference_source is not None or self.reference is not None))
        self.reference_button.setEnabled(idle)
        self.noise_button.setEnabled(idle)
        self.identify_button.setEnabled(idle and has and member_count >= 2)
        self.delete_member_button.setEnabled(idle and self.member_list.currentItem() is not None)

    def set_original(self, audio, title="原始语音", *, synchronized_reference=None):
        self.stop_playback(announce=False)
        self._preview_finished = False
        if synchronized_reference is not None:
            self.reference = synchronized_reference.copy()
            self.reference_is_synchronized = True
            self.reference_label.setText("已配对本次同步录音和播放参考，可以执行消除。")
        elif self.reference_is_synchronized:
            self.reference = None
            self.reference_is_synchronized = False
            self.reference_label.setText("原音已更换。请重新同步录制，或导入与新录音对应的参考。")
        self.original = audio
        self.original_title = title
        self.processed = None
        self.echo_metadata = None
        self.source_combo.setCurrentIndex(0)
        self.timeline.set_original(audio, title)
        self.speaker_result.hide()
        self.speaker_scores.clear()
        self.audition_tabs.setCurrentIndex(0)
        self.update_clip_label()
        stats = audio_stats(audio)
        self.log(f"已载入 {title}，{len(audio)/SR:.2f} 秒")
        if rms(audio)<0.002:
            self.log("输入音量很低，请检查麦克风或靠近一些说话。")
        if stats['peak']>=0.999:
            self.log("输入峰值达到满幅，可能发生削波；建议降低麦克风增益。")
        self.sync_controls()

    def update_clip_label(self):
        if self.original is None:
            self.state_label.setText("尚未载入音频")
            self.state_label.setToolTip("")
            return
        stats = audio_stats(self.original)
        self.state_label.setText(f"{stats['seconds']:.2f} 秒  ·  RMS {stats['rms_db']:.1f} dBFS")
        self.state_label.setToolTip(f"{self.original_title}\n{self.state_label.text()}\n峰值 {stats['peak']:.3f}")

    def set_result(self, audio, title, metadata=None):
        self.processed = audio
        self.echo_metadata = metadata
        self.timeline.set_result(audio, title)
        self.log(f"{title}完成，{len(audio)/SR:.2f} 秒；可试听或导出 WAV。")
        self.sync_controls()
        self.audition_tabs.setCurrentIndex(1)

    def selected_audio(self):
        audio = self.processed if self.source_combo.currentIndex()==1 else self.original
        if audio is None:
            raise ValueError("所选输入为空，请先录音、导入或产生处理结果")
        return audio.copy()

    def run_task(self,label,function,done):
        if self.task or self.engine.stream:
            return
        self.stop_playback(announce=False)
        self.log(label+"…")
        self.progress.setRange(0,0)
        self.progress.show()
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
        self.progress.hide()
        self.refresh_members()
        self.sync_controls()
        if self._closing:
            self.close()

    def start_recording(self):
        self.stop_playback(announce=False)
        try:
            self.engine.start_recording(self.input_device.currentData())
            if self.tabs.currentIndex() == 0 and self.noise_mode.currentIndex() == 0:
                self.log(f"正在录音。前 {self.noise_duration.value():g} 秒保持安静，然后说话。")
            else:
                self.log("正在录音。录完请点击结束录音。")
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
                self.set_original(audio, "声学实验麦克风录音", synchronized_reference=self.engine.reference)
            else:
                self.set_original(audio, "麦克风实录")
            if self.engine.warning:
                self.log("音频设备提示："+self.engine.warning+"；可能存在丢帧，请检查设备或重录。")
        except Exception as exc:
            self.error(str(exc))
        finally:
            self.level_meter.setValue(0)
            self.update_clip_label()
            self.sync_controls()

    def start_acoustic(self):
        playback = self.reference_source if self.reference_source is not None else self.reference
        if playback is None:
            return self.error("请先导入远端参考。")
        self.stop_playback(announce=False)
        try:
            self.engine.start_acoustic(playback,self.input_device.currentData(),self.output_device.currentData(),self.volume.value()/100)
            self.log(f"同步播放录音中。前 {self.calibration.value():g} 秒保持安静，播放完成后自动结束。")
            self.sync_controls()
        except Exception as exc:
            self.error("声学实验无法开始："+str(exc))

    def tick(self):
        if self.engine.stream:
            elapsed = self.engine.frames/self.engine.rate
            level = self.engine.level
            dbfs = 20 * np.log10(max(level, 1e-5))
            self.level_meter.setValue(int(np.clip((dbfs + 60) / 60 * 100, 0, 100)))
            self.level_meter.setToolTip(f"输入电平 {dbfs:.1f} dBFS")
            self.state_label.setText(f"正在录音 · {elapsed:.1f} 秒")
            if self.engine.done:
                self.stop_recording()
        elif self._playing and not self.timeline.scrubbing:
            if self.engine.is_playing():
                self.sync_playback_position()
            else:
                self.timeline.set_position(self._preview_length / SR, follow=self._follow_preview)
                self.stop_playback(announce=False, remember_position=False)
                self._preview_finished = True
                self.log("试听结束。")

    def import_audio(self):
        path,_ = QFileDialog.getOpenFileName(self,"导入语音",str(self.root),"音频 (*.wav *.flac *.ogg *.aiff *.aif);;所有文件 (*)")
        if path:
            self.run_task("读取音频",lambda _:read_audio(path),lambda x:self.set_original(x,Path(path).name))

    def audition_changed(self):
        self.stop_playback(announce=False)
        self.update_player()

    def update_player(self):
        result = self.audition_tabs.currentIndex() == 1
        audio = self.processed if result else self.original
        idle = self.task is None and self.engine.stream is None
        self.play_button.setEnabled(idle and audio is not None)
        self.play_button.setText("停止" if self._playing else "播放")
        self.audition_position.setEnabled(idle and self.timeline.duration > 0)
        hint = "输入秒数或 mm:ss.sss，按 Enter 定位。"
        if audio is not None:
            hint += f"\n当前试听音轨时长 {len(audio) / SR:.3f} 秒。"
        self.audition_position.setToolTip(hint)

    def display_position(self, seconds):
        if not self.audition_position.isModified():
            minutes, milliseconds = divmod(round(seconds * 1000), 60000)
            seconds, milliseconds = divmod(milliseconds, 1000)
            self.audition_position.setText(f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}")

    def commit_position(self):
        if not self.audition_position.isModified():
            return
        text = self.audition_position.text().strip()
        self.audition_position.setModified(False)
        try:
            minutes, separator, seconds = text.partition(":")
            value = int(minutes) * 60 + float(seconds) if separator else float(minutes)
            if not math.isfinite(value):
                raise ValueError
        except ValueError:
            self.log("位置格式用秒数或 mm:ss.sss，例如 1.25 或 00:01.250。")
            self.display_position(self.timeline.position)
            return
        self.seek_audition(value)

    def seek_audition(self, seconds):
        was_playing = self._playing
        self.stop_playback(announce=False, remember_position=False)
        self._preview_finished = False
        self.timeline.set_position(seconds, follow=True)
        self.display_position(self.timeline.position)
        if was_playing:
            self.play(self.audition_tabs.currentIndex() == 1, restart_at_end=False)

    def sync_playback_position(self):
        elapsed = max(0, time.monotonic() - self._preview_started)
        position = min(self._preview_offset / SR + elapsed, self._preview_length / SR)
        self.timeline.set_position(position, follow=self._follow_preview)

    def pause_preview_following(self):
        self._follow_preview = False

    def toggle_playback(self):
        if self._playing:
            self.stop_playback()
        elif self.play_button.isEnabled():
            self.play(self.audition_tabs.currentIndex() == 1)

    def play(self, result, *, restart_at_end=True):
        try:
            x = self.processed if result else self.original
            if x is None:
                return
            offset = round(self.timeline.position * SR)
            if self._preview_finished or offset >= len(x):
                if not restart_at_end:
                    return
                offset = 0
                self.timeline.set_position(0, follow=True)
            self.engine.play(x, self.output_device.currentData(), self.volume.value() / 100, start_sample=offset)
            self._preview_offset = offset
            self._preview_length = len(x)
            self._preview_started = time.monotonic()
            self._playing = True
            self._preview_finished = False
            self._follow_preview = True
            self.timeline.set_position(offset / SR, follow=True)
            self.timer.setInterval(33)
            self.update_player()
            self.log("正在播放" + ("处理结果。" if result else "原始录音。"))
        except Exception as exc:
            self._playing = False
            self.update_player()
            self.error("无法播放："+str(exc))

    def stop_playback(self, *, announce=True, remember_position=True):
        if not self._playing:
            return
        if remember_position:
            self.sync_playback_position()
        self.engine.stop_playback()
        self._playing = False
        self.timer.setInterval(100)
        self.update_player()
        if announce:
            self.log("已停止试听。")

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

    def toggle_spectrum(self, checked):
        index = int(checked)
        if self.display_tabs.currentIndex() != index:
            self.display_tabs.setCurrentIndex(index)
        else:
            self.timeline.set_spectrum(checked)
            self.monitor_meta.setText("前 8 秒 · 共用相对 dB" if checked else "16 kHz · 双轨同步")
            self.x_axis_label.setText("频率" if checked else "时间")
            self.y_axis_label.setVisible(not checked)
            self.fit_db_button.setVisible(checked)
            self.update_timeline_tools(self.timeline.width())

    def import_noise(self):
        path,_ = QFileDialog.getOpenFileName(self,"导入纯环境噪声",str(self.root),"音频 (*.wav *.flac *.ogg)")
        if path:
            def done(x):
                self.noise = x
                self.noise_name = Path(path).name
                self.noise_mode.setCurrentIndex(1)
                self.update_noise_label()
                self.log("已载入独立噪声样本。")
            self.run_task("载入噪声样本",lambda _:read_audio(path),done)

    def update_noise_label(self):
        if self.noise_mode.currentIndex() == 0:
            self.noise_label.setText(f"录音开始时，前 {self.noise_duration.value():g} 秒保持安静。")
        elif self.noise is None:
            self.noise_label.setText("请导入同一环境录制的纯噪声。")
        else:
            self.noise_label.setText(f"{self.noise_name} · {len(self.noise)/SR:.2f} 秒")

    def apply_enhance(self):
        try:
            x = self.selected_audio()
            strength,duration = self.noise_strength.value(),self.noise_duration.value()
            noise = None
            if self.noise_mode.currentIndex() == 1:
                if self.noise is None:
                    raise ValueError("请先导入独立噪声样本。")
                noise = self.noise.copy()
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
                self.reference_is_synchronized = False
                self.reference_label.setText(f"{Path(path).name} · {len(x)/SR:.2f} 秒。可同步录制，或用于对应录音。")
                self.log("已载入远端参考音频。")
            self.run_task("读取远端参考",lambda _:read_audio(path),done)

    def apply_aec(self):
        try:
            if self.reference is None:
                raise ValueError("请先同步录制，或导入对应的播放参考。")
            x,reference = self.selected_audio(),self.reference.copy()
            delay = None if self.auto_delay.isChecked() else self.aec_delay.value()
            taps,cal = self.aec_taps.currentData(),self.calibration.value()
            def done(result):
                out,stats = result
                self.set_result(out,"NLMS 回声消除")
                self.log(f"校准验证衰减 {stats['calibration_reduction_db']:.2f} dB，参考延时 {stats['delay_ms']:.2f} ms。"
                         f"\n校准后能量下降 {stats['energy_reduction_db']:.2f} dB，包含近端讲话时仅供对照。")
            self.run_task("自适应回声消除",lambda p:cancel_echo(x,reference,delay,taps,0.35,cal,p),done)
        except Exception as exc:
            self.error(str(exc))

    def refresh_members(self):
        selected = self.member_list.currentItem()
        active_name = selected.data(Qt.UserRole) if selected else None
        self.member_list.clear()
        names = self.store.members()
        for name in names:
            entries = [e for e in self.store.entries if e["name"]==name]
            self.member_list.addItem(f"{name}  ·  {len(entries)} 段  ·  {sum(e['seconds'] for e in entries):.1f} 秒")
            item = self.member_list.item(self.member_list.count() - 1)
            item.setData(Qt.UserRole, name)
            if name == active_name:
                self.member_list.setCurrentItem(item)
        self.member_title.setText(f"已注册成员 {len(names)} 人")
        self.member_list.setVisible(bool(names))
        self.delete_member_button.setVisible(bool(names))
        prompt = ("录一段语音，填写成员姓名，再点击注册。建议每人录 3 段不同内容。"
                  if len(names) < 2 else "重新录一段未用于注册的语音，再点击识别。")
        self.speaker_next.setText(prompt)

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
            self.speaker_result.show()
            self.log(f"已为 {entry['name']} 追加 {entry['seconds']:.1f} 秒声纹样本。")
        self.run_task("提取声纹并训练模型",register,done)

    def identify(self):
        if len(self.store.members()) < 2:
            return self.error("请先注册至少两名成员。")
        audio = self.original.copy()
        reject = self.reject_unknown.isChecked()
        def done(result):
            self.speaker_result.setText("识别结果："+result["name"])
            self.speaker_result.show()
            scores = [f"{name}：{score:.3f}" for name,score in result["scores"]]
            if result["margin"] is not None:
                scores.append(f"前两名得分差：{result['margin']:.3f}")
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
            name, amount = self.effect_names[self.effect_choices.checkedId()], self.effect_amount.value()
            self.run_task("应用"+name,lambda _:effect(x,name,amount),lambda out:self.set_result(out,name))
        except Exception as exc:
            self.error(str(exc))

    def show_help(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("使用指南")
        dialog.setText("数字音效实验室 · 快速开始")
        dialog.setInformativeText(
            "1. 在音频设备中选择麦克风和扬声器，再录音或导入已有音频。\n"
            "2. 用录音开头降噪时，先按页面标出的时长保持安静。也可以导入独立噪声样本。\n"
            "3. 回声音效可以直接还原。做声学回声消除时，请导入参考并在校准期间保持安静。\n"
            "4. 每位成员建议注册至少 3 段不同的话。注册两名成员后，可用新的录音测试识别。\n"
            "5. 用波形下方的原音和结果切换试听。文件可从保存与导出菜单写为 WAV。\n\n"
            "在图内滚轮缩放横轴，在右侧标尺上滚轮缩放纵轴。可以拖动底部和右侧的选框或手柄。点“联动”可让两轴一起缩放。\n"
            "频谱默认适配两条曲线的共同电平范围。点下方“自动”可重新适配当前频段的纵轴。\n"
            "点击或拖动时间刻度定位，输入框也可输入时间。右上角可以切换上下或叠加。\n\n"
            "音频在录完后处理，单段最长 300 秒。原音和结果可另存为 16 kHz / 16 bit WAV。\n"
            "声纹特征保存在软件目录的 data/speakers。")
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
    configure_appearance(app)
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
