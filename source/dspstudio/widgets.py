"""Qt-only waveform / spectrum drawing; avoids extra plotting runtime."""
import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import QWidget
from .dsp import SR


class SignalPlot(QWidget):
    def __init__(self, title, color="#5eead4", parent=None):
        super().__init__(parent)
        self.title, self.color = title, QColor(color)
        self.audio = None
        self.spectrum = False
        self.setMinimumHeight(150)
        self.setMinimumWidth(240)

    def set_audio(self, audio, title=None):
        self.audio = audio
        if title:
            self.title = title
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#101b2c"))
        p.setPen(QColor("#dce6f3"))
        p.drawText(16, 24, self.title)
        rect = QRectF(48, 38, max(1,self.width()-68), max(1,self.height()-65))
        p.setPen(QPen(QColor("#23334b"),1))
        for frac in (0,0.25,0.5,0.75,1):
            y = rect.top()+frac*rect.height()
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        p.setPen(QColor("#8e9fb5"))
        if self.audio is None or len(self.audio) == 0:
            p.drawText(rect, Qt.AlignCenter, "录制或导入语音后显示")
            return
        x = self.audio
        p.setPen(QPen(self.color,1.2))
        if self.spectrum:
            size = min(len(x), SR*8)
            # Average periodograms rather than draw one arbitrary frame.
            nfft = 1024
            padded = np.pad(x[:size], (0,max(0,nfft-size)))
            frames = np.lib.stride_tricks.sliding_window_view(padded,nfft)[::512]
            mag = np.mean(np.abs(np.fft.rfft(frames*np.hanning(nfft), axis=1))**2, axis=0)
            db = 10*np.log10(mag + 1e-12)
            db = np.maximum(db-db.max(), -80)
            path = QPainterPath()
            for i,v in enumerate(db):
                point = QPointF(rect.left()+i/(len(db)-1)*rect.width(), rect.top()-v/80*rect.height())
                path.moveTo(point) if i==0 else path.lineTo(point)
            p.drawPath(path)
            left, right, top, mid, bottom = "0 Hz", "8000 Hz", "0", "−40", "−80 dB"
        else:
            columns = min(int(rect.width()), len(x))
            boundaries = np.linspace(0,len(x),columns+1).astype(int)
            amplitude = max(1.0,float(np.max(np.abs(x))))
            for i in range(columns):
                block = x[boundaries[i]:boundaries[i+1]]
                hi, lo = float(block.max()), float(block.min())
                px = rect.left()+(i+0.5)*rect.width()/columns
                p.drawLine(QPointF(px,rect.center().y()-hi/amplitude*rect.height()/2),
                           QPointF(px,rect.center().y()-lo/amplitude*rect.height()/2))
            left, right, top, mid, bottom = "0 s", f"{len(x)/SR:.2f} s", f"{amplitude:.1f}", "0", f"−{amplitude:.1f}"
        p.setPen(QColor("#8e9fb5"))
        p.drawText(int(rect.left()),self.height()-10,left)
        p.drawText(int(rect.right()-64),self.height()-10,right)
        p.drawText(4,int(rect.top()+5),top)
        p.drawText(4,int(rect.center().y()+5),mid)
        p.drawText(4,int(rect.bottom()),bottom)
