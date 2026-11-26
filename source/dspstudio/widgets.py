"""Linked waveform and spectrum views with a shared timeline and playhead."""
from dataclasses import dataclass
import math

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from .dsp import SR


def clamp(value, low, high):
    return min(max(value, low), high)


@dataclass(frozen=True)
class AxisRange:
    start: float
    end: float

    @property
    def span(self):
        return self.end - self.start

    def at(self, fraction):
        return self.start + fraction * self.span

    def fraction(self, value):
        return (value - self.start) / self.span

    def shifted(self, delta):
        return AxisRange(self.start + delta, self.end + delta)

    def zoomed(self, factor, anchor=None):
        anchor = self.at(0.5) if anchor is None else anchor
        return AxisRange(anchor + (self.start - anchor) / factor, anchor + (self.end - anchor) / factor)

    def bounded(self, limits, minimum):
        width = clamp(self.span, min(minimum, limits.span), limits.span)
        start = clamp(self.start, limits.start, limits.end - width)
        return AxisRange(start, start + width)


class Trace:
    def __init__(self, name, color, empty_label):
        self.name = name
        self.color = QColor(color)
        self.empty_label = empty_label
        self.set_audio(None)

    def set_audio(self, audio, label=""):
        self.audio = audio
        self.label = label
        self.peak = float(np.max(np.abs(audio), initial=0)) if audio is not None else 0
        self._spectrum = None

    def spectrum_db(self):
        if self._spectrum is None:
            nfft = 1024
            audio = self.audio[:8 * SR]
            padded = np.pad(audio, (0, max(0, nfft - len(audio))))
            frames = np.lib.stride_tricks.sliding_window_view(padded, nfft)[::nfft // 2]
            power = np.mean(np.abs(np.fft.rfft(frames * np.hanning(nfft), axis=1)) ** 2, axis=0)
            self._spectrum = 10 * np.log10(power + 1e-12)
        return self._spectrum


@dataclass
class PlotGeometry:
    ruler: QRectF
    lanes: tuple
    overview: QRectF


@dataclass
class PeakColumns:
    x: np.ndarray
    top: np.ndarray
    bottom: np.ndarray


@dataclass
class Drag:
    kind: str
    origin: QPointF
    x: AxisRange
    y: AxisRange
    axis: str = "x"
    rail: QRectF | None = None
    bounds: AxisRange | None = None
    moved: bool = False


class SignalTimeline(QWidget):
    seekRequested = Signal(float)
    positionChanged = Signal(float)
    viewChanged = Signal()
    playToggleRequested = Signal()
    resized = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.original = Trace("原音", "#56d2e6", "录音或导入音频")
        self.result = Trace("处理结果", "#eec65b", "处理完成后显示结果")
        self.duration = 0.0
        self.spectrum = False
        self.overlay = False
        self.zoom_linked = False
        self.position = 0.0
        self.spectrum_reference = 0.0
        self._time_fit = AxisRange(0, 1)
        self._amplitude_fit = AxisRange(-1, 1)
        self._frequency_fit = AxisRange(0, SR / 2)
        self._db_fit = AxisRange(-80, 0)
        self._wave_view = (self._time_fit, self._amplitude_fit)
        self._spectrum_view = (self._frequency_fit, self._db_fit)
        self._spectrum_dirty = False
        self._spectrum_y_auto = True
        self._scene = None
        self._overview_cache = None
        self._blend_cache = None
        self._drag = None
        self.setMinimumSize(252, 132)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("音频对比时间轴")
        self.setAccessibleDescription("图内滚轮缩放横轴，右侧标尺滚轮缩放纵轴。拖动两侧选框浏览，点击时间刻度定位。")

    @property
    def view(self):
        return self._spectrum_view if self.spectrum else self._wave_view

    @property
    def fit(self):
        return (self._frequency_fit, self._db_fit) if self.spectrum else (self._time_fit, self._amplitude_fit)

    @property
    def scrubbing(self):
        return self._drag is not None and self._drag.kind == "seek"

    def set_original(self, audio, label):
        self.original.set_audio(audio, label)
        self.result.set_audio(None)
        self._update_data(reset=True)
        self.set_position(0)

    def set_result(self, audio, label):
        self.result.set_audio(audio, label)
        self._update_data()

    def _update_data(self, reset=False):
        self._overview_cache = None
        traces = [trace for trace in (self.original, self.result) if trace.audio is not None]
        self.duration = max((len(trace.audio) for trace in traces), default=0) / SR
        peak = max((trace.peak for trace in traces), default=0)
        amplitude = max(0.1, math.ceil(peak * 10) / 10)
        x, y = self._wave_view
        old_time, old_amplitude = self._time_fit, self._amplitude_fit
        self._time_fit = AxisRange(0, self.duration or 1)
        self._amplitude_fit = AxisRange(-amplitude, amplitude)
        self._wave_view = (
            self._time_fit if reset or x == old_time else x.bounded(self._time_fit, 4 / SR),
            self._amplitude_fit if reset or y == old_amplitude else y,
        )
        if reset:
            self._spectrum_view = (self._frequency_fit, self._db_fit)
            self._spectrum_y_auto = True
        self._spectrum_dirty = True
        if self.spectrum:
            self._refresh_spectrum()
        self.set_position(self.position)
        self.redraw()

    def _refresh_spectrum(self):
        if not self._spectrum_dirty:
            return
        self.spectrum_reference = max(
            (float(trace.spectrum_db().max()) for trace in (self.original, self.result) if trace.audio is not None),
            default=0,
        )
        self._db_fit = self._spectrum_fit(self._frequency_fit)
        if self._spectrum_y_auto:
            x, _ = self._spectrum_view
            self._spectrum_view = (x, self._db_fit if x == self._frequency_fit else self._spectrum_fit(x))
        self._spectrum_dirty = False

    def _spectrum_fit(self, x):
        bounds = []
        for trace in (self.original, self.result):
            if trace.audio is None:
                continue
            data = trace.spectrum_db()
            bin_width = self._frequency_fit.end / (len(data) - 1)
            start, end = x.start / bin_width, x.end / bin_width
            bins = data[math.ceil(start):math.floor(end) + 1]
            edges = np.interp((start, end), np.arange(len(data)), data)
            bounds.extend((np.min(bins, initial=edges.min()), np.max(bins, initial=edges.max())))
        if not bounds:
            return AxisRange(-80, 0)

        low = min(bounds) - self.spectrum_reference
        high = max(bounds) - self.spectrum_reference
        center = (low + high) / 2
        radius = max(5, (high - low) / 2 + max(2, 0.08 * (high - low)))
        return AxisRange(5 * math.floor((center - radius) / 5), 5 * math.ceil((center + radius) / 5))

    def fit_spectrum_y(self):
        if self.spectrum:
            self.reset_view(axis="y")

    def set_spectrum(self, enabled):
        self.spectrum = enabled
        if enabled:
            self._refresh_spectrum()
        self.redraw()

    def set_overlay(self, enabled):
        self.overlay = enabled
        self.redraw()

    def set_zoom_linked(self, enabled):
        self.zoom_linked = enabled

    def redraw(self):
        self._scene = None
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.redraw()
        self.resized.emit(event.size().width())

    def set_view(self, x, y):
        previous = self.view
        if self.spectrum:
            full_y = self._db_fit
            y_limits = AxisRange(min(-280, full_y.start - 20), max(160, full_y.end + 20))
            self._spectrum_view = (x.bounded(self._frequency_fit, 2 * SR / 1024),
                                   y.bounded(y_limits, 1))
            if y != previous[1] and self._spectrum_view[1] != previous[1]:
                self._spectrum_y_auto = False
        else:
            fit_y = self._amplitude_fit
            margin = max(4.0, 3 * fit_y.span)
            y_limits = AxisRange(fit_y.start - margin, fit_y.end + margin)
            self._wave_view = (x.bounded(self._time_fit, 4 / SR), y.bounded(y_limits, 0.0001))
        if self.view != previous:
            self.redraw()
            self.viewChanged.emit()

    def reset_view(self, axis=None):
        x, y = self.view
        fit_x, fit_y = self.fit
        if self.spectrum and axis == "y":
            fit_y = self._spectrum_fit(x)
        self.set_view(fit_x if axis != "y" else x, fit_y if axis != "x" else y)
        if self.spectrum and axis != "x":
            self._spectrum_y_auto = True

    def zoom(self, axis, factor, anchor=None, linked_anchor=None):
        if self.duration == 0:
            return
        x, y = self.view
        current = y if axis == "y" else x
        self._resize_axis(axis, current.zoomed(factor, anchor), x, y, linked_anchor)

    def _resize_axis(self, axis, resized, x, y, linked_anchor=None):
        if axis == "y":
            if self.zoom_linked:
                x = x.zoomed(y.span / resized.span, linked_anchor)
            y = resized
        else:
            if self.zoom_linked:
                y = y.zoomed(x.span / resized.span, linked_anchor)
            x = resized
        self.set_view(x, y)

    def set_position(self, seconds, *, follow=False):
        position = clamp(seconds, 0, self.duration)
        if follow:
            x, y = self._wave_view
            if position < x.start or position > x.end:
                x = x.shifted(position - x.at(0.15)).bounded(self._time_fit, 4 / SR)
                self._wave_view = (x, y)
                if not self.spectrum:
                    self.redraw()
        if self.position != position:
            self.position = position
            self.positionChanged.emit(position)
            self.update()

    def _geometry(self):
        left, right = 11, 85
        width = self.width() - left - right
        ruler = QRectF(left, 2, width, 25)
        overview = QRectF(left, self.height() - 23, width, 18) if not self.spectrum else QRectF()
        bottom = self.height() - (30 if not self.spectrum else 4)
        top = ruler.bottom() + 1
        if self.overlay:
            lanes = (QRectF(left, top, width, bottom - top),)
        else:
            lane_height = (bottom - top - 2) / 2
            lanes = (QRectF(left, top, width, lane_height),
                     QRectF(left, top + lane_height + 2, width, lane_height))
        return PlotGeometry(ruler, lanes, overview)

    @staticmethod
    def _track(lane):
        return lane.adjusted(0, 23, 0, -4)

    def _vertical_rail(self, track):
        return QRectF(self.width() - 16, track.top(), 10, track.height())

    def _vertical_domain(self, y):
        fit_y = self.fit[1]
        return AxisRange(min(fit_y.start, y.start), max(fit_y.end, y.end))

    def _vertical_selection(self, rail, domain, y):
        top = self._map_y(rail, domain, y.end)
        bottom = self._map_y(rail, domain, y.start)
        return QRectF(rail.left(), top, rail.width(), bottom - top)

    @staticmethod
    def _map_x(rect, limits, value):
        return rect.left() + rect.width() * limits.fraction(value)

    @staticmethod
    def _map_y(rect, limits, value):
        return rect.bottom() - rect.height() * limits.fraction(value)

    @staticmethod
    def _ticks(limits, count):
        interval = limits.span / max(1, count)
        magnitude = 10 ** math.floor(math.log10(interval))
        step = next(size * magnitude for size in (1, 2, 5, 10) if size * magnitude >= interval * (1 - 1e-9))
        first = math.ceil(limits.start / step - 1e-8)
        last = math.floor(limits.end / step + 1e-8)
        return (value * step for value in range(first, last + 1)), step

    @staticmethod
    def _number(value, step):
        if value == 0:
            return "0"
        decimals = min(7, max(0, -math.floor(math.log10(step * (1 + 1e-9)))))
        return f"{value:.{decimals}f}".replace("-", "−")

    def _x_label(self, value, step):
        if not self.spectrum:
            return self._number(value, step) + " s"
        if step >= 1000:
            return self._number(value / 1000, step / 1000) + " kHz"
        return self._number(value, step) + " Hz"

    def _draw_ruler(self, painter, ruler, x, tick_values, step):
        painter.setPen(QColor("#c6cad4"))
        metrics = QFontMetricsF(painter.font())
        for value in tick_values:
            at = self._map_x(ruler, x, value)
            text = self._x_label(value, step)
            width = metrics.horizontalAdvance(text) + 2
            label_left = clamp(at - width / 2, ruler.left(), ruler.right() - width)
            painter.drawText(QRectF(label_left, ruler.top(), width, 19), Qt.AlignmentFlag.AlignCenter, text)
            painter.drawLine(QPointF(at, ruler.bottom() - 4), QPointF(at, ruler.bottom()))
        painter.setPen(QColor("#484c57"))
        painter.drawLine(ruler.bottomLeft(), ruler.bottomRight())
        painter.setPen(QColor("#c6cad4"))
        painter.drawText(QRectF(ruler.right() + 8, 2, 61, 19), Qt.AlignmentFlag.AlignVCenter,
                         "dB" if self.spectrum else "幅度")

    def _draw_legend(self, painter, lane, traces):
        font = QFont(painter.font())
        font.setPointSize(12)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        start = lane.left() + 3
        right = lane.right() - 5
        for trace in traces:
            width = metrics.horizontalAdvance(trace.name) + 23
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(trace.color if trace.audio is not None else QColor("#787d89"))
            painter.drawRoundedRect(QRectF(start, lane.top() + 8, 10, 5), 2, 2)
            painter.setPen(trace.color if trace.audio is not None else QColor("#bdc0ca"))
            painter.drawText(QRectF(start + 16, lane.top(), width - 16, 20),
                             Qt.AlignmentFlag.AlignVCenter, trace.name)
            start += width + (15 if self.overlay else 0)
        if len(traces) == 1 and traces[0].label:
            subtitle = metrics.elidedText(traces[0].label, Qt.TextElideMode.ElideRight, int(max(0, right - start)))
            painter.setPen(QColor("#b6bac7"))
            painter.drawText(QRectF(start, lane.top(), max(0, right - start), 20),
                             Qt.AlignmentFlag.AlignVCenter, subtitle)

    def _draw_lane(self, painter, lane, x, y, x_ticks, traces):
        painter.fillRect(QRectF(0, lane.top(), self.width(), lane.height()), QColor("#252730"))
        plot = self._track(lane)
        painter.setPen(QColor("#383b46"))
        for value in x_ticks:
            pixel = self._map_x(plot, x, value)
            painter.drawLine(QPointF(pixel, plot.top()), QPointF(pixel, plot.bottom()))

        count = max(2, round(plot.height() / 40)) if plot.height() >= 40 else 1
        values, step = self._ticks(y, count)
        for value in values:
            pixel = self._map_y(plot, y, value)
            painter.setPen(QColor("#535764" if value == 0 else "#363944"))
            painter.drawLine(QPointF(plot.left(), pixel), QPointF(plot.right(), pixel))
            painter.setPen(QColor("#c6cad4"))
            painter.drawLine(QPointF(plot.right(), pixel), QPointF(plot.right() + 4, pixel))
            painter.drawText(QRectF(plot.right() + 8, pixel - 9, 59, 18),
                             Qt.AlignmentFlag.AlignVCenter, self._number(value, step))
        self._draw_vertical_axis(painter, self._vertical_rail(plot), y)

        painter.save()
        painter.setClipRect(plot)
        if self.overlay:
            self._draw_overlay(painter, plot, x, y, traces)
        elif traces[0].audio is not None:
            self._draw_trace(painter, plot, x, y, traces[0], traces[0].color)
        painter.restore()

        if not any(trace.audio is not None for trace in traces):
            painter.setPen(QColor("#c6cad4"))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, traces[0].empty_label)
        painter.save()
        self._draw_legend(painter, lane, traces)
        painter.restore()

    def _draw_vertical_axis(self, painter, rail, y):
        painter.fillRect(rail, QColor("#303340"))
        if self.duration:
            selection = self._vertical_selection(rail, self._vertical_domain(y), y)
            self._draw_range(painter, selection, vertical=True)

    def _draw_trace(self, painter, plot, x, y, trace, color):
        if self.spectrum:
            painter.strokePath(self._spectrum_path(plot, trace, x, y), QPen(color, 1.25))
        else:
            self._paint_wave(painter, self._wave_shape(plot, trace.audio, x, y), color)

    def _draw_overlay(self, painter, plot, x, y, traces):
        traces = [trace for trace in traces if trace.audio is not None]
        if len(traces) < 2:
            if traces:
                self._draw_trace(painter, plot, x, y, traces[0], traces[0].color)
            return
        scale = self.devicePixelRatioF()
        size = QSize(math.ceil(plot.width() * scale), math.ceil(plot.height() * scale))
        layer = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        layer.setDevicePixelRatio(scale)
        masks = [self._trace_mask(layer, plot, x, y, trace) for trace in traces]
        # Each pixel mixes the two colors in proportion to their coverage. Either trace can be rendered first.
        colors = tuple(trace.color.getRgb()[:3] for trace in traces)
        lookup = self._blend_palette(colors)
        pixels = np.ascontiguousarray(lookup[masks[0], masks[1]])
        image = QImage(pixels.data, size.width(), size.height(), pixels.strides[0],
                       QImage.Format.Format_RGBA8888_Premultiplied)
        image.setDevicePixelRatio(scale)
        painter.drawImage(plot.topLeft(), image)

    def _trace_mask(self, layer, plot, x, y, trace):
        scale = layer.devicePixelRatio()
        width, height = layer.width(), layer.height()
        shape = self._spectrum_path(plot, trace, x, y) if self.spectrum else self._wave_shape(plot, trace.audio, x, y, scale)
        if isinstance(shape, PeakColumns):
            columns = np.floor((shape.x - plot.left()) * scale).astype(np.intp)
            visible = (columns >= 0) & (columns < width)
            starts = np.floor((shape.top[visible] - plot.top()) * scale).astype(np.intp)
            ends = np.floor((shape.bottom[visible] - plot.top()) * scale).astype(np.intp)
            rows = np.arange(height)[:, None]
            mask = np.zeros((height, width), dtype=np.uint8)
            mask[:, columns[visible]] = ((rows >= starts) & (rows <= ends)) * np.uint8(255)
            return mask

        layer.fill(Qt.GlobalColor.transparent)
        if shape is not None:
            painter = QPainter(layer)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(-plot.left(), -plot.top())
            painter.strokePath(shape, QPen(QColor("white"), 1.25))
            painter.end()
        raster = np.frombuffer(layer.constBits(), dtype=np.uint32).reshape(height, width)
        return (raster >> 24).astype(np.uint8)

    def _blend_palette(self, colors):
        if self._blend_cache is None or self._blend_cache[0] != colors:
            coverage = np.arange(256, dtype=np.float32) * (0.84 / 255)
            a, b = coverage[:, None], coverage[None, :]
            total = a + b
            opacity = total - a * b
            weight_a = np.divide(a, total, out=np.zeros_like(total), where=total > 0) * opacity
            weight_b = opacity - weight_a
            palette = np.empty((256, 256, 4), dtype=np.uint8)
            for channel, (first, second) in enumerate(zip(*colors)):
                palette[:, :, channel] = np.rint(first * weight_a + second * weight_b)
            palette[:, :, 3] = np.rint(255 * opacity)
            self._blend_cache = (colors, palette)
        return self._blend_cache[1]

    def _wave_shape(self, plot, audio, x, y, pixel_scale=1):
        start = max(0, math.floor(x.start * SR))
        end = min(len(audio), math.ceil(x.end * SR) + 1)
        if end <= start:
            return None
        step = max(1, math.ceil(x.span * SR / max(1, plot.width() * pixel_scale)))
        if step <= 2:
            path = QPainterPath()
            samples = enumerate(audio[start:end], start)
            points = QPolygonF([QPointF(self._map_x(plot, x, index / SR), self._map_y(plot, y, float(value)))
                                for index, value in samples])
            path.addPolygon(points)
            if len(points) == 1:
                point = points[0]
                path.lineTo(point.x() + 0.6, point.y())
            return path

        samples = audio[start:end]
        indices = np.arange(0, len(samples), step)
        lows = np.minimum.reduceat(samples, indices)
        highs = np.maximum.reduceat(samples, indices)
        axis_positions = start + indices + np.minimum(step - 1, len(samples) - 1 - indices) / 2
        pixels = self._map_x(plot, x, axis_positions / SR)
        top = self._map_y(plot, y, highs)
        bottom = np.maximum(top + 0.65, self._map_y(plot, y, lows))
        visible = (bottom >= plot.top()) & (top <= plot.bottom())
        starts = np.maximum(top[visible], plot.top())
        ends = np.minimum(bottom[visible], plot.bottom())
        return PeakColumns(pixels[visible], starts, ends)

    @staticmethod
    def _paint_wave(painter, wave, color):
        if isinstance(wave, QPainterPath):
            painter.strokePath(wave, QPen(color, 1.25))
        elif isinstance(wave, PeakColumns) and len(wave.x):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(QPen(color, 1.0))
            painter.drawLines([QLineF(x, a, x, b) for x, a, b in zip(wave.x, wave.top, wave.bottom)])
            painter.restore()

    def _spectrum_path(self, plot, trace, x, y):
        data = trace.spectrum_db()
        bin_width = (SR / 2) / (len(data) - 1)
        start = max(0, math.floor(x.start / bin_width))
        end = min(len(data), math.ceil(x.end / bin_width) + 1)
        path = QPainterPath()
        path.addPolygon(QPolygonF([
            QPointF(self._map_x(plot, x, index * bin_width),
                    self._map_y(plot, y, float(value) - self.spectrum_reference))
            for index, value in enumerate(data[start:end], start)
        ]))
        return path

    def _overview_view(self, overview):
        x, _ = self._wave_view
        left = self._map_x(overview, self._time_fit, x.start)
        right = self._map_x(overview, self._time_fit, x.end)
        return QRectF(left, overview.top(), right - left, overview.height())

    def _draw_overview(self, painter, overview):
        painter.fillRect(overview, QColor("#2b2e38"))
        painter.setPen(QColor("#c6cad4"))
        painter.drawText(QRectF(overview.right() + 8, overview.top() - 2, 48, overview.height() + 4),
                         Qt.AlignmentFlag.AlignVCenter, "概览")
        trace = self.original if self.original.audio is not None else self.result
        if trace.audio is None:
            return
        painter.save()
        painter.setClipRect(overview.adjusted(-1, -1, 1, 1))
        if self._overview_cache is None or self._overview_cache[0] != overview:
            shape = self._wave_shape(overview.adjusted(0, 2, 0, -2), trace.audio, self._time_fit, self._amplitude_fit)
            self._overview_cache = (overview, shape)
        self._paint_wave(painter, self._overview_cache[1], QColor("#768599"))
        self._draw_range(painter, self._overview_view(overview), vertical=False)
        painter.restore()

    @staticmethod
    def _draw_range(painter, selection, vertical):
        painter.fillRect(selection, QColor(120, 178, 242, 44))
        painter.setPen(QPen(QColor("#8faad0"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(selection.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        painter.setPen(QPen(QColor("#abc6ea"), 2))
        if vertical:
            for edge in (selection.top(), selection.bottom()):
                painter.drawLine(QPointF(selection.left() + 2, edge), QPointF(selection.right() - 2, edge))
        else:
            for edge in (selection.left(), selection.right()):
                painter.drawLine(QPointF(edge, selection.top() + 5), QPointF(edge, selection.bottom() - 5))

    def _build_scene(self, geometry):
        scale = self.devicePixelRatioF()
        pixmap = QPixmap(self.size() * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(QColor("#20222a"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPointSize(11)
        painter.setFont(font)
        x, y = self.view
        x_ticks, step = self._ticks(x, max(2, int(geometry.ruler.width() / 98)))
        x_ticks = tuple(x_ticks)
        self._draw_ruler(painter, geometry.ruler, x, x_ticks, step)
        groups = ((self.original, self.result),) if self.overlay else ((self.original,), (self.result,))
        for lane, traces in zip(geometry.lanes, groups):
            self._draw_lane(painter, lane, x, y, x_ticks, traces)
        if not geometry.overview.isEmpty():
            self._draw_overview(painter, geometry.overview)
        painter.end()
        return pixmap

    def paintEvent(self, event):
        geometry = self._geometry()
        if self._scene is None:
            self._scene = self._build_scene(geometry)
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._scene)
        if self.spectrum or not self.duration:
            return
        x, _ = self.view
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#e4efff"), 1))
        at = self._map_x(geometry.overview, self._time_fit, self.position)
        painter.drawLine(QPointF(at, geometry.overview.top()), QPointF(at, geometry.overview.bottom()))
        if x.start <= self.position <= x.end:
            at = self._map_x(geometry.ruler, x, self.position)
            top = geometry.ruler.bottom() + 1
            painter.drawLine(QPointF(at, top), QPointF(at, geometry.lanes[-1].bottom()))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#e4efff"))
            painter.drawPolygon(QPolygonF([QPointF(at - 5, top - 7), QPointF(at + 5, top - 7), QPointF(at, top + 1)]))

    def _lane_at(self, point, geometry):
        return min(geometry.lanes, key=lambda lane: abs(lane.center().y() - point.y()))

    @staticmethod
    def _right_axis_at(point, geometry):
        return point.x() > geometry.ruler.right() and any(
            lane.top() <= point.y() <= lane.bottom() for lane in geometry.lanes
        )

    def wheelEvent(self, event):
        if not self.duration:
            event.ignore()
            return
        point = event.position()
        pixel_delta = event.pixelDelta()
        movement, scale = (event.angleDelta(), 120) if pixel_delta.isNull() else (pixel_delta, 55)
        dx, dy = movement.x() / scale, movement.y() / scale
        geometry = self._geometry()
        x, y = self.view
        track = self._track(self._lane_at(point, geometry))
        factor = 1.28 ** clamp(dy, -8, 8)
        if self._right_axis_at(point, geometry):
            if dy:
                anchor = y.at(clamp((track.bottom() - point.y()) / track.height(), 0, 1))
                self.zoom("y", factor, anchor)
        elif abs(dx) > abs(dy):
            self.set_view(x.shifted(-dx * x.span * 0.12), y)
        elif dy:
            fraction = clamp((point.x() - track.left()) / track.width(), 0, 1)
            other_anchor = y.at((track.bottom() - point.y()) / track.height()) if track.contains(point) else None
            self.zoom("x", factor, x.at(fraction), other_anchor)
        event.accept()

    def _seek_at(self, point, ruler):
        fraction = clamp((point.x() - ruler.left()) / ruler.width(), 0, 1)
        self.set_position(self.view[0].at(fraction))

    def _start_range_drag(self, point, rail, axis):
        x, y = self.view
        vertical = axis == "y"
        domain = self._vertical_domain(y) if vertical else self._time_fit
        selection = self._vertical_selection(rail, domain, y) if vertical else self._overview_view(rail)
        pixel = -point.y() if vertical else point.x()
        first = -selection.bottom() if vertical else selection.left()
        last = -selection.top() if vertical else selection.right()
        if last - first >= 14 and abs(pixel - first) < 6:
            kind = "range-start"
        elif last - first >= 14 and abs(pixel - last) < 6:
            kind = "range-end"
        else:
            kind = "range-move"
            if not selection.contains(point):
                fraction = (rail.bottom() - point.y()) / rail.height() if vertical else (point.x() - rail.left()) / rail.width()
                target = domain.at(fraction)
                if vertical:
                    self.set_view(x, y.shifted(target - y.at(0.5)).bounded(domain, 0))
                else:
                    self.set_view(x.shifted(target - x.at(0.5)), y)
                x, y = self.view
        return Drag(kind, point, x, y, axis, rail, domain)

    def mousePressEvent(self, event):
        if not self.duration or event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        position = event.position()
        geometry = self._geometry()
        x, y = self.view
        left_button = event.button() == Qt.MouseButton.LeftButton
        right_axis = self._right_axis_at(position, geometry)
        vertical_rail = self._vertical_rail(self._track(self._lane_at(position, geometry))) if right_axis else QRectF()
        if geometry.overview.contains(position) and left_button:
            drag = self._start_range_drag(position, geometry.overview, "x")
        elif vertical_rail.contains(position) and left_button:
            drag = self._start_range_drag(position, vertical_rail, "y")
        elif geometry.ruler.contains(position) and not self.spectrum and left_button:
            self._seek_at(position, geometry.ruler)
            drag = Drag("seek", position, x, y)
        elif right_axis:
            drag = Drag("vertical", position, x, y, "y")
        else:
            drag = Drag("pan" if left_button else "hand", position, x, y)
        self._drag = drag
        if drag.axis == "y":
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif drag.kind in ("seek", "range-start", "range-end"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        drag = self._drag
        if drag is None:
            geometry = self._geometry()
            cursor = Qt.CursorShape.OpenHandCursor
            if geometry.ruler.contains(event.position()) and not self.spectrum:
                cursor = Qt.CursorShape.CrossCursor
            elif self._right_axis_at(event.position(), geometry):
                cursor = Qt.CursorShape.SizeVerCursor
            self.setCursor(cursor)
            return
        position = event.position()
        delta = position - drag.origin
        drag.moved |= abs(delta.x()) + abs(delta.y()) >= 4
        if not drag.moved:
            return
        geometry = self._geometry()
        x, y = drag.x, drag.y
        if drag.kind == "seek":
            self._seek_at(position, geometry.ruler)
        elif drag.rail is not None:
            self._drag_axis_range(drag, delta)
        else:
            track = self._track(self._lane_at(drag.origin, geometry))
            if drag.kind == "vertical":
                y = y.shifted(delta.y() * y.span / track.height())
            else:
                x = x.shifted(-delta.x() * x.span / track.width())
            self.set_view(x, y)
        event.accept()

    def _drag_axis_range(self, drag, delta):
        vertical = drag.axis == "y"
        bounds = drag.bounds
        range_at_start = drag.y if vertical else drag.x
        movement = -delta.y() / drag.rail.height() if vertical else delta.x() / drag.rail.width()
        offset = bounds.span * movement
        minimum = min(bounds.span, (1 if self.spectrum else 0.0001) if vertical else 4 / SR)
        if drag.kind == "range-move":
            moved = range_at_start.shifted(offset).bounded(bounds, minimum)
            self.set_view(drag.x, moved) if vertical else self.set_view(moved, drag.y)
        else:
            start, end = range_at_start.start, range_at_start.end
            if drag.kind == "range-start":
                start = clamp(start + offset, bounds.start, end - minimum)
            else:
                end = clamp(end + offset, start + minimum, bounds.end)
            self._resize_axis(drag.axis, AxisRange(start, end), drag.x, drag.y)

    def mouseReleaseEvent(self, event):
        drag, self._drag = self._drag, None
        self.unsetCursor()
        if drag is None:
            super().mouseReleaseEvent(event)
            return
        if drag.kind == "seek" or (drag.kind == "pan" and not drag.moved and not self.spectrum):
            self._seek_at(event.position(), self._geometry().ruler)
            self.seekRequested.emit(self.position)
        event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.playToggleRequested.emit()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus):
            self.zoom("x", 1 / 1.4 if key == Qt.Key.Key_Minus else 1.4)
        elif not self.spectrum and key in (Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = max(1 / SR, self.view[0].span / 100)
            target = {Qt.Key.Key_Home: 0, Qt.Key.Key_End: self.duration,
                      Qt.Key.Key_Left: self.position - step, Qt.Key.Key_Right: self.position + step}
            self.set_position(target[key], follow=True)
            self.seekRequested.emit(self.position)
        else:
            super().keyPressEvent(event)
            return
        event.accept()
