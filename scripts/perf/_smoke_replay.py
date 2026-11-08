from pathlib import Path
import sys, json
sys.path.insert(0, ".")
from regression_replay import run


ROOT = Path(__file__).resolve().parents[2]
TOKEN = "PgEECYNIneqtk7fb-dyYvA4nzj3YTcjqPOVjZqAJATQ"
JMX = str(ROOT / "test_results/jmeter/scripts/xianyu_load_test_reads.jmx")
meta = run(JMX, "http://127.0.0.1:8011", TOKEN, str(ROOT / "test_results/jmeter/runs_smoke/reads_smoke.jsonl"), duration_override=8)
print("SMOKE_META", json.dumps(meta, ensure_ascii=False))
