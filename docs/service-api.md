# Serviço central HitMo Radio Studio — Fase 2A

## Arquitetura e limites

O serviço roda no computador da rádio e é o único ponto de acesso remoto ao banco SQLite. Não compartilhe o arquivo do banco em uma unidade de rede. A API serve cadastro, consulta e programação; não expõe comandos do motor de áudio. Os clientes remotos ainda não têm uma interface gráfica própria.

O serviço inicia em `127.0.0.1:8765`, acessível apenas no computador local. Endereços LAN exigem TLS com certificado e chave. HTTP sem TLS em endereço não local é recusado. A autenticação da API não substitui as contas do Windows, firewall nem as regras de segurança da rede.

## Instalação e inicialização no Windows

Na pasta do projeto, com o ambiente virtual criado e dependências instaladas:

```powershell
.venv\Scripts\Activate.ps1
python -m hitmo.server --init-admin
```

Informe usuário e senha quando solicitado. A senha deve ter pelo menos 12 caracteres e é armazenada como hash PBKDF2 com salt. Para criar contas adicionais:

```powershell
python -m hitmo.server --add-user catalogo --role editor
python -m hitmo.server --add-user consulta --role reader
```

Inicie o servidor em um terminal dedicado:

```powershell
python -m hitmo.server
```

O serviço usa `%APPDATA%\HitMo Radio Studio\hitmo.sqlite3`, igual ao Studio. Para isolar dados em um ambiente de teste, defina `HITMO_DATA_DIR` antes de iniciar ambos. A configuração por variável de ambiente também permite trocar host e porta (`HITMO_API_HOST`, `HITMO_API_PORT`); certificados podem ser definidos por `HITMO_API_CERT` e `HITMO_API_KEY`.

### Acesso pela LAN

Instale um certificado e sua chave privada no computador da rádio e restrinja a porta no Firewall do Windows à rede privada necessária. Exemplo:

```powershell
python -m hitmo.server --host 0.0.0.0 --port 8765 --cert C:\certs\hitmo.crt --key C:\certs\hitmo.key
```

Use `https://NOME-OU-IP-DA-RADIO:8765` nos clientes. A API não distribui certificados, não abre regra de firewall e não encaminha tráfego pela internet. A distribuição confiável do certificado aos clientes ainda precisa ser configurada pela estação.

As mídias precisam existir no caminho visto pelo computador da rádio. O cadastro remoto não envia os arquivos de áudio; se os arquivos estiverem em armazenamento compartilhado, monte e proteja esse armazenamento separadamente. O banco SQLite nunca é acessado diretamente pelo cliente.

## Rotas

Todas as rotas abaixo, exceto `/healthz`, exigem HTTP Basic Auth sobre loopback ou HTTPS. Escritas de catálogo exigem `If-Match` com a revisão global consultada em `/api/v1/version`. Conflitos retornam HTTP 409. Papéis `reader`, `editor` e `admin` têm permissões distintas.

| Método e rota | Acesso | Uso |
| --- | --- | --- |
| `GET /healthz` | Sem credencial | Disponibilidade e versão |
| `GET /api/v1/version` | Leitura | Versão, revisão do catálogo e da programação |
| `GET /api/v1/categories` | Leitura | Categorias; `?include_inactive=1` inclui inativas |
| `GET /api/v1/folders` | Leitura | Pastas cadastradas |
| `GET /api/v1/playlist-references` | Leitura | Snapshot mais recente de Folders.xml, sem associação automática ao catálogo |
| `GET /api/v1/materials` | Leitura | Materiais ativos; `?include_inactive=1` inclui desativados |
| `POST /api/v1/materials` | Edição | Cadastrar material; exige revisão do catálogo |
| `PUT /api/v1/materials/{id}` | Edição | Atualizar metadados; exige revisão do catálogo |
| `POST /api/v1/materials/{id}/deactivate` | Edição | Desativar cadastro sem apagar arquivo nem histórico |
| `POST /api/v1/materials/{id}/restore` | Edição | Restaurar cadastro desativado |
| `GET /api/v1/programs` | Leitura | Revisão e blocos salvos |
| `GET, POST /api/v1/program-drafts` | Leitura ou edição | Listar e criar rascunhos baseados em revisão |
| `GET, PUT /api/v1/program-drafts/{id}` | Leitura ou edição | Consultar e alterar rascunho próprio |
| `POST /api/v1/program-drafts/{id}/publish` | Edição | Publicar se a revisão-base ainda for atual |
| `GET /api/v1/history` | Leitura | Histórico efetivamente registrado; aceita `limit` e `offset` |
| `GET /api/v1/clients` | Admin | Clientes autenticados recentemente ativos |

Publicar uma programação altera os blocos salvos para execuções futuras. Ocorrências já geradas, seus itens congelados e o histórico não são removidos ou reescritos. Materiais são desativados logicamente, nunca apagados da unidade de armazenamento. A revisão de catálogo e a revisão de programação são separadas.

O serviço retorna os campos do registro, `material_kind` derivado da categoria e `path_exists_on_server`. Escritas aceitam `folder_id` para uma pasta ativa existente, `text_content` para categorias de tipo texto, e `icon_path` como metadado de imagem. Um caminho indisponível pode ser preservado no cadastro sem trocar o ID. Para itens não sonoros sem arquivo, são aceitas referências lógicas `hitmo://text/...` ou `hitmo://material/...`; elas não são caminhos físicos nem são executáveis pelo agendador de áudio.

`GET /api/v1/playlist-references` fornece somente a importação mais recente de `Folders.xml`. Seus IDs e códigos permanecem referências do Playlist, sem associação automática aos IDs de mídia do HitMo.

## Exemplo com o cliente Python

O módulo `hitmo.api_client.HitMoAPIClient` usa a biblioteca padrão:

```python
from hitmo.api_client import HitMoAPIClient

api = HitMoAPIClient("https://radio.exemplo.local:8765", "catalogo", senha)
print(api.health())
print(api.categories())
print(api.materials())
print(api.programs())
print(api.history(limit=50))
```

Os métodos de escrita exigem a revisão consultada, por exemplo:

```python
revision = api.version()["catalog_revision"]
api.add_material(
    {"path": r"D:\Audio\musica.wav", "title": "Música", "category": "Música"},
    revision,
)
```

## Verificação

Execute a suíte com Python 3.12:

```powershell
python -m unittest discover -s tests -v
```

Os testes do serviço usam banco temporário e processos locais. Eles validam API entre processos no mesmo computador, mas não comprovam conectividade entre duas máquinas, políticas de firewall, certificado implantado, nem operação de áudio em hardware real. Para o ConfigManager independente, veja [config-manager.md](config-manager.md).
