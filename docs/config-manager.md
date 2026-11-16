# HitMo ConfigManager — aplicativo independente

## Arquitetura

O ConfigManager é um ponto de entrada PySide6 separado: `python -m hitmo.config_manager_app`. Ele conecta-se ao serviço da Fase 2A por `HitMoAPIClient`; não abre SQLite, não importa o módulo do Studio e não exige que a janela de operação esteja aberta. O único catálogo continua sendo o SQLite do computador da rádio.

O serviço central atribui o ID permanente do cadastro. Inclusão chama `POST /api/v1/materials`; edição, inclusive troca de caminho, chama `PUT /api/v1/materials/{id}`; desativação e restauração usam as rotas existentes. Cada escrita consulta a revisão atual e envia `If-Match`. HTTP 409 é exibido para que o operador atualize e reaplique uma edição concorrente. Operações autenticadas ficam registradas em `api_audit_log` com usuário, data e ação.

## Iniciar no Windows

Instale Python 3.12, crie o ambiente e instale as dependências:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m hitmo.config_manager_app
```

Também é possível abrir `run_config_manager.bat`. O Studio não precisa estar aberto na estação ConfigManager.

Na primeira tela, informe o host ou IP do computador da rádio, a porta, se a conexão usa HTTPS, o certificado da CA (se for privada), usuário e senha. A senha só permanece em memória durante a sessão. Host, porta, preferência TLS, CA e usuário podem ser lembrados no perfil local do Windows. A tela mostra quem autenticou e o endereço do servidor.

No computador da rádio, o serviço deve estar ativo. Para acesso pela LAN, ele precisa ter sido iniciado com certificado TLS e a porta liberada apenas para a rede privada necessária; consulte [service-api.md](service-api.md). Use compartilhamento UNC acessível pelas credenciais do serviço para mídias em rede. Não compartilhe o SQLite.

## Catálogo e arquivos

A edição usa os campos existentes do catálogo: `media.id` (somente identidade), título, artista, categoria, caminho, duração, descrição, ícone e `text_content` (campo adicionado por migração). `material_kind` vem de `material_categories`; tipo não é inferido do nome ou extensão. A tabela mostra ID, título, artista, tipo, categoria, caminho físico, disponibilidade no servidor e estado. Pesquisa filtra nome, artista e caminho; filtros permitem categoria, pasta e estado. A Fase 2D permite criar, editar, desativar e restaurar categorias e pastas remotamente. Nomes de categoria são propagados aos cadastros atuais e eventos de timeline; o histórico e itens de ocorrências materializadas preservam seus snapshots. A troca de tipo de uma categoria usada por materiais é recusada. `modified`, `added_at`, `deactivated_at` e revisões permanecem dados do servidor, não são editáveis pelo operador.

A seleção de arquivo pode usar diretórios locais ou UNC visíveis pela estação. O caminho é enviado ao serviço; o arquivo não é copiado. Para caminhos locais, o serviço valida disponibilidade na máquina da rádio. Caminhos UNC são preservados literalmente e exibidos como teste físico pendente: consultar SMB síncronamente poderia manter uma resposta HTTP bloqueada quando o compartilhamento estivesse fora do ar. O teste de acesso ao UNC precisa ser realizado com a rede e as credenciais da emissora. Um caminho ausente é salvo e indicado, mantendo o ID para que possa ser corrigido depois. Ao editar caminho, a operação atualiza o registro existente. Um caminho já pertencente a outro registro gera conflito em vez de criar duplicata.

O modelo anterior exige `media.path` não nulo. Para materiais não sonoros sem arquivo, a migração aditiva cria `text_content`, e o ConfigManager usa uma referência lógica `hitmo://text/{token}` ou `hitmo://material/{token}`; isso não é caminho de arquivo e não é executável pelo motor de áudio. Textos existentes em arquivo podem continuar apontando ao arquivo original. O tipo de material é lido da categoria; não se convertem silenciosamente textos ou arquivos em áudio.

## Referências Playlist e atalhos HitMo

A aba **Referências Folders.xml** consulta o snapshot existente pela rota somente leitura `/api/v1/playlist-references`. Exibe códigos, destino, `icon_location`, argumentos, caminho do atalho, índice, `output`, contagem e `dbf_id` preservados. O ConfigManager não infere associação entre `playlist_id` e `media.id`, nem altera snapshots importados.

Em `%APPDATA%\HitMo Radio Studio\Atalhos`, o app reconcilia cartões JSON próprios `.hitmo.json`, identificados por `media.id`, e mostra as representações com ícone na aba **Atalhos HitMo**. Eles guardam referências ao caminho e aos metadados do material; não copiam áudio. Atalhos próprios ausentes são recriados. Arquivos existentes só são atualizados se tiverem o formato HitMo reconhecido e o mesmo ID; outros arquivos permanecem intocados. Cartões antigos sem registro correspondente não são removidos automaticamente. Duplo clique na representação abre a edição do cadastro.

O ícone do material é um caminho de imagem; ele é armazenado como metadado e exibido se estiver acessível na estação. A API não envia nem copia arquivos de ícone. Os atalhos usam o ícone próprio do material e, se vazio, recorrem ao ícone de categoria. Pastas e categorias também aceitam caminhos de ícones compartilhados; o arquivo precisa estar acessível às estações que devem exibi-lo.

## Integração com o Studio e limites

O Studio verifica `catalog_state.revision` periodicamente. Ao detectar uma revisão diferente, atualiza a lista e o filtro da biblioteca sem encerrar a reprodução nem apagar itens em execução, programação ou histórico. O ConfigManager também tem ação explícita **Atualizar catálogo** e tenta a reconexão quando acionada.

Limites desta entrega:

- Caminhos de pasta são metadados e não movem fisicamente nenhum arquivo. Diretórios locais são verificados no servidor; caminhos UNC são mantidos sem consulta de rede bloqueante e ficam marcados para teste físico. A alteração de caminho da pasta não reescreve os caminhos dos materiais nela vinculados.
- Os snapshots Playlist são somente leitura e o endpoint retorna a importação mais recente; associações semânticas com registros HitMo não são inferidas.
- A disponibilidade de caminhos locais é verificada no computador do serviço. UNC fica como teste pendente para não bloquear respostas HTTP; acesso ao compartilhamento e credenciais do serviço exigem validação física na rede.
- Não há teste em duas máquinas físicas neste ambiente. Os testes de API local não validam firewall, certificados implantados ou rede real.
- O cartão `.hitmo.json` é um atalho referencial próprio do HitMo, não um atalho Shell `.lnk` e não integra o menu Iniciar do Windows.

## Testes

Com Python 3.12 e dependências instaladas:

```powershell
python -m unittest discover -s tests -v
```

Os testes exercitam o cliente e serviço reais em loopback com banco temporário, incluindo uso simultâneo do Studio e ConfigManager sobre a mesma base SQLite. Não são prova de rede física entre máquinas.
