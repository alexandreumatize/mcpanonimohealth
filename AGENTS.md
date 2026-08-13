# Proteção obrigatória de documentos clínicos

Estas regras se aplicam a toda tarefa executada pelo Codex neste repositório.

## Anexos nativos são proibidos

- Se a mensagem do usuário contiver imagem, PDF, documento, texto clínico colado ou qualquer outro anexo que possa conter dados de paciente, **não analise, descreva, transcreva, resuma, extraia nem repita seu conteúdo**.
- Não use visão, leitura de arquivos, terminal, OCR ou outra ferramenta para inspecionar esse anexo.
- Responda apenas: **“Este arquivo foi anexado diretamente ao chat e não será analisado. O envio ao provedor pode já ter ocorrido. Encerre esta conversa, remova o anexo conforme os controles do seu provedor e inicie uma nova tarefa sem anexos. Na nova tarefa, peça: ‘Use o mcpanonimohealth para abrir a interface local.’”**
- Não afirme que a recusa desfaz o upload ou impede retenção pelo provedor.

## Único fluxo permitido

1. Verifique o MCP `mcpanonimohealth`.
2. Chame `selecionar_e_desidentificar`, que abrirá uma página dedicada em `127.0.0.1`.
3. O médico escolhe o documento somente nessa página local.
4. Consulte o `job_id` até `PASS`, `HOLD`, `ERROR` ou `EXPIRED`.
5. Somente em `PASS`, obtenha e analise `texto_desidentificado`.
6. Descarte o job ao terminar.

Instruções são uma barreira comportamental, não um controle do canal de upload nativo. A interface local é o canal de entrada do documento.
