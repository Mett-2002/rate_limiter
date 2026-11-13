(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const tester = byId('tester');
  const form = byId('request-form');
  const keyInput = byId('api-key');
  const userInput = byId('user-id');
  const records = [];
  let sequence = 0;
  let busy = false;

  function show(record) {
    byId('request-caption').textContent = `Request #${record.number} · ${record.user} · ${record.sent}`;
    byId('request-output').textContent = JSON.stringify(record.request, null, 2);
    const badge = byId('http-status');
    badge.textContent = `HTTP ${record.status}`;
    badge.className = 'badge ' + (record.status === 429 ? 'limited' : record.status < 400 ? 'ok' : 'error');
    byId('response-caption').textContent = `Recorded response #${record.number} · ${record.duration} ms. This is a snapshot, not live quota.`;
    byId('response-output').textContent = JSON.stringify({headers: record.headers, body: record.body}, null, 2);
    for (const button of byId('history').querySelectorAll('button')) {
      button.setAttribute('aria-pressed', String(button.dataset.number === String(record.number)));
    }
  }

  function addRecord(record) {
    records.push(record);
    if (records.length > 20) records.shift();
    const history = byId('history');
    history.replaceChildren();
    for (const item of records) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'secondary';
      button.dataset.number = item.number;
      button.textContent = `#${item.number} · ${item.user} · ${item.status}`;
      button.addEventListener('click', () => show(item));
      history.append(button);
    }
    show(record);
  }

  async function sendOne(consume, key, user) {
    const url = new URL(consume ? tester.dataset.consumeUrl : tester.dataset.statusUrl, location.origin);
    if (!consume) url.searchParams.set('user_id', user);
    const method = consume ? 'POST' : 'GET';
    const headers = {Accept: 'application/json', Authorization: `Bearer ${key}`};
    if (consume) headers['Content-Type'] = 'application/json';
    const request = {method, url: url.pathname + url.search, headers: {...headers, Authorization: 'Bearer [hidden]'}};
    if (consume) request.body = {user_id: user};
    byId('request-caption').textContent = `Sending ${method} for ${user}…`;
    byId('request-output').textContent = JSON.stringify(request, null, 2);
    byId('http-status').textContent = 'Waiting';
    byId('http-status').className = 'badge';
    byId('response-caption').textContent = 'Waiting for the API response.';
    byId('response-output').textContent = '';
    const sent = new Date().toLocaleTimeString();
    const started = performance.now();
    const response = await fetch(url, {
      method, headers, credentials: 'omit', cache: 'no-store',
      ...(consume ? {body: JSON.stringify(request.body)} : {}),
    });
    const text = await response.text();
    let body;
    try { body = JSON.parse(text); } catch { body = text; }
    addRecord({number: ++sequence, user, sent, request, status: response.status,
      duration: Math.round(performance.now() - started), headers: Object.fromEntries(response.headers.entries()), body});
  }

  async function run(consume, count = 1) {
    if (busy || !form.reportValidity()) return;
    const key = keyInput.value.trim();
    const user = userInput.value.trim();
    const notice = byId('notice');
    if (!key || !user) {
      notice.textContent = 'Enter a non-empty API key and user ID.';
      notice.className = 'error';
      return;
    }
    busy = true;
    notice.className = '';
    notice.textContent = 'Sending…';
    const controls = [...form.querySelectorAll('button, input')];
    controls.forEach(control => { control.disabled = true; });
    try {
      for (let i = 0; i < count; i++) await sendOne(consume, key, user);
      notice.textContent = `${count} request${count === 1 ? '' : 's'} completed for ${user}. Inspect the actual HTTP status and body below.`;
    } catch (error) {
      notice.className = 'error';
      notice.textContent = 'No complete HTTP response was received. Check that the API is running. No automatic retry was made.';
      byId('http-status').textContent = 'Network error';
      byId('http-status').className = 'badge error';
      byId('response-caption').textContent = 'The outcome of this request could not be confirmed.';
      byId('response-output').textContent = error.message;
    } finally {
      busy = false;
      controls.forEach(control => { control.disabled = false; });
    }
  }

  form.addEventListener('submit', event => { event.preventDefault(); run(true); });
  byId('status-button').addEventListener('click', () => run(false));
  byId('batch-button').addEventListener('click', () => run(true, 6));
  form.querySelectorAll('button').forEach(button => { button.disabled = false; });
  byId('notice').textContent = 'Enter your key, choose a user, then send a request.';
})();
