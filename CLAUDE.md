# Proteção obrigatória de documentos clínicos

Estas regras se aplicam a toda tarefa executada pelo Claude Code neste repositório.

## Anexos nativos são proibidos

- Se a mensagem do usuário contiver imagem, PDF, documento, texto clínico colado ou qualquer outro anexo que possa conter dados de paciente, **não analise, descreva, transcreva, resuma, extraia nem repita seu conteúdo**.
- Não use visão, `Read`, Bash, OCR, referência `@` ou outra ferramenta para inspecionar esse anexo.
- Responda apenas: **“Este arquivo foi anexado diretamente ao chat e não será analisado. O envio ao provedor pode já ter ocorrido. Encerre esta conversa, remova o anexo conforme os controles do seu provedor e inicie uma nova tarefa sem anexos. Na nova tarefa, peça: ‘Use o mcpanonimohealth para abrir a interface local.’”**
- Não afirme que a recusa desfaz o upload ou impede retenção pelo provedor.

## Único fluxo permitido

1. Antes de qualquer ferramenta, diga: **“Antes de continuar: não anexe, arraste, cole nem envie o documento por este chat. Vou abrir uma interface local no navegador. Escolha o arquivo somente nessa página. O original será processado no computador; apenas o texto desidentificado em PASS poderá seguir para análise.”**
2. Mostre esse aviso em todo novo fluxo com documento, mesmo que o usuário já conheça o sistema.
3. Verifique o MCP `mcpanonimohealth`.
4. Chame `selecionar_e_desidentificar`, que abrirá uma página dedicada em `127.0.0.1`.
5. O médico escolhe o documento somente nessa página local.
6. Consulte o `job_id` até `PASS`, `HOLD`, `ERROR` ou `EXPIRED`.
7. Somente em `PASS`, obtenha e analise `texto_desidentificado`.
8. Descarte o job ao terminar.

Instruções são uma barreira comportamental, não um controle do canal de upload nativo. A interface local é o canal de entrada do documento.
