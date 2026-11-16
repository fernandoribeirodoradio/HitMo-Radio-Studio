# Programação remota HitMo — Fase 2C

## Inicialização independente

Na estação remota, com o repositório atualizado para esta branch e Python 3.12:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m hitmo.programming_manager_app
```

Também é possível abrir `run_programming_manager.bat`. O Studio não precisa estar aberto na estação remota. A primeira tela usa a conexão autenticada do ConfigManager: endereço/IP, porta, HTTP local ou HTTPS/TLS, CA se necessária, usuário e senha. A senha fica apenas em memória durante a sessão.

O computador da rádio deve estar executando `python -m hitmo.server`. Para uma segunda máquina, use HTTPS com certificado e firewall restrito à LAN; consulte [service-api.md](service-api.md). Não compartilhe o SQLite.

## Fluxo de edição

1. A aba **Grade e rascunhos** carrega a revisão publicada e os blocos musicais/comerciais.
2. Crie ou edite blocos com nome, tipo, horário recorrente, dias e estado ativo/pausado.
3. Monte a sequência com materiais ativos de áudio, incluindo a ordem de execução.
4. **Salvar rascunho** grava uma cópia identificada por usuário, revisão-base e revisão do rascunho. Não muda a grade que o Studio executa.
5. **Publicar programação** envia somente um rascunho salvo. O servidor confirma que a revisão-base ainda é vigente e valida os materiais novamente.
6. Em HTTP 409, a grade foi alterada em paralelo ou desde a criação do rascunho. A tentativa não publica por cima da nova revisão; atualize a grade e reconcilie em outro rascunho.

Itens antigos sem `media.id` são mostrados como indisponíveis. O editor bloqueia salvar até que sejam explicitamente removidos ou substituídos. Blocos ativos sem itens também não podem ser publicados. Remover um bloco do rascunho o desativa na publicação; o registro não é apagado.

## Proteção da execução em curso

O serviço publica dentro de transação SQLite com controle de revisão. O motor local cria `program_occurrences` e cópias imutáveis dos itens da ocorrência em `program_occurrence_items`. A publicação troca a grade salva para disparos futuros e não reescreve essas cópias, o estado da ocorrência nem `playback_history`. A suíte inclui um teste integrado que atualiza um bloco e publica uma nova sequência enquanto uma ocorrência está marcada como `running`, e confirma que os itens e o histórico em execução permanecem iguais.

Isso valida a preservação no modelo de dados e na API, não uma transmissão de áudio física. A interface remota não dispõe de comandos PLAY/PAUSE/STOP/EXPULSAR.

## Consultar programação e execução real

- **Grade publicada:** horários e sequências que o sistema usará em novas ocorrências.
- **Ocorrências programadas:** itens que o agendador já materializou, com estado, operador e horários por item. O endpoint é `GET /api/v1/occurrences`; permite filtrar `from`, `to`, `status`, `limit` e `offset`.
- **Histórico executado:** `playback_history`, consultado por `GET /api/v1/history`, com horários reais de início/fim, material, operador e situação.

Uma grade recorrente futura ainda não é uma ocorrência materializada: o agendador atual cria a ocorrência quando chega o horário configurado. A tela mantém essa distinção e não apresenta uma definição futura da grade como execução ocorrida.

## Limites e validação

- Blocos recorrentes de música e comercial podem ser editados remotamente; a edição remota de eventos de timeline individuais ainda não faz parte desta fase.
- O histórico e as ocorrências são consultados, não editados pelo módulo remoto.
- Os testes usam processos separados em loopback e SQLite temporário. Nenhuma conexão entre duas máquinas físicas, firewall real ou certificado implantado foi validado; isso fica como próxima verificação no estúdio.
- Testes de banco e API não provam continuidade de áudio em hardware real.

Execute a suíte com Python 3.12:

```powershell
python -m unittest discover -s tests -v
```
