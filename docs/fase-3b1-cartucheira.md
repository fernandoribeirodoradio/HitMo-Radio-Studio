# Fase 3B.1 — Cartucheira

## Implementação

O Studio mantém 12 posições permanentes (`cartouche_slots`, números 1–12) e cada posição referencia um `media.id` do catálogo local central. A associação e a remoção alteram somente o vínculo do cartucho. O cadastro, o arquivo físico e o histórico não são apagados. Se o material for removido do catálogo, o slot mantém o ID numérico e a interface o apresenta como vazio até uma nova associação.

O `AudioEngine` cria um player e uma saída Qt por posição. F1–F12 e o botão PLAY disparam o slot correspondente; PLAY depois de PAUSE retoma o mesmo player. Um segundo disparo enquanto o cartucho toca é recusado. Cada player pode ser pausado ou parado individualmente, e há comando para parar todos. Players distintos podem reproduzir simultaneamente, conforme suporte do backend/dispositivo.

Cartuchos não chamam `stop_program`, não substituem a fonte PGM e têm seu próprio registro em `cartouche_history`. A consulta e exportação do histórico operacional incluem esses eventos como `cartouche_event`/`cartouche`, com slot, snapshot de material, operador, disparo, início, término e situação. Esse tipo de registro representa atividade local do player e nunca uma confirmação de transmissão.

## Roteamento de áudio

Cada cartucho tem um objeto `QAudioOutput`, mas nesta fase todos os players (PGM, CUE/pré-escuta, loops e cartuchos) usam o dispositivo selecionado nas preferências do Studio. O Qt/Windows pode combinar sinais concorrentes no mesmo dispositivo, mas o HitMo ainda não implementa barramentos PGM/CUE, roteamento independente, mixer, ganho por cartucho, mute por cartucho, crossfade ou medidor do sinal mixado. A cartucheira sinaliza essa limitação na interface; os objetos de saída separados não devem ser interpretados como canais físicos separados.

## Comandos do Playlist consultados

O `ManualPlaylist5.pdf` foi consultado na seção impressa “Operando o Playlist” (página 26) e nas opções de operador (página impressa 122). O manual descreve “passar para a próxima inserção” como interromper imediatamente o áudio atual e avançar para o próximo áudio, sujeito à permissão do operador. O comando PRÓXIMO do HitMo permanece coerente com esse avanço explícito.

Não foi encontrada no texto extraído do manual uma definição operacional inequívoca do botão “EXPULSAR”. O HitMo mantém o comportamento que já existia — descarregar a fonte PGM sem avançar a programação — como decisão própria do HitMo, sem atribuí-la ao Playlist.

## Validação prevista

Com Python 3.12.10 e as dependências de `requirements.txt`, executar na raiz do projeto:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q hitmo tests main.py
git diff --check
```

Os testes de WAV exercitam eventos do backend Qt em software. Eles não validam dispositivos físicos, mistura analógica/digital do sistema operacional, canal CUE dedicado, retorno ao transmissor ou transmissão real.
