import argparse
import json
import sys
from .application import run, compare, summary
from .storage import canonical, read_report


def main():
    parser = argparse.ArgumentParser(description='QuantOS-PM: virtual paper capital only')
    sub = parser.add_subparsers(dest='command', required=True)
    paper = sub.add_parser('paper')
    paper.add_argument('--config', required=True)
    paper.add_argument('--feed', required=True)
    paper.add_argument('--model', choices=('optimistic', 'baseline', 'pessimistic'))
    report = sub.add_parser('report')
    report.add_argument('--run-id', required=True)
    report.add_argument('--json', action='store_true')
    comp = sub.add_parser('compare')
    comp.add_argument('--feed', default='fixtures/btc_5m.jsonl')
    shadow = sub.add_parser('shadow')
    shadow.add_argument('--config', required=True)
    shadow.add_argument('--seconds', type=int, default=60)
    research = sub.add_parser('research')
    research.add_argument('--wallet', default='0x56f609c609975454bf851f0d91079fbc4a5fe251')
    research.add_argument('--max-pages', type=int, default=10000)
    research.add_argument('--resume', help='existing QuantOS-PM research directory; keep original cursor window')
    chain = sub.add_parser('research-chain')
    chain.add_argument('--directory', required=True)
    chain.add_argument('--limit', type=int, default=12)
    args = parser.parse_args()
    try:
        if args.command == 'paper':
            from .config import load
            if load(args.config).mode != 'paper':
                raise ValueError('paper command requires paper configuration')
            print(summary(run(args.config, args.feed, args.model)), end='')
        elif args.command == 'report':
            r = read_report(args.run_id)
            print(canonical(r) if args.json else summary(r))
        elif args.command == 'compare':
            print(canonical(compare(args.feed)))
        elif args.command == 'shadow':
            from .adapters.public import shadow_run
            result = shadow_run(args.config, args.seconds)
            print(canonical(result))
            if not result['public_feed_worked']:
                return 2
        elif args.command == 'research':
            from .forensics import research
            print(canonical(research(args.wallet, args.max_pages, args.resume)))
        elif args.command == 'research-chain':
            from .adapters.polygon import verify_samples
            result = verify_samples(args.directory, args.limit)
            print(canonical(result))
            if not result['verified']:
                return 2
    except (ValueError, OSError, RuntimeError) as error:
        print(f'QuantOS-PM blocked: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
