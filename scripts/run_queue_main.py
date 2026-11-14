#!/usr/bin/env python3
"""Один запуск frontend и стенда на публичной Google-таблице и расписании; запись оценок отключена."""
import argparse
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_queue_live as live
from queue_dev_server import frontend_server,require_free_backend


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8000,help='Порт backend')
    parser.add_argument('--frontend-port',type=int,default=5173,help='Порт frontend')
    parser.add_argument('--data-dir',default='data/queue-live')
    parser.add_argument('--check',action='store_true',help='Только проверить внешние источники, без серверов')
    parser.add_argument('--reset',action='store_true',help='Архивировать локальную историю и начать заново')
    parser.add_argument('--backend-only',action='store_true')
    args=parser.parse_args()
    if args.check and args.reset:parser.error('--check и --reset нельзя использовать вместе')
    if args.reset:
        import socket
        with socket.socket() as probe:
            try:probe.bind(('127.0.0.1',args.port))
            except OSError:parser.error('Перед очисткой остановите прежний backend через Ctrl+C')
    try:
        if not args.check:require_free_backend(args.port)
        directory,course,lessons=live.prepare(args)
        if args.check:return 0
        if args.reset:live.reset_local_state(directory)
        os.environ.update(FRONTEND_URL=f'http://localhost:{args.frontend_port}',PUBLIC_BASE_URL=f'http://localhost:{args.frontend_port}')
        app=live.create_app(directory,course,lessons,frontend_port=args.frontend_port)
        with frontend_server(ROOT,backend_port=args.port,frontend_port=args.frontend_port,enabled=not args.backend_only):
            import uvicorn
            uvicorn.run(app,host='127.0.0.1',port=args.port)
        return 0
    except KeyboardInterrupt:return 0
    except Exception as error:
        print(f'Запуск не завершён: {error}',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
