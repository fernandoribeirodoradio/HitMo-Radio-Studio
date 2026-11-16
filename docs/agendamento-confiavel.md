# Agendamento confiável — Fase 3A.5

## O que já existia

O Studio já criava ocorrências persistentes com snapshots dos itens, evitava ocorrências duplicadas por meio de índices únicos por bloco/evento e minuto, e mantinha uma fila serial para o canal PGM. A recuperação anterior também marcava itens ativos como interrompidos sem apagá-los.

## Política do HitMo

O serviço mantém em SQLite um marco de verificação do relógio. Ao iniciar ou ao detectar uma lacuna longa entre verificações, reconcilia os horários atravessados e grava a decisão em `scheduler_decisions`. No primeiro uso, o marco inicial cobre apenas a janela de tolerância atual; horários anteriores à primeira execução do HitMo não são rotulados como falha do software. O marco só avança depois que as decisões daquele intervalo foram persistidas; índices únicos de ocorrência e de auditoria permitem repetir a reconciliação sem duplicar registros.

- Atraso de até 30 segundos: blocos musicais/comerciais e eventos da timeline são registrados como `delayed` e entram na fila normal. Eles nunca substituem o áudio PGM ativo.
- Mais de 30 segundos: a ocorrência fica `missed`; seus itens ficam `ignored`, com hora e justificativa. Seleções por pasta e rodízios não avançam para eventos que não serão reproduzidos.
- Para um bloco `Fixo`, a tolerância de despacho tardio é de 1000 ms. Depois disso, a ocorrência é perdida e o áudio atual continua. O corte tardio da fase anterior foi restringido para que uma retomada após suspensão não corte PGM fora do horário.
- Itens pendentes de ocorrências antigas são expirados durante recuperação após reinício ou lacuna superior a 30 segundos. Um item PGM atualmente carregando, tocando ou pausado não é encerrado por essa reconciliação.
- Lacunas superiores a 31 dias são limitadas ao horizonte recente: o sistema não reconstrói nem reproduz programações mais antigas e registra a decisão `recovery_horizon`.
- Se o relógio retroceder, o marco não volta. O despacho fica suspenso até o relógio alcançar o último marco, com uma decisão `clock_rollback` registrada.

Em colisões no mesmo minuto, os blocos comerciais `Fixo` têm prioridade. Se houver mais de um, vence o menor ID de bloco; os demais itens simultâneos ficam `ignored`, com justificativa. Sem `Fixo`, todos os blocos são enfileirados por ID crescente, seguidos por eventos da timeline por ID crescente. A fila executa um item por vez no PGM.

## Relação com o Playlist Digital

O manual descreve os horários e as opções `Descartar Inserções` e `Fixo`, mas não define recuperação após suspensão do Windows, relógio retrocedido, janela de atraso, desempate de horários iguais ou limite de recuperação histórica. As janelas de 30 segundos, 1000 ms para `Fixo`, horizonte de 31 dias, desempates e estados `delayed`, `missed` e `ignored` são políticas próprias do HitMo para evitar reprodução atrasada sem controle. Elas não são atribuídas ao Playlist.

Referência funcional do manual: [páginas impressas 98–99, 141 e 143 (PDF oficial)](https://playlist.com.br/uploads/0/Manual%20Playlist%20Digital%205.pdf).

O limite de 31 dias reduz a criação massiva de ocorrências depois de uma indisponibilidade longa. O registro `recovery_horizon` documenta que eventos mais antigos não foram individualizados nem reproduzidos.

## Verificação

Os testes cobrem atraso dentro e fora da janela, reinício, lacuna longa, conflito com `Fixo`, ordem determinística, retrocesso do relógio, expiração idempotente e preservação do áudio PGM. Testes com WAV validam o fluxo do software e não comprovam transmissão física ou o sinal efetivamente transmitido.
