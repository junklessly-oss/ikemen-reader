"""
make_icon.py - build assets/Icon.ico (the .exe's icon) from assets/Icon.png.

    py make_icon.py

Windows shows a program's icon at many sizes (16 px in lists, up to 256 px in large views), so the .ico holds
each size, every one scaled smoothly from the original picture.
"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage

SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def main() -> None:
    QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])
    here = Path(__file__).resolve().parent / "assets"
    src = QImage(str(here / "Icon.png"))
    if src.isNull():
        sys.exit("assets/Icon.png couldn't be read")
    src = src.convertToFormat(QImage.Format_ARGB32)
    images = []
    for size in SIZES:
        img = src.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if img.width() != size or img.height() != size:          # (a non-square picture: centre it)
            square = QImage(size, size, QImage.Format_ARGB32)
            square.fill(Qt.transparent)
            from PySide6.QtGui import QPainter
            p = QPainter(square)
            p.drawImage((size - img.width()) // 2, (size - img.height()) // 2, img)
            p.end()
            img = square
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        images.append((size, bytes(buf.data())))
    # ICO file: a header, one directory entry per size, then each size as PNG data
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for size, data in images:
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for _, data in images:
        out += data
    (here / "Icon.ico").write_bytes(out)
    print(f"assets/Icon.ico: {len(images)} sizes, {len(out) // 1024} KB")


if __name__ == "__main__":
    main()
