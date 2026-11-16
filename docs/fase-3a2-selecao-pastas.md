# Fase 3A.2 — Seleção musical e rotação por pasta

## Comportamento implementado

- Um item de programação pode apontar para um material fixo ou para uma pasta elegível.
- Seleção sequencial percorre materiais ativos por título, caminho e ID, em ordem estável.
- Seleção aleatória usa um baralho embaralhado persistido no SQLite; não repete até percorrer os candidatos do ciclo.
- A posição do baralho, o ciclo e o último ID ficam persistidos por pasta e modo, inclusive após reiniciar o serviço.
- Itens escolhidos são copiados para o snapshot imutável da ocorrência, com ID, caminho, categoria, pasta de origem, modo e `selected_at`.
- A escolha é registrada como material selecionado/programado. Só a conclusão do player atualiza o estado de execução; a seleção, por si só, não comprova que foi ao ar.
- A mesma ocorrência evita selecionar novamente um ID já fixado ou escolhido nela. Se não houver outro candidato, o seletor fica registrado como falho, em vez de duplicar o áudio.
- Se a lista da pasta mudar, o baralho é reconstruído com os membros atuais. Uma pasta vazia ou sem áudio compatível produz um item falho auditável.

## Referência Playlist Digital

O Manual Playlist 5 descreve a montagem da grade por uma sequência ordenada de códigos de pastas. A documentação explica que cada código musical seleciona uma música aleatória da pasta; também documenta “Rodízio” de comerciais pela associação de arquivos a um mesmo código. Ela não especifica uma política de seleção sequencial, um algoritmo de aleatoriedade sem repetição ou a persistência de rotação após reinício. Por isso, o baralho persistente e a regra de não repetição do HitMo são decisões próprias documentadas aqui, e não afirmações sobre a lógica interna do Playlist.

Referências: Manual Playlist 5, pp. 72–75 (rodízio e programação por pastas/grade). A implementação não importa código ou executáveis proprietários.

## Limites desta entrega

- A rotação vale por pasta e modo; seletores de mesma pasta, mas com modos diferentes, mantêm cursores separados.
- Se só houver um candidato válido, ele pode voltar a ser selecionado em uma ocorrência posterior. O HitMo não consegue evitar repetição sem outro material elegível.
- A configuração de descarte de inserções por bloco ou operador continua pendente e não foi alterada nesta fase.
- Não foi validada transmissão física nem integração em duas máquinas.
