# Transição entre blocos programados

## Comportamento implementado

Ao chegar um horário de bloco com itens, o Studio materializa uma ocorrência e seus itens a partir da programação publicada. A ocorrência e os snapshots permanecem no SQLite. Se o PGM já estiver reproduzindo um material, o tick não chama `stop_program()` nem encerra esse material; a fila programada aguarda o fim natural ou o tratamento de falha já existente no motor.

Cada bloco publicado tem uma política de transição, copiada para a ocorrência no momento em que ela é criada. O padrão compatível com versões anteriores é `discard_insertions`: o item PGM em execução termina normalmente; itens que ainda estavam `planned` em blocos anteriores passam a `discarded`, com horário e motivo, sem apagar seus snapshots. Itens em `loading`, `playing` ou `paused` não são classificados como descartados. Ocorrências do mesmo minuto não descartam umas às outras.

Itens pendentes continuam persistidos durante reinicialização. A recuperação já existente marca apenas itens que estavam carregando ou em reprodução como interrompidos; itens seguintes permanecem planejados e podem ser retomados pela fila. O histórico anterior não é atualizado por esta transição.

## Referência operacional e limites

O ManualPlaylist5 documenta **Descartar Inserções** para blocos musicais e comerciais: o áudio atual termina e as inserções restantes que excederiam o próximo horário são desconsideradas. Também documenta **Fixo** como parâmetro de relógio para blocos comerciais: no horário marcado, o áudio em execução no bloco musical recebe fade-out e a programação passa ao próximo bloco comercial. O manual não informa a duração do fade.

Fonte: [Manual Playlist Digital 5, páginas impressas 98–99, 141 e 143 (PDF oficial)](https://playlist.com.br/uploads/0/Manual%20Playlist%20Digital%205.pdf).

No HitMo, a política é definida por bloco de destino. `Descartar inserções` mantém a execução do item atual e descarta os excedentes pendentes. `Fixo` só pode ser usado em bloco comercial e inicia uma redução do ganho PGM que termina na fronteira programada. A duração é uma configuração do HitMo, entre 0 e 5000 ms; 0 efetua corte imediato. Esse intervalo e o corte imediato são decisões próprias do HitMo, não regras atribuídas ao Playlist. O padrão para blocos antigos e clientes remotos que omitem a política continua `discard_insertions`.

O material interrompido por `Fixo` recebe `interrupted` no histórico de reprodução e na ocorrência, com motivo, horário e operador pelo histórico já existente. Inserções não iniciadas recebem `discarded`, fim e motivo na ocorrência; elas não são incluídas no histórico como se tivessem ido ao ar. A política e fade configurados também ficam em snapshot na ocorrência, sem reescrever ocorrências antigas.

Na instalação Playlist Digital local consultada (versão 5.0.6.16), `C:\Playlist\pgm\PLAYLIST.ini` está em formato `AUTO` para blocos comerciais e musicais; os arquivos `Grades\GRADE.txt` e `Mapas\MAPA.txt` fornecem horários e sequências. Os arquivos de grade/mapa examinados não contêm parâmetros `FIXO` ou `DESCARTE`; isso não permite concluir como as opções por operador estão configuradas dentro da interface. A inspeção foi somente de leitura.

O HitMo oferece a política por bloco, como solicitado nesta fase; não modela permissões de operador para alterar essa configuração durante a operação. A transição `Fixo` só inicia depois que o processo de automação está ativo e tem um item PGM para reduzir. Se o processo acordar depois do horário, registra a ocorrência e executa corte imediato como recuperação tardia, anotando o motivo.

O manual também descreve opções independentes para blocos vazios, pausa e retomada automática. Essas opções não existem como configurações equivalentes no HitMo. Para esta fase, preserva-se o comportamento atual: bloco vazio é ignorado, não cria ocorrência nem interrompe o áudio. O HitMo também não determina a transição por duração estimada do bloco; o limite é o horário do próximo bloco materializado.

Testes com arquivos WAV, relógio controlado e relógio de ganho verificam o fluxo de software. Eles não validam continuidade de transmissão em hardware de emissora nem o sinal efetivamente transmitido.

## Referências do beSonus

Não foi encontrada instalação do beSonus no registro de aplicativos Windows nem nas pastas padrão de programas examinadas. A captura fornecida nesta conversa é da interface Playlist Digital. Para o beSonus, a comparação ficou limitada à descrição pública do fabricante: perfis por locutor, execução automática, arquivo de execução/auditoria e gerenciamento simultâneo de músicas, vinhetas, comerciais e escuta em até quatro placas de áudio. Esses recursos descrevem referência de produto; não foram verificados em execução nesta máquina e não definem o comportamento de transição do Playlist.
