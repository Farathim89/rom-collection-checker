"""Draws assets/icon.png (256 px) and assets/icon.ico (16-256 px): a game cartridge with a check mark.
Run with the project's Python:  python assets/make_icon.py"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

HERE = Path(__file__).parent


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    bg = QLinearGradient(0, 0, size, size)
    bg.setColorAt(0, QColor("#e9c24a"))
    bg.setColorAt(1, QColor("#a8862a"))
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 52 * s, 52 * s)
    # the cartridge: body with a notch, a label and contacts
    cart = QPainterPath()
    cart.moveTo(64 * s, 48 * s)
    cart.lineTo(192 * s, 48 * s)
    cart.lineTo(192 * s, 196 * s)
    cart.lineTo(176 * s, 212 * s)
    cart.lineTo(80 * s, 212 * s)
    cart.lineTo(64 * s, 196 * s)
    cart.closeSubpath()
    p.setBrush(QColor("#1b1b1f"))
    p.drawPath(cart)
    p.setBrush(QColor("#f4ecd8"))
    p.drawRoundedRect(QRectF(82 * s, 66 * s, 92 * s, 78 * s), 8 * s, 8 * s)
    p.setBrush(QColor("#d4af37"))
    for i in range(6):
        p.drawRect(QRectF((88 + i * 14) * s, 180 * s, 8 * s, 22 * s))
    # the check mark on the label
    pen = QPen(QColor("#2e8b3e"), 15 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPolyline([QPointF(103 * s, 106 * s), QPointF(122 * s, 125 * s), QPointF(155 * s, 86 * s)])
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    draw(256).save(str(HERE / "icon.png"))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    pngs = [png_bytes(draw(n)) for n in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for n, data in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    (HERE / "icon.ico").write_bytes(head + entries + b"".join(pngs))
    print("icon.png and icon.ico written")
