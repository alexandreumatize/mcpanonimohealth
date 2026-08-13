# Política de segurança

## Aviso essencial

O mcpanonimohealth 0.1 é uma demonstração didática, não validada para dados reais, assistência clínica ou conformidade regulatória. Detecção automática pode falhar. Não trate `PASS` como certificação de anonimização.

## Relatar uma vulnerabilidade

Use **GitHub → Security → Report a vulnerability** para abrir um relato privado. Não abra issue pública para falhas que possam expor dados, permitir leitura arbitrária de arquivos, burlar `HOLD` ou vazar conteúdo em logs.

Inclua versão, sistema operacional, passos mínimos e um caso inteiramente sintético. Nunca envie documento de paciente, segredo, credencial, caminho de arquivo pessoal ou captura contendo dados reais.

## Modelo de ameaças do MVP

Proteções pretendidas:

- ferramentas MCP não recebem caminho ou nome bruto como argumento;
- o seletor de arquivos é local e acionado sem devolver o caminho ao agente;
- somente texto de job em `PASS` pode ser recuperado;
- `HOLD`, `ERROR` e `EXPIRED` não devolvem conteúdo clínico;
- logs devem conter somente metadados operacionais não identificáveis;
- documentos são tratados como dados não confiáveis, inclusive contra prompt injection;
- derivados temporários expiram e podem ser descartados explicitamente.

Fora da fronteira de proteção:

- um agente com acesso amplo ao mesmo usuário do sistema operacional;
- comprometimento do computador, do provedor de IA ou da cadeia de dependências;
- reidentificação por contexto clínico raro ou combinação com outras bases;
- erro do OCR, identificador manuscrito, texto embutido ou formato não suportado;
- uso fora das regras institucionais, LGPD, ética médica ou contrato do provedor.

O processamento local reduz exposição, mas não substitui controle de acesso, criptografia do dispositivo, atualizações, backups seguros, política institucional, avaliação de risco e revisão humana.

## Dados em relatórios e testes

- Use somente identidades e documentos sintéticos.
- Não copie logs de produção sem inspecioná-los localmente.
- Remova nomes de usuário, caminhos, IDs de máquina e metadados antes de enviar.
- Se houver suspeita de exposição real, interrompa o uso, preserve somente evidências não sensíveis e siga o plano de resposta a incidentes do controlador/serviço de saúde.
