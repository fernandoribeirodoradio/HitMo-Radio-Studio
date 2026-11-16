# HitMo Radio Studio

Projeto de automação de rádio para Windows. Este repositório mantém o protótipo original em `main.py` e inclui a arquitetura modular 0.1.0 em `hitmo/`. São dois pontos de entrada disponíveis no mesmo projeto; o protótipo original foi preservado.

## Requisitos e instalação no Windows

Use Windows 10 ou 11 e Python 3.11 ou superior (Python 3.12 recomendado). Durante a instalação do Python, marque **Add Python to PATH**.

No PowerShell, na pasta do projeto:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Se o Python 3.12 não estiver disponível pelo inicializador `py`, substitua `py -3.12` por `py -3` para usar uma versão instalada compatível. Se a política local bloquear a ativação do ambiente, execute os comandos usando `.venv\Scripts\python.exe`.

As dependências cobrem os dois pontos de entrada: PySide6 para a interface e mutagen para leitura de metadados da arquitetura modular.

## Executar a arquitetura modular 0.1.0

```powershell
python -m hitmo.app
```

Ou, depois de instalar as dependências, execute `run_hitmo.bat`. O banco SQLite e as preferências ficam em `%APPDATA%\HitMo Radio Studio`. Para usar outro diretório de dados, defina `HITMO_DATA_DIR` antes de iniciar.

A arquitetura modular inclui biblioteca de mídia, playlist persistente com arrastar e soltar, player principal, pré-escuta, trilhas em loop, avanço automático, timeline musical e comercial, operadores e histórico com exportação CSV. Para iniciar, importe uma pasta de áudio na Biblioteca, adicione mídias à playlist ou cadastre eventos na Timeline.

Para consultar sem alterar o Playlist Digital, use **Cadastro > Ler catálogo do Playlist (Folders.xml)…** e selecione o arquivo de configuração. O HitMo salva um snapshot dos campos e caminhos no SQLite e informa quais diretórios existem neste computador. Esta operação não varre mídias, não segue atalhos e não escreve no diretório do Playlist. Detalhes e limitações estão em `docs/playlist-compatibility.md`.

A timeline inicia uma mídia por evento e assume o canal principal naquele momento. Os blocos publicados aceitam política por bloco para descarte de excedentes ou transição comercial Fixo com fade-out/corte configurável. O despacho registra eventos atrasados, perdidos e ignorados, usa uma janela de tolerância de 30 segundos (1000 ms para Fixo) e não interrompe o PGM ativo ao reconciliar retomadas. Ainda não há duração fixa geral de inserções, satélite/local, crossfade ou marcadores de mixagem. A seleção de operador do Studio é registrada no histórico. A API central tem autenticação e papéis próprios; isso não substitui a seleção de operador do Studio nem as permissões do Windows. A pré-escuta e as trilhas compartilham o dispositivo de saída configurado com o player principal. Formatos disponíveis dependem dos codecs e do backend de mídia do Windows.

## Serviço central e acesso remoto (Fase 2A)

O serviço HTTP opcional mantém o SQLite no computador da rádio. Inicialize a primeira conta administrativa e depois inicie o serviço em um terminal:

```powershell
python -m hitmo.server --init-admin
python -m hitmo.server
```

Por padrão, o serviço escuta somente em `127.0.0.1:8765`. Para adicionar uma conta de edição, use `python -m hitmo.server --add-user NOME --role editor`. O uso pela LAN exige certificado e chave TLS: `python -m hitmo.server --host 0.0.0.0 --port 8765 --cert C:\caminho\servidor.crt --key C:\caminho\servidor.key`. Configure o firewall do Windows para permitir a porta apenas na rede privada da emissora. O serviço não publica nem abre portas na internet.

Clientes remotos acessam a API autenticada e não recebem acesso ao arquivo SQLite. Caminhos indisponíveis no servidor podem ser preservados e são sinalizados; não tornam a mídia executável. A API não oferece comandos de reprodução. Consulte [docs/service-api.md](docs/service-api.md) para o serviço e [docs/config-manager.md](docs/config-manager.md) para o aplicativo independente.

Inicie o ConfigManager sozinho em qualquer estação com Python 3.12 e as dependências instaladas:

```powershell
python -m hitmo.config_manager_app
```

Ou execute `run_config_manager.bat`. O aplicativo conecta-se ao serviço central por HTTP no computador local ou HTTPS/TLS na rede. A Fase 2D permite administrar categorias, pastas, caminhos e ícones, além de consultar/reconciliar atalhos locais do HitMo. As referências importadas de `Folders.xml` continuam preservadas como snapshots de leitura; nenhum código do Playlist é interpretado como regra operacional sem implementação específica. Consulte [docs/fase-2d.md](docs/fase-2d.md) e [docs/playlist-compatibility.md](docs/playlist-compatibility.md).

Para montar, revisar e publicar grades remotamente, execute também:

```powershell
python -m hitmo.programming_manager_app
```

Ou abra `run_programming_manager.bat`. O módulo cria rascunhos revisionados, organiza blocos musicais/comerciais e sequências, publica quando a revisão-base ainda é atual e consulta ocorrências materializadas e histórico real. O serviço central não oferece comandos remotos de reprodução. Detalhes e limites em [docs/programacao-remota.md](docs/programacao-remota.md).

## Executar o protótipo original

O arquivo `main.py` permanece integralmente preservado e mantém a entrada e os comportamentos do protótipo remoto:

```powershell
python main.py
```

Esse protótipo permite adicionar arquivos ou pastas por arrastar e soltar, importar os arquivos suportados de uma pasta em ordem aleatória, selecionar e reordenar linhas da playlist, usar ações de reprodução pelo menu de contexto e remover itens da playlist sem apagar os arquivos de mídia originais. A interface original oferece controles básicos de áudio.

## Testes

Com o ambiente virtual ativo:

```powershell
python -m unittest discover -s tests -v
```

Os testes cobrem banco SQLite, importação e classificação de mídia, playlist, validação e disparo de eventos da timeline, integração de reprodução com histórico e operações do motor de áudio. Os testes de áudio usam WAV de teste e não medem qualidade audível nem validam todos os formatos ou dispositivos de transmissão.

## Organização

- `main.py`: protótipo original, mantido separado da arquitetura nova.
- `hitmo/app.py`: entrada da aplicação modular.
- `hitmo/audio_engine.py`: players do programa, pré-escuta e trilhas em loop.
- `hitmo/database.py` e `hitmo/services.py`: persistência SQLite e serviços de mídia, playlist, timeline e histórico.
- `hitmo/main_window.py`, `hitmo/config_manager.py` e `hitmo/operator_dialog.py`: interface e configuração.
- `hitmo/config_manager_app.py`: aplicativo desktop ConfigManager independente, conectado pela API central.
- `hitmo/programming_manager_app.py`: editor remoto de grades, blocos e sequências com rascunhos/publicação.
- `hitmo/api_client.py`, `hitmo/api_service.py` e `hitmo/server.py`: cliente, API e processo central.
- `hitmo/shortcuts.py`: cartões referenciais locais `.hitmo.json`, indexados pelo `media.id`.
- `tests/`: testes automatizados.
- `docs/reference-analysis.md`: análise funcional das referências.
- `docs/config-manager.md`: operação e limites do aplicativo independente.
- `docs/fase-2d.md`: catálogo remoto, atalhos, compatibilidade e pendências de rede.
- `docs/programacao-remota.md`: edição remota, publicação segura e consulta da execução.
