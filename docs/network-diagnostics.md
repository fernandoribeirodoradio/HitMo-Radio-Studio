# Diagnóstico de rede da Fase 4.1

## O que a infraestrutura já oferece

- O servidor central mantém o SQLite local e recusa iniciar em um endereço não local sem certificado e chave TLS.
- O cliente verifica certificados HTTPS e pode receber uma CA privada. HTTP agora é bloqueado pelo cliente quando o destino não é loopback, antes de enviar credenciais.
- Cada solicitação da API autentica novamente por HTTP Basic. Não existe token de sessão com expiração. Senha não é guardada pelo ConfigManager e não é registrada pelo servidor.
- Papéis `reader`, `editor` e `admin` têm permissões distintas. Revisões `If-Match` impedem que atualizações concorrentes sobrescrevam uma versão mais nova.
- Catálogo e programação são consultados pelo cliente remoto; a API não expõe controles do motor de áudio. O PGM local não depende de conexão remota para continuar a reprodução.
- O ConfigManager atualiza o catálogo pela ação **Atualizar catálogo**. O Studio observa as revisões e atualiza sua biblioteca sem descarregar a fonte atual.

## Diagnóstico pela estação remota

No Windows, dentro da pasta do projeto e com as dependências instaladas:

```powershell
python -m hitmo.network_diagnostics --server https://RADIO:8765 --username USUARIO --ca-file C:\certs\radio-ca.pem --media-id 123 --folder-id 4
```

O parâmetro `--ca-file` é opcional quando a cadeia do certificado é confiável no Windows. `--media-id` e `--folder-id` podem ser repetidos. A senha é solicitada sem eco e não é aceita em argumento de linha de comando. O diagnóstico verifica conexão/API, validação TLS, identidade, papel, permissões, revisões e leitura dos endpoints de programação e histórico.

Os IDs de material e pasta são consultados pelo servidor central, que testa o caminho como o mesmo computador/conta do Windows usados para reproduzir. Para áudio, o diagnóstico abre o arquivo e tenta ler um byte; para diretórios, verifica acesso à listagem. A resposta contém estado, ID, nome e categoria, sem devolver o caminho físico. Estados possíveis: acessível, caminho ausente, arquivo/diretório não encontrado, sem permissão, compartilhamento indisponível, falha temporária, inacessível, tipo de caminho incorreto e referência lógica sem arquivo. O teste confirma acesso naquele momento; não confirma que o formato será decodificado ou que o arquivo continuará disponível.

O diagnóstico de um caminho UNC pode depender do Windows e do servidor SMB e levar alguns segundos. Teste primeiro poucos IDs representativos, incluindo um arquivo e uma pasta do compartilhamento. Não use a verificação como autorização para apagar ou mover arquivos.

## Segurança, recuperação e limites

- HTTP sem TLS permanece permitido somente em loopback. Um destino remoto HTTP é bloqueado no cliente antes do envio de autenticação Basic.
- O endpoint `/api/v1/network-diagnostics` exige `catalog.read`. As rotas de consulta de arquivo também exigem autenticação e respondem apenas com estados sem caminho bruto.
- Logs de acesso registram IP, rota e nome autenticado em caso de autenticação recusada; não registram cabeçalho Authorization nem senha. O cliente não imprime senha.
- Após uma falha de rede, o operador pode usar **Atualizar catálogo** ou reconectar pelo aplicativo remoto. Edições concorrentes são recusadas com HTTP 409. Não há fila offline nem repetição automática de gravações.
- PGM roda no processo do Studio e não usa a API para manter áudio em execução. Os testes automatizados exercitam essa independência com reprodução WAV, mas não certificam a estação física.
- Os testes locais cobrem TLS confiável e não confiável com servidor de teste, autenticação, papéis, conflitos de revisão, estados de caminhos e atualizações enquanto o Studio está aberto. Ainda é necessária a validação com duas máquinas, certificado real, firewall e compartilhamento da emissora.

## Sequência de validação física

1. No PC da rádio, crie uma conta API com papel adequado e configure o serviço com certificado cujo nome corresponda ao endereço usado pelos clientes.
2. Instale a CA/cadeia confiável na estação remota; restrinja a regra do Firewall do Windows à rede privada da emissora.
3. A partir da estação remota, execute o comando acima sem `--media-id` para validar API, certificado, credenciais, permissões e revisões.
4. Cadastre um arquivo e uma pasta UNC representativos e execute novamente com seus IDs. Confirme que o resultado é acessível do servidor central.
5. Mantenha o PGM tocando durante atualização remota do catálogo e publicação de rascunho; confirme que o item atual continua e que a nova programação vale para ocorrências futuras.
6. Desconecte a estação remota, confirme que o PGM permanece tocando e reconecte. Atualize o catálogo, consulte histórico/ocorrências e confirme que não houve duplicação.
7. Repita com credencial inválida, conta somente leitura, certificado não confiável, compartilhamento sem permissão, compartilhamento indisponível e arquivo removido. Guarde a saída sem incluir credenciais ou caminhos sensíveis.

Até essa sequência ser executada em hardware da emissora, a conectividade entre computadores, implantação TLS, firewall e acesso físico aos compartilhamentos permanecem não validados.
