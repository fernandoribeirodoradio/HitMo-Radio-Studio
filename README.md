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

A timeline inicia uma mídia por evento e assume o canal principal naquele momento. Ainda não há montagem automática de blocos, duração fixa de inserções, satélite/local, corte programado, crossfade ou marcadores de mixagem. A seleção de operador é registrada no histórico; autenticação e permissões individuais ainda não estão implementadas. A pré-escuta e as trilhas compartilham o dispositivo de saída configurado com o player principal. Formatos disponíveis dependem dos codecs e do backend de mídia do Windows.

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
- `tests/`: testes automatizados.
- `docs/reference-analysis.md`: análise funcional das referências.
