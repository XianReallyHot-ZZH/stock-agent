"""CLI entry for the strategy parameter sweep (logic in stockagent.backtest.sweep)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.backtest.sweep import main

if __name__ == "__main__":
    main()
