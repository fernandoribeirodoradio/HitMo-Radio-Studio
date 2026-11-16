# Transição entre blocos programados

## Comportamento implementado

Ao chegar um horário de bloco com itens, o Studio materializa uma ocorrência e seus itens a partir da programação publicada. A ocorrência e os snapshots permanecem no SQLite. Se o PGM já estiver reproduzindo um material, o tick não chama `stop_program()` nem encerra esse material; a fila programada aguarda o fim natural ou o tratamento de falha já existente no motor.

Quando um novo bloco é materializado, itens ainda `planned` de ocorrências de bloco com horário anterior são marcados `skipped` e recebem a razão “Inserção excedente descartada no horário do próximo bloco”. Itens em `loading`, `playing` ou `paused` não são descartados por essa regra. As linhas e snapshots da ocorrência antiga permanecem para auditoria. Ocorrências do mesmo minuto não descartam umas às outras; a restrição única existente impede criar novamente a mesma ocorrência para o mesmo bloco e minuto.

Itens pendentes continuam persistidos durante reinicialização. A recuperação já existente marca apenas itens que estavam carregando ou em reprodução como interrompidos; itens seguintes permanecem planejados e podem ser retomados pela fila. O histórico anterior não é atualizado por esta transição.

## Referência operacional e limites

O ManualPlaylist5 documenta **Descartar Inserções** como uma opção configurável para blocos musicais e comerciais: ao ultrapassar o horário do bloco seguinte, o sistema espera o áudio atual terminar e então desconsidera as demais inserções. O manual também documenta a opção musical **Fixo**, que corta o áudio em execução com fade-out para passar ao próximo bloco comercial no horário exato. Esta implementação escolhe a política sem corte solicitada nesta fase; não implementa nem aciona **Fixo**.

Na instalação Playlist Digital local consultada (versão 5.0.6.16), `C:\Playlist\pgm\PLAYLIST.ini` está em formato `AUTO` para blocos comerciais e musicais; os arquivos `Grades\GRADE.txt` e `Mapas\MAPA.txt` fornecem horários e sequências. Os arquivos de grade/mapa examinados não contêm parâmetros `FIXO` ou `DESCARTE`; isso não permite concluir como as opções por operador estão configuradas dentro da interface. A inspeção foi somente de leitura.

Há uma diferença a considerar: no Playlist, descartar inserções é uma opção explícita por tipo de bloco/operador. O HitMo ainda não tem essa configuração e aplica o descarte de itens pendentes por padrão quando materializa o próximo bloco. A política exata por categoria e operador permanece uma decisão de produto, não inferida nesta entrega.

O manual também descreve opções independentes para blocos vazios, pausa e retomada automática. Essas opções não existem como configurações equivalentes no HitMo. Para esta fase, preserva-se o comportamento atual: bloco vazio é ignorado, não cria ocorrência nem interrompe o áudio. O HitMo também não determina a transição por duração estimada do bloco; o limite é o horário do próximo bloco materializado.

Testes com arquivos WAV e relógio controlado verificam o fluxo de software quando há saída de áudio disponível. Eles não validam continuidade de transmissão em hardware de emissora nem o sinal efetivamente transmitido.

## Referências do beSonus

Não foi encontrada instalação do beSonus no registro de aplicativos Windows nem nas pastas padrão de programas examinadas. A captura fornecida nesta conversa é da interface Playlist Digital. Para o beSonus, a comparação ficou limitada à descrição pública do fabricante: perfis por locutor, execução automática, arquivo de execução/auditoria e gerenciamento simultâneo de músicas, vinhetas, comerciais e escuta em até quatro placas de áudio. Esses recursos descrevem referência de produto; não foram verificados em execução nesta máquina e não definem o comportamento de transição do Playlist.
