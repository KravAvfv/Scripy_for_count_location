# PyInstaller spec — single-file, windowed SiteSizer build.
# Usage (from the repo root):  pyinstaller --noconfirm --clean packaging/sitesizer.spec
# The build scripts (build.ps1 / build.sh) wrap this and regenerate the icon + version info.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH is injected by PyInstaller
sys.path.insert(0, str(ROOT))
from sitesizer import __version__  # noqa: E402

PKG = ROOT / "sitesizer"
datas = [
    (str(PKG / "data"), "sitesizer/data"),
    (str(PKG / "i18n" / "uk.json"), "sitesizer/i18n"),
    (str(PKG / "i18n" / "en.json"), "sitesizer/i18n"),
    (str(PKG / "assets"), "sitesizer/assets"),
]

# Qt modules the app never touches — excluding them keeps the .exe small.
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtWebSockets",
    "PySide6.QtHttpServer", "tkinter", "matplotlib", "pandas", "numpy", "PIL", "pytest",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "sitesizer" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=["PySide6.QtSvg", "PySide6.QtPrintSupport"],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

is_win = sys.platform == "win32"
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LocalCount",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "packaging" / "sitesizer.ico") if is_win else None,
    version=str(ROOT / "packaging" / "version_info.txt") if is_win else None,
)
print(f"SiteSizer {__version__} spec loaded")
