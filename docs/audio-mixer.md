# Arquitetura de áudio PGM/CUE

## Caminho do sinal

O HitMo mantém os `QMediaPlayer` existentes para decodificar arquivos e controlar posição, pausa, término e repetição. Cada player envia buffers PCM estéreo float a 48 kHz por `QAudioBufferOutput`. Esses buffers entram em filas limitadas por fonte e seguem para o mixer de software.

- **PGM:** player principal, destino inicial de cartuchos e trilhas.
- **CUE:** pré-escuta; cartuchos e trilhas podem ser roteados pela API do motor.
- Com duas saídas selecionadas, PGM e CUE mantêm sinks físicos separados, como antes.
- Com uma saída selecionada para ambos, somente o sink PGM é aberto; seu callback soma as contribuições lógicas PGM e CUE em um PCM comum. O CUE continua com ganho, mute, roteamento, estado e medidor próprios. Isso evita dois `QAudioSink` concorrentes para o mesmo dispositivo.
- Em uma máquina que enumera uma única saída, o CUE é automaticamente associado ao PGM compartilhado, inclusive quando a preferência CUE antiga estiver vazia ou apontar para dispositivo ausente.
- A mudança de destino de uma fonte exige que ela esteja parada, evitando transferir áudio em execução entre as saídas.

## Ganho, mute e crossfade

O ganho linear de cada canal, o mute por canal, o ganho/mute dos barramentos e o Master são aplicados antes da saída. No modo compartilhado, PGM e CUE são somados antes de um único limitador de pico vinculado, com teto de 0,98 (cerca de 0,18 dB de margem digital). Os medidores por barramento representam a contribuição PCM efetiva após o limitador compartilhado. Isso protege contra sobrecarga numérica; não substitui controle de loudness, limitador de transmissão ou ajuste de ganho do console físico.

O crossfade aceita duas fontes no mesmo barramento e aplica curvas equal-power por amostra. Medidores são calculados sobre o PCM pós-ganho e pós-limitador que o callback de saída entrega ao `QAudioSink`; em mute, o medidor mostra o sinal efetivamente zerado.

## Dependências e compatibilidade

O caminho usa PySide6/Qt Multimedia e NumPy. O projeto requer Qt 6.8 ou superior porque `QAudioBufferOutput` foi introduzido no Qt 6.8 e, conforme a documentação do Qt, depende do backend FFmpeg. O pacote PySide6 do Windows deve incluir um backend Multimedia com FFmpeg. Dispositivos que não aceitam 48 kHz float usam o formato preferido informado pelo dispositivo; o mixer converte taxa, canais e representação antes da escrita.

Não é necessário instalar PortAudio, PyAudio, FFmpeg no sistema ou DLLs do Playlist para esse caminho. A saída separada depende de dois dispositivos de áudio enumerados pelo Windows. Dispositivos virtuais podem servir como endpoints independentes de software, mas seu uso não valida a conexão a uma mesa, a transmissão FM/streaming ou a operação em duas máquinas físicas.

## Limitações desta fase

- IDs de saída PGM/CUE são guardados nas preferências. Um ID ausente continua preservado; em configuração de saída única, o CUE usa a saída PGM compartilhada para continuar disponível.
- Roteamento PGM/CUE dos cartuchos e trilhas usa as colunas existentes em `cartouche_slots` e `loop_tracks`.
- Ganho e mute por canal, ganho e mute de barramento, Master e duração preferida do crossfade são salvos na chave `audio_mixer_state` do SQLite. A duração padrão é 3000 ms e aceita 100–15000 ms.
- Preferências malformadas ou fora dos limites são ignoradas campo a campo em memória; o banco não é regravado automaticamente durante a abertura. Alterações explícitas feitas pelo operador salvam o estado corrigido.
- Salvar e restaurar a mesa não retoma players comuns. `startup_play` continua sendo a preferência explícita preexistente para iniciar a programação; trilhas marcadas como habilitadas continuam seguindo a regra preexistente de restauração.
- No modo compartilhado, os sinais PGM e CUE chegam fisicamente misturados ao mesmo endpoint; não há isolamento acústico entre eles na placa de som. Para ouvir o CUE isoladamente, é necessário mutar o barramento PGM ou usar duas saídas independentes.
- A perda de saída pausa as fontes que estavam tocando somente naquele barramento. Reconectar o mesmo ID recria seu sink e retoma essas fontes; uma falha não recuperada exige intervenção do operador.
- A validação automatizada com WAV confirma decodificação, mixagem e buffers entregues ao sink. Não constitui teste auditivo nem validação de transmissão física.
- O limitador trabalha por bloco e é uma proteção de pico digital simples, não um processador de áudio de estúdio.

## Transporte

Esta fase não modifica os comandos de operação do HitMo. A documentação do Playlist consultada em fases anteriores confirma que PRÓXIMO encerra o material corrente e avança ao próximo item. A semântica precisa de EXPULSAR não ficou definida no manual; o comportamento existente do HitMo permanece uma decisão própria e continua distinto de PRÓXIMO.
