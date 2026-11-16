# Fase 3A.3 — Rotação de vinhetas

## Referência Playlist Digital

O Manual Playlist 5 documenta a escolha aleatória de áudio associado a uma pasta em alguns fluxos do QuickStart e exemplos de seleção aleatória de vinhetas na grade. A seção **Rodízio**, porém, descreve comerciais associados ao mesmo código: programar o código permite ao Playlist escolher um dos comerciais associados. O manual consultado não especifica equilíbrio de frequência entre vinhetas, cooldown contra repetição ou persistência da rotação após reiniciar.

Referência: [Manual Playlist 5 (PDF oficial)](https://playlist.com.br/uploads/0/Manual%20Playlist%205%20PT.pdf), páginas impressas 31–32 e 72–75.

## Política própria do HitMo

O modo `jingle_rotation` é uma melhoria do HitMo, não uma regra atribuída ao Playlist:

- habilitação por pasta da categoria **Vinheta**;
- seleção entre materiais de áudio ativos daquela pasta;
- preferência por materiais com menor contador de seleção; empates usam o gerador aleatório injetável;
- cooldown configurável de 0 a 100 seleções recentes, aplicado quando existem alternativas;
- se o cooldown excluir todas as alternativas restantes, ele é relaxado para manter a programação selecionável;
- um material não pode ser selecionado duas vezes dentro da mesma ocorrência;
- contadores e IDs recentes ficam no SQLite, separados da tabela de histórico de execução;
- a ocorrência guarda pasta, modo e horário da seleção. Isso comprova planejamento/seleção, não que o áudio foi ao ar.

Pastas existentes mantêm o rodízio desativado por migração. Pastas novas de categoria **Vinheta** podem habilitá-lo no ConfigManager. O motor de áudio não participa desta fase.

## Limites

A rotação opera quando o serviço cria as ocorrências e snapshots dos itens. A política não inclui pesos editoriais por material, intervalo por tempo de relógio, nem comprovação de transmissão física. A execução efetiva continua dependendo do fluxo de reprodução e do histórico existente.
