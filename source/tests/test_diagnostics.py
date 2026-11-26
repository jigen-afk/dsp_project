import json
from dspstudio.diagnostics import main


def test_standalone_report_and_demo_files(tmp_path):
    report=tmp_path/"reports"/"self_check.json"
    demos=tmp_path/"samples"
    assert main(["--report",str(report),"--make-demo",str(demos)])==0
    result=json.loads(report.read_text(encoding="utf-8"))
    assert result["passed"]
    assert len(list(demos.glob("*.wav")))==7
    assert result["checks"]["speaker_pipeline"]["restart_persistence"]
