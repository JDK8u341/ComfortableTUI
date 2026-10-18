"""Opaque, application-owned visual test background; never a terminal replacement."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget


class OpticalTestBackground(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Comfortable Glass / Optical test background")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        for at, color in ((0, "#133c62"), (.28, "#189898"), (.56, "#e7ae82"), (.8, "#c05674"), (1, "#243451")):
            gradient.setColorAt(at, QColor(color))
        painter.fillRect(self.rect(), gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 243, 196, 190))
        painter.drawEllipse(round(self.width() * .61), -180, 520, 520)
        painter.setBrush(QColor(28, 69, 122, 180))
        painter.drawEllipse(-160, round(self.height() * .4), 650, 650)
        painter.setPen(QPen(QColor(255, 255, 255, 95), 2))
        for x in range(0, self.width(), 36):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 36):
            painter.drawLine(0, y, self.width(), y)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(28, self.height() - 28, "OWNED OPTICAL TEST PATTERN / lines expose lensing and blur")
