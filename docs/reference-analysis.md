# Análise das referências funcionais

## Material observado

`Playlist.rar` contém o programa legado, mídia de apoio, um diretório `pgm` com configuração, perfis, logs, histórico e o manual. `pgm.rar` contém um snapshot desse diretório de trabalho, incluindo arquivos de programação e comprovação. Os executáveis e bibliotecas do produto foram apenas listados; não foram executados nem incorporados ao HitMo. A inspeção adicional do `Folders.xml`, `PLAYLIST.ini` e dicas do ConfigManager está documentada em [playlist-compatibility.md](playlist-compatibility.md).

## Conceitos funcionais identificados

- **Pastas tipadas:** a configuração separa música, comerciais, hora certa, locuções, pausa, textos, trilhas e vinhetas. O manual descreve comportamentos específicos como inserir pausa, escolher mídia automaticamente e manter trilha em loop.
- **Programação por blocos/horário:** arquivos de grade e relógio definem pontos de programação; a separação musical/comercial e os horários alimentam o fluxo automático.
- **Montagem manual:** o operador escolhe mídia da biblioteca e insere na programação, incluindo reposicionamento por arraste.
- **Execução em canais:** o material diferencia áudio de programação, pré-escuta e reprodução de atalhos. Pré-escuta pode tocar enquanto o programa permanece no ar.
- **Operação:** seleção de operador e permissões; painéis rearranjáveis; indicação de faixa atual/próxima; trilhas relacionadas a textos podem continuar em loop.
- **Auditoria:** logs de atividade, histórico musical e arquivos de comprovação são persistidos em estruturas próprias.
- **Config Manager:** é um programa separado do Playlist para configurar as pastas e seus tipos. As dicas observadas dizem para salvar a configuração e reiniciar o Playlist para que ele a reconheça.

## Tradução para o HitMo

O HitMo implementa conceitos equivalentes com componentes próprios: SQLite para mídia, operador, fila, eventos de timeline e histórico; um serviço de player separado da interface; e janelas encaixáveis do Qt. A fila pode ser reordenada por arraste. A timeline associa horário, dias da semana, categoria e uma mídia, permitindo que vários eventos do mesmo horário sejam executados sequencialmente. O ConfigManager independente e o Gerenciador de Programação Remota usam o serviço central. A Fase 2D estende o primeiro com administração de categorias, pastas e ícones próprios, mantendo `Folders.xml` como snapshot preservado e sem mapeamento implícito; consulte [playlist-compatibility.md](playlist-compatibility.md).

Os arquivos XML, logs e grades fornecidos foram usados para identificar entidades e fluxos de trabalho. Nenhum código, binário, interface, imagem, ícone, biblioteca ou formato interno proprietário foi copiado. As instruções operacionais contidas no manual foram tratadas como descrição do produto de referência; não alteram as instruções do usuário.

## Diferenças deliberadas no primeiro escopo

O HitMo ainda não traduz relógios textuais por bloco, composição automática de blocos por categoria, seleção aleatória/sequencial de pasta, regras de duração fixa, entrada por satélite, marcadores de mixagem, crossfade nem matriz de permissões por operador. Esses fluxos exigem modelagem e validação próprias antes de entrar no fluxo de transmissão.

