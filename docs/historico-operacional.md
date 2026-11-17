# Histórico operacional

O HitMo mantém dois registros com funções diferentes:

- `program_occurrences` e `program_occurrence_items` guardam ocorrências criadas pela programação, itens selecionados e seus estados. Um item `planned` nessa tabela já foi materializado para a ocorrência; isso não significa que começou a tocar.
- `playback_history` guarda tentativas observadas no player. O registro nasce em `loading`; o início só é preenchido quando o Qt informa `PlayingState`. Término, parada, expulsão, interrupção e falha ficam no mesmo registro, com duração observada pelo player e motivo quando houver.

A consulta operacional combina as duas fontes e informa `record_type` (`player_event` ou `occurrence_item`), o estado do item, `selection_state` e, separadamente, o estado da ocorrência. Assim, um item `planned` com material e horário de seleção aparece também com seleção `selected`; a situação da ocorrência continua sendo um campo distinto. Itens com uma tentativa do player correspondente não são repetidos como uma segunda linha de ocorrência. Materiais apenas selecionados, descartados, ignorados ou ainda pendentes continuam visíveis como itens de ocorrência, com `selected_at` quando a seleção foi materializada.

IDs e rótulos históricos usam snapshots. `program_occurrences.block_name_snapshot`, `program_occurrence_items.media_id_snapshot`, `playback_history.material_id_snapshot` e os campos existentes de título, artista, caminho e categoria preservam a identidade e os metadados observados. A migração é aditiva; IDs antigos de itens e execuções ainda ligados a um cadastro são copiados para os campos snapshot. Para ocorrências legadas sem nome de bloco salvo, o nome histórico permanece vazio em vez de presumir que o nome atual era o nome antigo. O operador identificado no Studio é guardado na ocorrência no momento do despacho e na tentativa do player no início observado.

O HitMo oferece filtros de período, material (título, artista, caminho ou ID), categoria e situação, paginação com total de resultados e exportação CSV respeitando os mesmos filtros. A API `GET /api/v1/history` oferece os parâmetros `from`, `to`, `material`, `category`, `status`, `limit` e `offset`.

Este histórico comprova somente eventos registrados pelo processo HitMo e pelo player. `PlayingState` demonstra que o player iniciou a reprodução; não confirma que o áudio chegou à saída configurada, ao transmissor ou ao sinal efetivamente irradiado. Essa comprovação exigirá uma fonte de monitoramento independente ou integração futura com a saída da emissora.
