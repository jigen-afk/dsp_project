"""Render this application's own Qt widgets for visual QA (no desktop automation)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase,QFont
from dspstudio.app import MainWindow,STYLE
from dspstudio.diagnostics import synthetic_voice
from dspstudio.dsp import add_echo,EchoConfig

destination=Path(sys.argv[1])
destination.mkdir(parents=True,exist_ok=True)
app=QApplication([])
for font in ("C:/Windows/Fonts/msyh.ttc","C:/Windows/Fonts/msyhbd.ttc"):
    QFontDatabase.addApplicationFont(font)
app.setFont(QFont("Microsoft YaHei",10))
app.setStyle("Fusion")
app.setStyleSheet(STYLE)
with tempfile.TemporaryDirectory() as temp:
    window=MainWindow(temp)
    window.resize(1200,950)
    window.show()
    window.set_original(synthetic_voice(),"合成测试音（界面预览）")
    echo,scale=add_echo(window.original,EchoConfig())
    window.set_result(echo,"回声生成",{"config":EchoConfig(),"scale":scale,"length":len(window.original)})
    for index,name in enumerate(["01_语音增强","02_回声实验","03_成员识别","04_创意音效"]):
        window.tabs.setCurrentIndex(index)
        app.processEvents()
        window.grab().save(str(destination/(name+".png")))
    window.close()
