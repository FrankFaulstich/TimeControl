# PyInstaller spec for a single, self-contained Windows executable of the
# TimeControl desktop app (Streamlit GUI wrapped in a pywebview window).
# Build with:
#     pyinstaller TimeControl.spec --noconfirm
#
# This must run ON Windows - PyInstaller does not cross-compile, so a
# Windows .exe cannot be produced from macOS or Linux. See
# .github/workflows/build-windows.yaml, which runs this on GitHub's
# windows-latest runner so nobody needs a Windows machine to produce it.
#
# TimeTrackerSL_GUI.py (the entry point below) launches Streamlit and the
# optional MCP server as separate subprocesses via sys.executable - which,
# once frozen, IS this .exe rather than a Python interpreter. It handles
# that by re-invoking itself with a sentinel flag instead (see
# TimeTrackerSL_GUI.py's _run_streamlit_subprocess() and the top of its
# __main__ block) - nothing extra is needed here for that part.

from PyInstaller.utils.hooks import collect_all

datas = [
    ('sl/SL_Menu.py', 'sl'),
    ('sl/style.css', 'sl'),
    ('sl/style_dark.css', 'sl'),
    ('sl/style_claude.css', 'sl'),
    ('sl/style_blue.css', 'sl'),
    ('sl/icon.png', 'sl'),
    ('locale', 'locale'),
]
binaries = []
# sl/SL_Menu.py is carried in `datas`, which PyInstaller copies verbatim
# without ever scanning it for imports - so everything only that file reaches
# has to be named here or it is simply left out of the build. The sync
# modules degrade quietly when missing (SL_Menu catches ImportError and sets
# SYNC_AVAILABLE = False), which is exactly why their absence would go
# unnoticed until somebody wondered why the packaged build never syncs.
hiddenimports = [
    'TimeTrackerMCP_Server',
    'tt.TimeTracker',
    'tt.sync_client',
    'tt.sync_engine',
    'tt.sync_apply',
    'tt.sync_outbox',
    'tt.filelock',
]

# collect_all() pulls in a package's submodules, data files (including its
# own dist-info metadata, which streamlit's importlib.metadata-based version
# checks need at runtime) and native binaries alike - both streamlit and
# pywebview dynamically import backend modules by name (e.g. pywebview's
# platform-specific renderer) in ways PyInstaller's static bytecode scan
# cannot see on its own, so a hand-picked hiddenimports list would silently
# miss some of them.
for _pkg in ('streamlit', 'pywebview'):
    _datas, _binaries, _hiddenimports = collect_all(_pkg)
    datas += _datas
    binaries += _binaries
    hiddenimports += _hiddenimports

a = Analysis(
    ['TimeTrackerSL_GUI.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TimeControl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
