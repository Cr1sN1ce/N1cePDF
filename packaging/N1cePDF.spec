# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for N1cePDF (onedir, windowed, no console, UPX off).

Build (from the project root, inside the build virtual environment):
    python -m PyInstaller packaging/N1cePDF.spec --noconfirm --clean --distpath dist --workpath build/pyinstaller

Output: dist/N1cePDF/N1cePDF.exe + dist/N1cePDF/_internal/ (bundled Python, Qt, PDFium, qpdf, ...).
onedir (not onefile) is deliberate: faster start-up (no temp extraction), fewer antivirus false positives,
and Qt/LGPL libraries stay separate, replaceable files.
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

HERE = Path(SPECPATH).resolve()          # noqa: F821  (SPECPATH is injected by PyInstaller)
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from spec_helpers import license_datas, sibling_libs  # noqa: E402

IS_WIN = sys.platform == "win32"

# --- third-party packages that carry native libraries or data PyInstaller must not miss -------------
datas, binaries, hiddenimports = [], [], []
for pkg in ("pikepdf", "pypdfium2", "pypdfium2_raw"):          # qpdf / PDFium binaries + version data
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("reportlab")                        # ReportLab's own font/data files
hiddenimports += collect_submodules("n1cepdf")                  # includes lazily imported modules
hiddenimports += ["lxml.etree", "lxml._elementpath", "PySide6.QtSvg"]

binaries += sibling_libs(["pikepdf", "Pillow"])                 # pikepdf.libs / pillow.libs next to the package

# --- application resources (read at run time through n1cepdf.runtime.resource_path) -----------------
datas += [(str(ROOT / "assets" / "n1cepdf.png"), "assets"),
          (str(ROOT / "assets" / "n1cepdf.ico"), "assets")]

# --- licence texts of everything we ship (LGPL/MPL/BSD notices travel with the binaries) ------------
lic_datas, lic_problems = license_datas(
    ["PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "pypdfium2", "pikepdf", "reportlab",
     "Pillow", "lxml", "packaging"])
datas += lic_datas
for msg in lic_problems:
    print(f"N1cePDF.spec WARNING (licences): {msg}")

# Qt modules the application never imports (it uses QtCore/QtGui/QtWidgets/QtSvg only). Excluding them
# keeps their large DLLs out of the installer.
EXCLUDES = ["tkinter", "pytest", "pytestqt", "_pytest", "IPython", "matplotlib", "numpy"] + [
    "PySide6." + m for m in (
        "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebSockets", "QtWebView",
        "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras",
        "QtQuick", "QtQuickWidgets", "QtQuick3D", "QtQuickControls2", "QtQml", "QtMultimedia", "QtMultimediaWidgets",
        "QtCharts", "QtDataVisualization", "QtGraphs", "QtBluetooth", "QtNfc", "QtPositioning", "QtLocation",
        "QtSensors", "QtSerialPort", "QtSerialBus", "QtPdf", "QtPdfWidgets", "QtDesigner", "QtHelp",
        "QtSql", "QtRemoteObjects", "QtScxml", "QtStateMachine", "QtTextToSpeech", "QtSpatialAudio",
        "QtNetworkAuth", "QtHttpServer", "QtTest")]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe_kwargs = {}
if IS_WIN:                                                       # icon / version resource are Windows-only
    exe_kwargs["icon"] = str(ROOT / "assets" / "n1cepdf.ico")
    exe_kwargs["version"] = str(HERE / "version_info.txt")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,       # onedir
    name="N1cePDF",
    debug=False,
    strip=False,
    upx=False,                   # UPX corrupts some Qt DLLs and raises antivirus false positives
    console=False,               # GUI subsystem: no terminal window
    disable_windowed_traceback=False,
    **exe_kwargs,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="N1cePDF")
