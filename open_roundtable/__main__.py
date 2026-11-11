"""Offline command line entry point. Never connects to a model provider."""
import argparse
import json

from .demo import run_demo


def main():
    parser = argparse.ArgumentParser(description='Offline synthetic roundtable; no real models')
    commands = parser.add_subparsers(dest='command', required=True)
    demo = commands.add_parser('demo', help='run the synthetic SQLite state-machine demonstration')
    demo.add_argument('--db', required=True, help='path for a new SQLite database')
    args = parser.parse_args()
    try:
        result = run_demo(args.db)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
