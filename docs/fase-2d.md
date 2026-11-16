# Fase 2D — Biblioteca, pastas e atalhos

## Implementado

- Categorias e pastas agora podem ser criadas, editadas, desativadas e restauradas pelo ConfigManager independente, usando o mesmo catálogo SQLite central.
- As escritas usam revisão do catálogo no `If-Match`; conflitos são recusados, nunca sobrescritos silenciosamente. Todas as operações registram usuário/ação no log de auditoria da API.
- Categorias têm nome, tipo de conteúdo (`audio`, `text`, `file`) e caminho de ícone. Pastas têm nome, diretório do servidor, categoria associada e ícone. A interface mostra quantidade de materiais e disponibilidade local conhecida.
- Identificadores de categoria, pasta e mídia são preservados. Categoria renomeada atualiza rótulos dos materiais atuais e eventos de timeline. Histórico e itens de ocorrências materializadas mantêm os snapshots originais.
- O tipo de uma categoria com materiais vinculados não pode ser trocado. Pastas/categorias são desativadas logicamente; a API não oferece exclusão física. Mudar o diretório cadastrado não move arquivos nem reescreve os caminhos de materiais.
- A reconciliação automática cria cartões `.hitmo.json` ausentes por `media.id`, atualiza apenas cartões que comprovam formato e identidade HitMo e mantém arquivos estranhos/antigos. O ícone de material tem precedência; se estiver vazio, usa o ícone da categoria.
- Ícones e áudio não são copiados. Para que outros clientes vejam um ícone, seu caminho precisa ser acessível também a eles.

## Compatibilidade com o Playlist

O importador de `Folders.xml` continua somente de leitura e preserva os IDs, códigos, títulos, destinos, caminhos/índices de ícone, argumentos de atalho, saída, quantidade e `DBFId` em snapshots. Nenhum vínculo é inferido entre `playlist_id` e IDs HitMo.

O manual descreve o Config Manager do Playlist como processo separado, autenticado por operador/senha e permissão administrativa. Suas propriedades incluem título, tipo, diretório, registro automático e ícone; a atualização é salva e o Playlist precisa ser reiniciado. Os parâmetros de pasta (`P`, `M`, `V`, `S`, `A`, `X`, `T`, `L`, `Temp`, `H`) podem determinar comportamentos operacionais. O HitMo preserva esses valores de origem, mas não os interpreta como execução, seleção aleatória/sequencial ou registro automático. A classificação de conteúdo HitMo (`audio`, `text`, `file`) é distinta desses parâmetros. Ver [compatibilidade detalhada](playlist-compatibility.md).

## Caminhos locais e rede

Diretórios locais são verificados pelo servidor. O serviço preserva literalmente caminhos UNC e não os testa de modo síncrono: uma consulta SMB a uma máquina desligada pode prender um worker HTTP. Para UNC, a interface mostra **teste físico pendente**. Isso não significa que a pasta esteja presente nem que o serviço tenha acesso a ela.

O teste entre duas máquinas, com TLS/certificado instalado, regras de firewall e compartilhamento acessível pelas credenciais do processo do serviço, continua obrigatório antes de declarar a rede pronta. Os testes automatizados usam loopback e banco temporário.

## Execução e testes

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 -m unittest discover -s tests -v
```

No computador da rádio, inicie o serviço em um terminal próprio:

```powershell
py -3.12 -m hitmo.server
```

Para uma estação remota, inicie o ConfigManager em outro terminal:

```powershell
py -3.12 -m hitmo.config_manager_app
```

O acesso remoto exige que o serviço esteja configurado para HTTPS/TLS e que o firewall permita a conexão. O cliente não recebe conexão direta ao SQLite.

## Pendências para fases seguintes

- Vínculo explícito e revisável entre cada referência legada de `Folders.xml` e uma entidade HitMo; importação não deve alterar os snapshots.
- Regras operacionais equivalentes aos parâmetros do Playlist, após modelagem por caso e testes que comprovem o efeito em programação.
- Ícones incorporados/sincronizados com identidade HitMo para clientes que não compartilham o mesmo caminho de arquivo.
- Teste em duas máquinas físicas e com compartilhamento real. O suporte de rede não está validado para uso no ar.
