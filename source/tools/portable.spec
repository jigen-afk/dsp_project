from pathlib import Path


source_dir = Path(SPECPATH).parent
analysis = Analysis(
    [str(source_dir / "main.py")],
    excludes=[
        "matplotlib", "pandas", "torch", "sklearn", "pytest", "IPython",
        "tkinter", "numba", "cv2", "sphinx", "docutils", "PIL",
        "pygments", "babel", "jinja2", "ssl", "_ssl", "_hashlib",
        "PySide6.QtNetwork", "zstandard",
    ],
)

# The app draws with Qt Widgets and QPainter and reads only local audio.
# Keep the Windows and offscreen platform plugins for the app and its self-test.
unused_binaries = {
    "pyside6/opengl32sw.dll",
    "pyside6/qt6network.dll",
    "pyside6/qtnetwork.pyd",
    "pyside6/plugins/generic/qtuiotouchplugin.dll",
    "libssl-3-x64.dll",
    "libcrypto-3-x64.dll",
}
network_plugins = ("pyside6/plugins/networkinformation/", "pyside6/plugins/tls/")


def keep_binary(entry):
    destination = entry[0].replace("\\", "/").lower()
    return destination not in unused_binaries and not destination.startswith(network_plugins)


def keep_data(entry):
    destination = entry[0].replace("\\", "/").lower()
    if not destination.startswith("pyside6/translations/"):
        return True
    return destination.endswith(("_zh_cn.qm", "_zh_tw.qm", "_en.qm"))


analysis.binaries = [entry for entry in analysis.binaries if keep_binary(entry)]
analysis.datas = [entry for entry in analysis.datas if keep_data(entry)]

archive = PYZ(analysis.pure)
exe = EXE(
    archive,
    analysis.scripts,
    exclude_binaries=True,
    name="DSPStudio",
    console=False,
    upx=False,
)
bundle = COLLECT(exe, analysis.binaries, analysis.datas, name="DSPStudio", upx=False)
