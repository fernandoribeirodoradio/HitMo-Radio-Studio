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

O importador não escreve de volta em `Folders.xml` e ainda não alimenta a biblioteca ativa nem a programação. A janela atual de gerenciamento de materiais do HitMo continua dentro do Studio; transformá-la em um Config Manager independente requer uma etapa própria, com política de autenticação e contrato compartilhado definidos. Não foi realizada análise reversa das DLLs ou do formato binário `PLAYLIST.RCV`.
