# Validação de dispositivos de áudio

## Comportamento coberto por software

PGM e CUE mantêm sinks e destinos independentes. Se um dispositivo selecionado desaparecer, o HitMo marca somente o barramento correspondente como indisponível e pausa as fontes que estavam tocando nele. Ao detectar novamente o mesmo dispositivo, recria o sink e retoma apenas essas fontes. Uma seleção PGM explicitamente salva que não esteja disponível não é substituída silenciosamente pelo dispositivo padrão. O CUE pode permanecer desativado quando não há um segundo endpoint.

O callback de cada `QAudioSink` continua alimentando o mixer em PCM; os medidores seguem medindo as amostras entregues a cada sink depois do ganho e limitador. A validação automatizada de sinks e dispositivos usa objetos simulados e não demonstra saída audível, roteamento da placa, sinal de mesa ou transmissão. Os testes de WAV existentes exercitam a decodificação e buffers Qt no ambiente de execução disponível; isso também não substitui escuta e validação de hardware na emissora.

O Master continua iniciando em aproximadamente −6 dB e o limitador de pico mantém teto de 0,98, conforme [audio-mixer.md](audio-mixer.md). É uma proteção digital simples, não um limitador de transmissão. Falha ou remoção de um cartucho segue isolada ao player e canal daquele cartucho.

## Roteiro de validação física na emissora

1. Conecte a saída PGM à entrada designada da mesa e selecione-a em Preferências.
2. Conecte uma segunda saída física à entrada de monitoração CUE e selecione um endpoint diferente para CUE. Confirme que selecionar o mesmo endpoint para ambos é recusado.
3. Com uma música no PGM, faça pré-escuta de outro arquivo no CUE. Confirme auditivamente na mesa que o CUE não aparece no canal PGM e que a música PGM não é interrompida.
4. Dispare cartuchos roteados para PGM e para CUE, confirmando cada destino no canal físico esperado.
5. Inicie duas trilhas simultâneas e confirme que ambas chegam ao barramento configurado.
6. Execute crossfade entre duas fontes do mesmo barramento e confirme a transição auditivamente.
7. Teste ganho individual, mute de canal, ganho/mute de barramento e Master em níveis baixos; confirme o efeito real em cada entrada da mesa.
8. Observe os medidores do HitMo e os medidores da mesa. Aumente fontes gradualmente e confirme que o limitador não produz clipping digital; verifique também distorção na cadeia analógica e nos conversores da mesa.
9. Durante reprodução, desconecte e reconecte uma saída em janela de manutenção. Confirme a indicação de falha, a pausa apenas do barramento afetado e a recuperação após reconexão.
10. Registre sistema operacional, interfaces, drivers, taxas de amostragem, roteamento, níveis, resultados auditivos e qualquer interrupção.

Este roteiro ainda precisa ser executado e assinado por um operador com acesso à mesa e aos dispositivos da emissora. Nenhum teste automatizado equivale à validação física ou à comprovação de transmissão.
