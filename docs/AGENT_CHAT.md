# PORTAI Agent — chat local

1. Inicie `python portai.py` no Termux ou Linux e escolha PORTAI AGENT. O PortAI inicia o servidor apenas em `127.0.0.1` e abre o navegador; se a abertura automática falhar, use o endereço impresso no terminal.
2. Comece a conversa sem projeto ou use **Projeto** para selecionar um projeto recente ou informar o caminho absoluto. O agente cria/reutiliza uma workspace isolada para alterações.
3. Escreva livremente e envie com Enter. Shift+Enter insere uma nova linha. As quatro sugestões preenchem o campo; revise e envie quando estiver pronto.
4. A resposta e a atividade Codex chegam na conversa por streaming. O campo permanece disponível durante tarefas; mensagens adicionais ficam na fila da conversa.
5. Use **Interromper** para parar a tarefa atual e cancelar a fila pendente. Se o Codex salvou uma sessão retomável, **Retomar** reaparece na conversa.
6. O botão ↻ verifica novamente a instalação e faz uma solicitação real de conectividade. O status mostra versão, runtime e caminho; autenticação é apresentada apenas como estado, nunca como segredo.
7. Conversas e estado são persistidos sob `~/.local/share/offtec-portai/` ou `PORTAI_AGENT_HOME`. Relatórios continuam em `reports/`; backups ficam na pasta `backups/` do armazenamento do agente.
8. **Sair do agente** encerra o servidor local e solicita interrupção das tarefas; também é possível encerrar com Ctrl+C no terminal.

O chat é local e usa a instalação existente do Codex CLI. No Android, conceda acesso ao armazenamento no Termux para usar caminhos externos. O seletor web não consegue abrir diretamente um diálogo nativo de pastas; use os projetos recentes ou o caminho absoluto.
