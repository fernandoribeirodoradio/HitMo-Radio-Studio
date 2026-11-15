from __future__ import annotations

import argparse
import getpass
import os
import sys

from .api_service import configure_logging, create_api_user, create_server
from .database import Database, default_data_dir


def _password_pair() -> str:
    first = getpass.getpass("Senha (mínimo 12 caracteres): ")
    second = getpass.getpass("Confirme a senha: ")
    if first != second:
        raise ValueError("As senhas não coincidem.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serviço HTTP central do HitMo Radio Studio")
    parser.add_argument("--host", default=os.environ.get("HITMO_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("HITMO_API_PORT", "8765")))
    parser.add_argument("--cert", default=os.environ.get("HITMO_API_CERT", ""))
    parser.add_argument("--key", default=os.environ.get("HITMO_API_KEY", ""))
    parser.add_argument("--init-admin", action="store_true", help="Criar o primeiro usuário administrador interativamente")
    parser.add_argument("--add-user", metavar="USUARIO", help="Criar usuário da API interativamente")
    parser.add_argument("--role", choices=("admin", "editor", "reader"), default="reader")
    args = parser.parse_args(argv)

    db = Database()
    users = int(db.one("SELECT COUNT(*) AS n FROM api_users WHERE active=1")["n"])
    if args.init_admin or args.add_user:
        if args.init_admin and users:
            print("Já existem usuários ativos; --init-admin não se aplica.", file=sys.stderr)
            return 2
        if args.add_user and not users:
            print("Crie primeiro um administrador com --init-admin.", file=sys.stderr)
            return 2
        username = args.add_user or input("Usuário administrador: ").strip()
        role = "admin" if args.init_admin else args.role
        try:
            create_api_user(db, username, _password_pair(), role)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Usuário {username} criado com papel {role}.")
        return 0
    if not users:
        print("Nenhum usuário da API configurado. Execute: python -m hitmo.server --init-admin", file=sys.stderr)
        return 2
    if not 1 <= args.port <= 65535:
        print("A porta deve estar entre 1 e 65535.", file=sys.stderr)
        return 2

    logger = configure_logging()
    try:
        server = create_server(db, args.host, args.port, args.cert, args.key)
    except (OSError, ValueError) as exc:
        logger.error("Não foi possível iniciar o serviço: %s", exc)
        print(str(exc), file=sys.stderr)
        return 2
    address = f"https://{args.host}:{args.port}" if args.cert else f"http://{args.host}:{args.port}"
    logger.info("HitMo API ouvindo em %s; banco local %s", address, db.path)
    print(f"HitMo API ouvindo em {address}")
    print(f"Banco SQLite local: {db.path}")
    print("Interrompa com Ctrl+C. HTTP sem TLS só é aceito em loopback.")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        logger.info("Encerramento solicitado pelo operador.")
    finally:
        server.shutdown()
        server.server_close()
        logger.info("Serviço encerrado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
