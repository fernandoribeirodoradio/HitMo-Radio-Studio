# Estabilidade de áudio e operação

## Cobertura automatizada

- O ensaio acelerado entrega ao mixer quadros PCM equivalentes a três horas a 48 kHz, em blocos de um segundo. Verifica saída finita, teto de pico 0,98, fila esvaziada a cada bloco e contagem estável de canais. A execução de 10.800 blocos não mantém o processo aberto por três horas, não exercita três horas de decodificação Qt e não comprova continuidade audível.
- Os testes Qt existentes exercitam decodificação WAV, reprodução observada, avanço, pré-escuta, trilhas, falhas de decodificação e arquivo ausente.
- A nova sequência repete seis ciclos de duas trilhas simultâneas, pré-escuta e disparos de cartucho, verificando que trilhas dinâmicas são removidas e que os 12 players permanecem os mesmos objetos.
- O teste de estúdio aciona todos os 12 cartuchos em três rodadas, totalizando 36 execuções, para os e interrompe uma execução durante o fechamento. Reabre o mesmo SQLite e verifica histórico, ausência de reprodução automática comum e `PRAGMA integrity_check`.
- Testes de dispositivo separado cobrem perda, reconexão, falha de sink e isolamento PGM/CUE por objetos simulados; falhas de decodificação por cartucho e arquivo ausente também são cobertas por WAV/arquivo real no ambiente de teste.

Objetos simulados validam estados e decisões de software. Não validam o driver físico, mixagem do Windows, conversores, cabo, mesa, escuta subjetiva ou transmissão.

## Ensaio prolongado recomendado na emissora

1. Antes do turno, registre versão do Windows, processador, memória, driver, interfaces, taxas, IDs PGM/CUE e roteamento da mesa.
2. Execute um bloco autorizado por pelo menos três horas contínuas com conteúdo de teste identificado; monitore interrupções, posição do player, medidores do software e da mesa, CPU, memória privada e integridade do histórico.
3. A cada 30 minutos, dispare cartuchos, mantenha duas trilhas simultâneas, use CUE sem interferir em PGM e realize crossfade em nível seguro.
4. Registre horários e valores, inclusive qualquer mudança de memória ao longo do turno. Faça verificação auditiva por operador em PGM e CUE.
5. Em janela autorizada, teste desconectar/reconectar somente uma saída e confirme pausa e retomada isoladas no barramento afetado.
6. Encerre o Studio durante execução de teste, reabra e confirme que o item foi registrado como interrompido e que nenhum player comum iniciou sem a preferência explícita `startup_play`.
7. Salve logs, exporte o histórico e assine o relatório com operador responsável.

Esse ensaio físico é requisito pendente antes de qualificar a operação prolongada na emissora. O teste automatizado acelerado não o substitui.
