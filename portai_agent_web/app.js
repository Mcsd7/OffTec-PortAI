(() => {
  const $ = id => document.getElementById(id);
  const state = { conversations: [], projects: [], conversation: null, events: null, busy: false };
  const token = window.PORTAI_CSRF;
  const welcome = 'Olá! Sou o PortAI, seu agente inteligente de desenvolvimento e portabilidade.\n\nPosso analisar projetos, identificar incompatibilidades, adaptar código, preparar ambientes e executar compilações.\n\nO que vamos construir hoje?';
  const suggestions = [['Portar um jogo', 'Quero portar meu jogo para outra plataforma. Analise o projeto e proponha um plano seguro.'], ['Analisar um projeto', 'Analise este projeto e explique sua estrutura, tecnologias e ferramentas de build.'], ['Corrigir incompatibilidades', 'Analise e corrija as incompatibilidades encontradas neste projeto.'], ['Preparar projeto para Android', 'Prepare este projeto para Android ARM64 e informe o que pode ser compilado neste ambiente.']];
  async function request(path, method = 'GET', body) {
    const headers = {}; if (method === 'POST') headers['X-PortAI-Token'] = token;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {method, headers, body: body === undefined ? undefined : JSON.stringify(body)});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`); return data;
  }
  function toast(text) { const el = $('toast'); el.textContent = text; el.classList.add('show'); setTimeout(() => el.classList.remove('show'), 3200); }
  function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function markdown(text) {
    const chunks = String(text || '').split(/```([\w+-]*)\n?([\s\S]*?)```/g); let html = '';
    for (let i = 0; i < chunks.length; i += 3) {
      let part = esc(chunks[i] || '').replace(/`([^`]+)`/g, '<code>$1</code>');
      part = part.split(/\n\n+/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join(''); html += part;
      if (chunks[i + 2] !== undefined) html += `<pre><code>${esc(chunks[i + 2])}</code></pre>`;
    }
    return html;
  }
  function title(value) { return String(value || 'Nova conversa').replace(/[\r\n]/g, ' ').slice(0, 70); }
  function drawSidebar() {
    $('conversationList').innerHTML = state.conversations.map(c => `<button class="conversation-item ${state.conversation?.id === c.id ? 'active' : ''}" data-id="${esc(c.id)}"><span>◌</span><span>${esc(title(c.title))}</span></button>`).join('');
    $('conversationList').querySelectorAll('[data-id]').forEach(button => button.onclick = () => openConversation(button.dataset.id));
  }
  function drawWelcome() {
    $('messages').innerHTML = `<div class="welcome"><div class="welcome-logo">P</div><h1>Olá, eu sou o PortAI</h1><p>${esc(welcome)}</p><div class="suggestions">${suggestions.map(([label, prompt], i) => `<button class="suggestion" data-suggestion="${i}"><b>${esc(label)}</b>${esc(prompt)}</button>`).join('')}</div></div>`;
    $('messages').querySelectorAll('[data-suggestion]').forEach(button => button.onclick = () => { $('composer').value = suggestions[Number(button.dataset.suggestion)][1]; resizeComposer(); $('composer').focus(); });
  }
  function drawMessages(messages = []) {
    if (!messages.length) { drawWelcome(); return; }
    $('messages').innerHTML = messages.map(m => `<article class="message ${m.role === 'user' ? 'user' : 'assistant'}" data-message="${esc(m.id)}"><div class="avatar">${m.role === 'user' ? 'U' : 'P'}</div><div><div class="bubble">${markdown(m.content || (m.status === 'streaming' ? 'Codex está processando…' : ''))}</div><div class="message-meta">${m.role === 'user' ? 'Você' : 'PortAI'} · ${esc(m.status || '')}</div></div></article>`).join('');
    $('chat').scrollTop = $('chat').scrollHeight;
  }
  function updateMessage(id, content, status) {
    const item = $('messages').querySelector(`[data-message="${CSS.escape(id)}"]`);
    if (!item) { if (state.conversation) loadConversation(); return; }
    item.querySelector('.bubble').innerHTML = markdown(content);
    if (status) item.querySelector('.message-meta').textContent = `PortAI · ${status}`;
    $('chat').scrollTop = $('chat').scrollHeight;
  }
  function setRuntime(runtime, connection) {
    const ready = Boolean(runtime?.path && runtime?.version);
    const ok = connection?.ok ?? runtime?.connection === 'passed';
    const pill = $('connection'); const pulse = pill.querySelector('.pulse'); pulse.className = `pulse ${ok ? 'good' : ready ? '' : 'bad'}`;
    pill.lastChild.textContent = ready ? `${ok ? 'Conectado' : 'Codex instalado'} · ${runtime.version}` : 'Codex indisponível';
    $('sidePulse').className = `pulse ${ok ? 'good' : ready ? '' : 'bad'}`;
    $('sideStatus').textContent = ready ? `${runtime.path}${runtime.runtime ? ` · ${runtime.runtime}` : ''}${ok ? ' · conectado' : ' · verifique a conexão'}` : (runtime?.error || 'Codex não encontrado');
    $('connection').title = `Executável: ${runtime?.path || 'não detectado'}\nRuntime: ${runtime?.runtime || 'não detectado'}\nAutenticação: ${runtime?.authenticated ? 'detectada' : 'não confirmada'}`;
  }
  function setProject(path) { $('projectLabel').textContent = path ? path.split('/').filter(Boolean).pop() || path : 'Selecionar projeto'; $('projectButton').title = path || 'Selecionar projeto'; }
  function setStatus(status, stage) {
    state.busy = ['running', 'queued', 'stopping'].includes(status);
    $('taskStrip').classList.toggle('hidden', !state.busy);
    $('taskStage').textContent = status === 'queued' ? 'Mensagem na fila' : status === 'stopping' ? 'Interrompendo tarefa…' : stage || 'Codex está trabalhando';
    $('taskDetail').textContent = state.busy ? 'O campo de conversa continua disponível' : '';
    const resumable = !state.busy && state.conversation?.last_task?.status === 'interrupted' && state.conversation?.last_task?.session_id;
    $('resumeTask').classList.toggle('hidden', !resumable);
    $('taskSpinner').classList.toggle('hidden', !state.busy);
    if (resumable) $('taskStrip').classList.remove('hidden');
  }
  async function loadConversation() {
    if (!state.conversation) return;
    const data = await request(`/api/conversations/${state.conversation.id}`); state.conversation = data.conversation;
    drawMessages(state.conversation.messages); setProject(state.conversation.project_path); setStatus(state.conversation.status, state.conversation.active_task?.stage);
    drawSidebar(); localStorage.setItem('portai-conversation', state.conversation.id);
  }
  function listenEvents() {
    if (state.events) state.events.close(); if (!state.conversation) return;
    state.events = new EventSource(`/api/conversations/${state.conversation.id}/events?after=${state.conversation.event_cursor || 0}`);
    state.events.addEventListener('update', event => {
      const item = JSON.parse(event.data); const d = item.data || {};
      if (item.type === 'assistant_start') { const messages = state.conversation.messages || []; messages.push(d); state.conversation.messages = messages; drawMessages(messages); }
      if (item.type === 'assistant_delta') {
        const node = $('messages').querySelector(`[data-message="${CSS.escape(d.message_id)}"] .bubble`);
        if (node) { const current = state.conversation.messages.find(m => m.id === d.message_id); if (current) current.content += d.delta; node.innerHTML = markdown(current?.content || d.delta); $('chat').scrollTop = $('chat').scrollHeight; }
      }
      if (item.type === 'assistant_replace') { const m = state.conversation.messages.find(x => x.id === d.message_id); if (m) { m.content = d.content; m.status = d.status; updateMessage(m.id, m.content, m.status); } else loadConversation(); }
      if (item.type === 'message') { state.conversation.messages.push(d); drawMessages(state.conversation.messages); }
      if (item.type === 'conversation') setStatus(d.status, d.stage);
      if (item.type === 'task') { setStatus(d.status, d.stage); if (d.report) toast('Relatório salvo no diretório de relatórios.'); if (!['running','queued','stopping'].includes(d.status)) loadConversation(); }
      if (item.type === 'project') { setProject(d.path); loadConversation(); }
      if (item.type === 'log') {
        const log = document.createElement('div'); log.className = 'log-card'; log.textContent = d.message || 'Atividade Codex'; $('messages').appendChild(log); $('chat').scrollTop = $('chat').scrollHeight;
      }
    });
    state.events.onerror = () => {};
  }
  async function openConversation(id) {
    try { const data = await request(`/api/conversations/${id}`); state.conversation = data.conversation; drawMessages(state.conversation.messages); setProject(state.conversation.project_path); setStatus(state.conversation.status, state.conversation.active_task?.stage); drawSidebar(); localStorage.setItem('portai-conversation', id); listenEvents(); $('sidebar').classList.remove('open'); }
    catch (e) { toast(e.message); }
  }
  async function createConversation() { const data = await request('/api/conversations', 'POST', {}); state.conversations.unshift(data.conversation); await openConversation(data.conversation.id); }
  function resizeComposer() { const el = $('composer'); el.style.height = 'auto'; el.style.height = `${Math.min(el.scrollHeight, 180)}px`; }
  async function send() {
    const text = $('composer').value.trim(); if (!text) return;
    try { if (!state.conversation) await createConversation(); const conv = state.conversation; $('composer').value = ''; resizeComposer(); await request(`/api/conversations/${conv.id}/messages`, 'POST', {message:text}); await loadConversation(); }
    catch (e) { toast(e.message); $('composer').focus(); }
  }
  async function openProjectDialog() {
    $('recentProjects').innerHTML = state.projects.length ? state.projects.map((p, i) => `<button class="recent-project" data-project="${i}">${esc(p.name)}<small>${esc(p.path)}</small></button>`).join('') : '<small class="muted">Nenhum projeto recente. Informe o caminho da pasta.</small>';
    $('recentProjects').querySelectorAll('[data-project]').forEach(button => button.onclick = () => { $('projectPath').value = state.projects[Number(button.dataset.project)].path; });
    $('projectDialog').classList.remove('hidden');
  }
  async function attachProject() {
    try { const path = $('projectPath').value.trim(); if (!path) throw new Error('Informe o caminho da pasta do projeto.'); if (!state.conversation) await createConversation(); await request(`/api/conversations/${state.conversation.id}/project`, 'POST', {path}); $('projectDialog').classList.add('hidden'); toast('Projeto vinculado à conversa.'); await loadConversation(); }
    catch (e) { toast(e.message); }
  }
  $('sendMessage').onclick = send; $('composer').addEventListener('input', resizeComposer);
  $('composer').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(); } });
  $('newConversation').onclick = createConversation; $('projectButton').onclick = openProjectDialog; $('attachProject').onclick = openProjectDialog; $('selectProject').onclick = attachProject;
  document.querySelectorAll('[data-close]').forEach(button => button.onclick = () => $(button.dataset.close).classList.add('hidden'));
  $('projectDialog').onclick = event => { if (event.target === $('projectDialog')) $('projectDialog').classList.add('hidden'); };
  $('stopTask').onclick = async () => { try { await request(`/api/conversations/${state.conversation.id}/stop`, 'POST', {}); setStatus('stopping'); } catch (e) { toast(e.message); } };
  $('resumeTask').onclick = async () => { try { await request(`/api/conversations/${state.conversation.id}/resume`, 'POST', {}); await loadConversation(); } catch (e) { toast(e.message); } };
  $('doctor').onclick = async () => { $('sideStatus').textContent = 'Testando conexão real…'; try { const result = await request('/api/doctor', 'POST', {}); setRuntime(result.runtime, result.connection); toast(result.connection?.ok ? 'Conexão Codex confirmada.' : result.connection?.message || 'Codex indisponível.'); } catch (e) { toast(e.message); } };
  $('openSidebar').onclick = () => $('sidebar').classList.add('open'); $('closeSidebar').onclick = () => $('sidebar').classList.remove('open');
  $('exitAgent').onclick = async () => { if (!confirm('Encerrar o agente e parar tarefas ativas?')) return; try { await request('/api/shutdown', 'POST', {}); document.body.innerHTML = '<div style="padding:40px;color:white;font:16px system-ui">PORTAI Agent encerrado. Você pode fechar esta aba.</div>'; } catch (e) { toast(e.message); } };
  async function boot() {
    try {
      const data = await request('/api/bootstrap'); state.conversations = data.conversations || []; state.projects = data.projects || []; setRuntime(data.runtime || {}); drawSidebar();
      const wanted = localStorage.getItem('portai-conversation'); const selected = state.conversations.find(c => c.id === wanted) || state.conversations[0];
      if (selected) await openConversation(selected.id); else await createConversation();
    } catch (e) { drawWelcome(); toast(`Não foi possível iniciar o chat: ${e.message}`); }
  }
  boot();
})();
