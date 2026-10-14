"""Build native app icons from the SVG. SPDX-License-Identifier: GPL-3.0-only."""

from pathlib import Path
import os
import struct
import subprocess
import sys

from PyQt6.QtCore import QBuffer, QIODevice, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication([sys.argv[0]])
    assets = Path(__file__).resolve().parent.parent / "assets"
    renderer = QSvgRenderer(str(assets / "app.svg"))
    if not renderer.isValid():
        raise ValueError("The app icon SVG is invalid.")

    def render(size):
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError("Could not save the icon PNG.")
        return bytes(buffer.data())

    sizes = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
    pngs = {size: render(size) for size in sizes}
    (assets / "app.png").write_bytes(pngs[1024])

    # ICO supports a separate PNG payload for each display size.
    ico_sizes = sizes[:7]
    offset = 6 + 16 * len(ico_sizes)
    entries = []
    for size in ico_sizes:
        data = pngs[size]
        entries.append(
            struct.pack(
                "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset
            )
        )
        offset += len(data)
    (assets / "app.ico").write_bytes(
        struct.pack("<HHH", 0, 1, len(ico_sizes))
        + b"".join(entries)
        + b"".join(pngs[size] for size in ico_sizes)
    )

    if sys.platform == "darwin":
        iconset = assets.parent / "build" / "icons" / "app.iconset"
        iconset.mkdir(parents=True, exist_ok=True)
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                suffix = "@2x" if scale == 2 else ""
                (iconset / f"icon_{size}x{size}{suffix}.png").write_bytes(
                    pngs[size * scale]
                )
        subprocess.run(
            [
                "/usr/bin/iconutil",
                "-c",
                "icns",
                "-o",
                str(assets / "app.icns"),
                str(iconset),
            ],
            check=True,
        )
    print(
        "Built app.png, app.ico, and app.icns."
        if sys.platform == "darwin"
        else "Built app.png and app.ico. Use macOS to update app.icns."
    )
    app.quit()


if __name__ == "__main__":
    main()
