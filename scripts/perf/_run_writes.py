from pathlib import Path
import sys
sys.path.insert(0, ".")
from regression_replay import run


ROOT = Path(__file__).resolve().parents[2]
TOKEN = "PgEECYNIneqtk7fb-dyYvA4nzj3YTcjqPOVjZqAJATQ"
JMX = str(ROOT / "test_results/jmeter/scripts/xianyu_load_test_writes.jmx")
OUT = str(ROOT / "test_results/jmeter/runs_p0p1/writes.jsonl")
run(JMX, "http://127.0.0.1:8011", TOKEN, OUT)
