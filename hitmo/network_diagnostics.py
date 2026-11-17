"""Read-only network and media-access diagnostic for HitMo stations."""
from __future__ import annotations

import argparse
import getpass
import ssl

from .api_client import HitMoAPIClient


def _connection_error_message(error: BaseException) -> str:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, ssl.SSLCertVerificationError):
            return "Certificado TLS inválido, não confiável ou incompatível com o nome do servidor."
        current = current.__cause__ or current.__context__
    message = str(error)
    if "CERTIFICATE_VERIFY_FAILED" in message or "certificate verify failed" in message.casefold():
        return "Certificado TLS inválido, não confiável ou incompatível com o nome do servidor."
    if "timed out" in message.casefold() or "tempo limite" in message.casefold():
        return "Servidor sem resposta dentro do limite de tempo; verifique rede e firewall."
    return "Não foi possível conectar ao serviço; verifique endereço, porta, rede e firewall."


def run_diagnostics(
    client: HitMoAPIClient,
    media_ids: list[int] | None = None,
    folder_ids: list[int] | None = None,
) -> tuple[list[tuple[str, str]], bool]:
    """Run actual remote API checks and optional server-side file probes."""
    results: list[tuple[str, str]] = []
    try:
        health = client.health()
        if health.get("status") != "ok":
            raise ConnectionError("O endpoint de disponibilidade retornou estado inesperado.")
    except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
        return [("Conexão/API", _connection_error_message(exc))], False
    results.append(("Conexão/API", "OK"))
    results.append(("TLS", "Válido e verificado" if client.base_url.lower().startswith("https://") else "HTTP local; TLS não usado"))

    try:
        identity = client.network_diagnostics()
    except RuntimeError as exc:
        message = str(exc)
        if " 401" in message:
            message = "Credenciais recusadas pelo serviço central."
        elif " 403" in message:
            message = "A conta autenticada não tem permissão para consultar o diagnóstico."
        else:
            message = _connection_error_message(exc)
        return results + [("Autenticação/permissão", message)], False
    except (ConnectionError, OSError, ValueError) as exc:
        return results + [("Autenticação/permissão", _connection_error_message(exc))], False
    results.append(("Autenticação", f"OK · {identity['authenticated_as']} ({identity['role']})"))
    results.append(("Permissões", ", ".join(identity.get("permissions", [])) or "nenhuma"))
    results.append(("Revisões", f"catálogo {identity['catalog_revision']} · programação {identity['program_revision']}"))
    results.append(("Verificação de arquivos", "executada no computador central que reproduz a mídia"))

    successful = True
    for media_id in media_ids or []:
        try:
            result = client.diagnose_media_path(media_id)
            status = f"{result['detail']} · {result['title']} (ID {result['id']})"
            results.append(("Material", status))
            successful = successful and result["status"] == "accessible"
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            results.append((f"Material ID {media_id}", _connection_error_message(exc)))
            successful = False
    for folder_id in folder_ids or []:
        try:
            result = client.diagnose_folder_path(folder_id)
            status = f"{result['detail']} · {result['title']} (ID {result['id']})"
            results.append(("Pasta", status))
            successful = successful and result["status"] == "accessible"
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            results.append((f"Pasta ID {folder_id}", _connection_error_message(exc)))
            successful = False

    # Exercise the two read scopes used by remote programming and reporting.
    for label, operation in (("Leitura da programação", client.programs), ("Leitura do histórico", lambda: client.history(limit=1))):
        try:
            operation()
            results.append((label, "permitida"))
        except RuntimeError as exc:
            results.append((label, f"sem permissão ou indisponível · {exc}"))
            successful = False
        except (ConnectionError, OSError, ValueError) as exc:
            results.append((label, _connection_error_message(exc)))
            successful = False
    return results, successful


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostica API, TLS, permissões e acesso a arquivos no servidor de reprodução HitMo."
    )
    parser.add_argument("--server", required=True, help="Endereço completo, por exemplo https://radio:8765")
    parser.add_argument("--username", help="Usuário da API; se omitido, será solicitado")
    parser.add_argument("--ca-file", help="CA privada em PEM, se necessária")
    parser.add_argument("--media-id", type=int, action="append", default=[], help="ID de material para testar no servidor; pode ser repetido")
    parser.add_argument("--folder-id", type=int, action="append", default=[], help="ID de pasta para testar no servidor; pode ser repetido")
    args = parser.parse_args(argv)
    username = args.username or input("Usuário da API: ").strip()
    password = getpass.getpass("Senha da API (não será exibida): ")
    client = HitMoAPIClient(args.server, username, password, timeout=10.0, ca_file=args.ca_file)
    results, successful = run_diagnostics(client, args.media_id, args.folder_id)
    print("Diagnóstico de rede HitMo")
    for label, result in results:
        print(f"{label}: {result}")
    if not args.media_id and not args.folder_id:
        print("Mídia: informe --media-id e/ou --folder-id para confirmar o acesso no servidor de reprodução.")
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
