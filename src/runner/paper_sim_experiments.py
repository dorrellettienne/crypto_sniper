from src.runner.paper_sim_runner import format_simulation_summary, run_simulation


def run_seed_sweep(steps: int, seeds: list[int], usd_size: float = 50.0) -> list[dict]:
    """
    Runs deterministic paper simulations across multiple seeds.
    Returns normalized summaries for comparison.
    """
    results = []
    for seed in seeds:
        sim_result = run_simulation(steps=steps, seed=seed, usd_size=usd_size)
        normalized = format_simulation_summary(sim_result)
        normalized["seed"] = seed
        results.append(normalized)
    return results


def summarize_seed_sweep(runs: list[dict]) -> dict:
    """
    Builds a compact aggregate summary from normalized seed-sweep runs.
    """
    if not runs:
        return {
            "run_count": 0,
            "avg_total_pnl": 0.0,
            "avg_total_trades": 0.0,
            "avg_win_rate": 0.0,
            "best_total_pnl": 0.0,
            "worst_total_pnl": 0.0,
        }

    pnls = [float(run["summary"]["total_pnl"]) for run in runs]
    trades = [float(run["summary"]["total_trades"]) for run in runs]
    win_rates = [float(run["summary"]["win_rate"]) for run in runs]

    return {
        "run_count": len(runs),
        "avg_total_pnl": round(sum(pnls) / len(pnls), 4),
        "avg_total_trades": round(sum(trades) / len(trades), 4),
        "avg_win_rate": round(sum(win_rates) / len(win_rates), 4),
        "best_total_pnl": round(max(pnls), 4),
        "worst_total_pnl": round(min(pnls), 4),
    }
