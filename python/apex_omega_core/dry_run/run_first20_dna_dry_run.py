import argparse
from .dry_run_orchestrator import DryRunOrchestrator


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--dashboard-stream', action='store_true')
    p.add_argument('--no-broadcast', action='store_true')
    args = p.parse_args()
    orchestrator = DryRunOrchestrator()
    summary = orchestrator.run(limit=args.limit)
    print(summary)


if __name__ == '__main__':
    main()
