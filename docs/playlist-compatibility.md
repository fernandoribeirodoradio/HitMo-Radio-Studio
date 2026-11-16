# Compatibilidade de catálogo com Playlist Digital

## Escopo implementado

O HitMo pode ler `Folders.xml` pelo menu **Cadastro > Ler catálogo do Playlist (Folders.xml)…**. Cada importação cria um snapshot no SQLite HitMo. O importador mantém campos originais e a ordem das entradas, registra raízes compartilhadas e marca se cada `Target` existe neste computador.

O processo é somente de leitura em relação ao Playlist: não altera `Folders.xml`, não escreve em `C:\Playlist`, não segue atalhos, não abre DLLs e não varre ou cadastra arquivos de mídia. O catálogo importado fica separado das tabelas operacionais de biblioteca, programação e histórico. Reimportar cria outro snapshot; versões anteriores não são sobrescritas.

## Mapeamento dos campos observados

| Playlist `Folders.xml` | Tabela SQLite do HitMo | Observação |
|---|---|---|
| `Version`, `Folders` | `playlist_catalogs.version`, `declared_folder_count` | Metadados declarados no XML |
| `Shared/Folders` | `playlist_catalogs.declared_shared_count` | Quantidade declarada |
| `Shared/Server`, `Shared/FolderN/Name`, `Path` | `playlist_shared_roots.server`, `title`, `path` | Raízes compartilhadas; preservadas como texto |
| `FolderN/ID`, `Title`, `Type` | `playlist_folder_entries.playlist_id`, `title`, `type_code` | IDs e códigos de origem preservados sem tradução destrutiva |
| `Target` | `target`, `target_exists` | Caminho textual original e indicador de existência local; não é varrido |
| `IconLocation`, `IconIndex` | `icon_location`, `icon_index` | Referências preservadas; DLL não é carregada pelo HitMo |
| `ShortcutArguments`, `ShortcutPathName` | campos homônimos | Preservados como referências; `.lnk` não é aberto |
| `Output`, `TotalFiles`, `DBFId` | campos homônimos | Valores originais preservados |
| ordem `FolderN` | `position` | Mantida dentro do snapshot |

No XML observado, `M` acompanha `Músicas`, `$` `Comerciais`, `H` `Hora Certa`, `L` `Locuções`, `P` `Pausa`, `V` `SH`/`Vinhetas`, `X` `Textos` e `T` `Trilhas`. O HitMo persiste os códigos brutos; ainda não lhes atribui regras de execução.

`Pausa` demonstra por que não se deve varrer todos os `Target`: nesse registro, o alvo é `pgm\Atalhos`, e o tipo é `P`. Os atalhos `.lnk` e os ícones referenciados por DLL são coisas distintas. A documentação do Playlist também instrui salvar as alterações de pastas e reiniciar o Playlist para que ele as reconheça.

## Limites e próximo trabalho

O importador não escreve de volta em `Folders.xml` nem associa automaticamente as referências antigas a pastas do catálogo HitMo. O ConfigManager independente da Fase 2B gerencia o catálogo central pela API. Não foi realizada análise reversa das DLLs ou do formato binário `PLAYLIST.RCV`.

## Confirmação no manual de 198 páginas

O manual fornecido (`ManualPlaylist5.pdf`, revisão que apresenta o Playlist Digital 5.0.6.16) foi consultado na seção impressa **Configurando o Playlist Digital**, páginas 18–22, no painel **Pastas**, páginas 42–44, e no apêndice **Config Manager**, páginas 188–193 (a paginação indicada no sumário difere do rodapé dessas páginas). Ele descreve:

- Pastas e subpastas de trabalho para organizar os arquivos, identificadas no Playlist por atalhos dentro de `Playlist\Pgm\Atalhos`.
- Tipos/parâmetros que alteram o tratamento operacional: `P` pausa, `M` música, `V` força rodízio de vinhetas, `S` seleção sequencial, `A` seleção aleatória, `X` arquivos de texto TXT/RTF, `T` trilha em loop, `L` locução, `Temp` temperatura e `H` hora certa. O próprio manual associa pausa e trilha a comportamentos durante o bloco; esses códigos não são meras categorias visuais.
- Config Manager separado, com autenticação de operador/senha e permissão administrativa, para criar atalhos e editar os existentes. O fluxo descrito seleciona tipo, diretório e depois permite editar título, tipo, diretório, opção de registrar na programação e ícone.
- Ícones selecionados da biblioteca própria do Playlist; salvar todas as alterações e reiniciar o Playlist para reconhecer as mudanças. Para uso em rede, a pasta compartilhada requer permissão de leitura/gravação.
- O painel Pastas serve para montagem manual da programação: duplo clique ou arraste de item para o bloco. Arrastar a pasta inteira permite seleção automática conforme o parâmetro configurado.

O HitMo não carrega DLLs nem modifica `Folders.xml`. A Fase 2D gerencia o catálogo próprio central e seus ícones/metadados, preserva integralmente os códigos e campos originais do snapshot e não converte `V`, `S`, `A`, `P`, `T`, `L`, `Temp` ou `H` em ações operacionais até que cada regra tenha implementação e testes próprios. O tipo de categoria HitMo (`audio`, `text`, `file`) descreve o conteúdo cadastrado e não substitui os parâmetros de pasta do Playlist.
