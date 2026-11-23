"""Command line entry point for offline demonstrations and local consultations."""
import argparse
import json
from pathlib import Path
from contextlib import closing
from .core import Kernel

from .demo import run_demo


def main():
    parser = argparse.ArgumentParser(description='SQLite roundtable with optional local Ollama consultation')
    commands = parser.add_subparsers(dest='command', required=True)
    demo = commands.add_parser('demo', help='run the synthetic SQLite state-machine demonstration')
    demo.add_argument('--db', required=True, help='path for a new SQLite database')
    consult = commands.add_parser('consult', help='submit a weekly roster and plain-language preferences')
    consult.add_argument('input',type=Path)
    consult.add_argument('--db',required=True,type=Path)
    consult.add_argument('--model',default='qwen3.5:9b')
    verify = commands.add_parser('verify-batch',help='reconcile preference results offline')
    verify.add_argument('directory',type=Path)
    create = commands.add_parser('create', help='create a draft in a new or existing database')
    create.add_argument('db', type=Path)
    create.add_argument('title')
    create.add_argument('credits', type=int)
    join = commands.add_parser('join', help='seat a member on a draft topic')
    join.add_argument('db', type=Path)
    join.add_argument('topic')
    join.add_argument('member')
    join.add_argument('kind', choices=['expert','human','model','roundtable'])
    join.add_argument('--source')
    start = commands.add_parser('start', help='start a draft with seated members')
    start.add_argument('db', type=Path)
    start.add_argument('topic')
    post = commands.add_parser('post', help='record a member message and its cost in local credits')
    post.add_argument('db', type=Path)
    post.add_argument('topic')
    post.add_argument('member')
    post.add_argument('body')
    post.add_argument('--cost', type=int, default=1)
    top_up = commands.add_parser('top-up', help='add local credits to an open, independently funded topic')
    top_up.add_argument('db', type=Path)
    top_up.add_argument('topic')
    top_up.add_argument('amount', type=int)
    cite = commands.add_parser('cite', help='attach a resolved local proposal as historical evidence')
    cite.add_argument('db', type=Path)
    cite.add_argument('topic')
    cite.add_argument('source')
    delegate = commands.add_parser('delegate', help='fund a child roundtable on a draft parent')
    delegate.add_argument('db', type=Path)
    delegate.add_argument('parent')
    delegate.add_argument('member')
    delegate.add_argument('title')
    delegate.add_argument('budget', type=int)
    settle = commands.add_parser('settle', help='deliver a resolved child proposal and refund unused credits')
    settle.add_argument('db', type=Path)
    settle.add_argument('child')
    for name in ('propose','vote'):
        command=commands.add_parser(name)
        command.add_argument('db',type=Path)
        command.add_argument('topic')
        command.add_argument('version',type=int)
        if name=='propose':
            command.add_argument('body')
        else:
            command.add_argument('member')
            command.add_argument('ballot',choices=['yes','no'])
    args = parser.parse_args()
    try:
        if args.command=='consult':
            from examples.user_consult import consult
            result=consult(args.input,args.db,args.model)
        elif args.command=='verify-batch':
            from examples.verify_preferences import verify
            result=verify(args.directory)
        elif args.command in ('create','join','start','post','top-up','delegate','settle','cite','propose','vote'):
            if args.command != 'create' and not args.db.is_file():
                raise ValueError('database missing')
            with closing(Kernel(args.db)) as k:
                if args.command=='create':
                    result = k.get(k.create(args.title,args.credits))
                elif args.command=='delegate':
                    child = k.delegate(args.parent,args.member,args.title,args.budget)
                    result = dict(parent=args.parent, child=child, budget=args.budget)
                elif args.command=='settle':
                    result = dict(child=args.child, spent=k.settle(args.child))
                else:
                    if args.command=='join':
                        k.join(args.topic,args.member,kind=args.kind,source=args.source)
                    elif args.command=='start':
                        k.start(args.topic)
                    elif args.command=='top-up':
                        k.top_up(args.topic,args.amount)
                    elif args.command=='post':
                        if not k.post(args.topic,args.member,args.body,cost=args.cost):
                            raise ValueError('insufficient credits: topic paused, message not recorded')
                    elif args.command=='cite':
                        k.cite(args.topic,args.source)
                    elif args.command=='propose':
                        k.propose(args.topic,args.body,previous_version=args.version)
                    else:
                        k.vote(args.topic,args.member,args.ballot=='yes',version=args.version)
                    result=k.get(args.topic)
        else:
            result = run_demo(args.db)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
