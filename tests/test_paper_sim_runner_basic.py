from src.runner.paper_sim_runner import run_simulation
from src.execution.persistence import init_db
import sqlite3
from src.execution.persistence import DB_PATH


def reset_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM positions")
    conn.commit()
    conn.close()


def test_sim_runner_executes():
    init_db()
    reset_db()

    result = run_simulation(steps=20, seed=42)

    assert result["steps"] == 20
    assert result["seed"] == 42
    assert "summary" in result
    assert isinstance(result["summary"], dict)
