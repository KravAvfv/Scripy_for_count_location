"""Render the LocalCount brand mark to packaging/sitesizer.ico (Windows), .icns (macOS) and .png."""

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

    def png(size: int) -> bytes:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        render(size).save(buf, "PNG")  # type: ignore[call-overload]
        buf.close()
        return bytes(data.data())

    for s in sizes:
        blobs.append(png(s))
    head = BytesIO()
    head.write(struct.pack("<HHH", 0, 1, len(sizes)))
    offset = 6 + 16 * len(sizes)
    for s, blob in zip(sizes, blobs, strict=True):
        head.write(struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)
    (out / "sitesizer.ico").write_bytes(head.getvalue() + b"".join(blobs))
    # macOS .icns: PNG images under their type codes (16…1024 px, Retina variants included)
    icns_types = [
        ("icp4", 16), ("icp5", 32), ("icp6", 64), ("ic07", 128), ("ic08", 256), ("ic09", 512),
        ("ic10", 1024), ("ic11", 32), ("ic12", 64), ("ic13", 256), ("ic14", 512),
    ]  # fmt: skip
    chunks = b"".join(
        code.encode() + struct.pack(">I", len(blob) + 8) + blob for code, blob in ((c, png(n)) for c, n in icns_types)
    )
    (out / "sitesizer.icns").write_bytes(b"icns" + struct.pack(">I", len(chunks) + 8) + chunks)
    print("icons written to", out)


if __name__ == "__main__":
    main()
