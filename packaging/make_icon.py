"""Render the LocalCount brand mark to packaging/sitesizer.ico and .png (multi-size)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402


def render(size: int) -> QImage:
    from sitesizer.exporters.diagram import paint_brand

    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    paint_brand(p, QRectF(0, 0, size, size))
    p.end()
    return img


def main() -> None:
    QGuiApplication.instance() or QGuiApplication([])
    out = Path(__file__).resolve().parent
    render(256).save(str(out / "sitesizer.png"))
    # Qt's ICO writer stores one image per file; build a proper multi-size .ico by hand.
    import struct
    from io import BytesIO

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    sizes = [16, 24, 32, 48, 64, 128, 256]
    blobs = []
    for s in sizes:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        render(s).save(buf, "PNG")  # type: ignore[call-overload]
        buf.close()
        blobs.append(bytes(data.data()))
    head = BytesIO()
    head.write(struct.pack("<HHH", 0, 1, len(sizes)))
    offset = 6 + 16 * len(sizes)
    for s, blob in zip(sizes, blobs, strict=True):
        head.write(struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)
    (out / "sitesizer.ico").write_bytes(head.getvalue() + b"".join(blobs))
    print("icons written to", out)


if __name__ == "__main__":
    main()
