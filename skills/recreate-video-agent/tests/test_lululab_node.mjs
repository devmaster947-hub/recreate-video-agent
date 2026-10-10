import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { LuluLabClient, resolveApiKey, imageMimeType } from '../scripts/lululab_cli.mjs';

const response = value => new Response(JSON.stringify(value), { status: 200 });

test('image MIME follows bytes, while signed storage upload keeps its required Content-Type', async () => {
  const root = await mkdtemp(join(tmpdir(), 'lululab-mime-'));
  try {
    // Deliberately misleading extension: the content is PNG.
    const file = join(root, 'product.jpg');
    const bytes = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
    await writeFile(file, bytes);
    const calls = [];
    const client = new LuluLabClient({ apiKey: 'fake-key', fetchImpl: async (url, options) => {
      calls.push({ url: String(url), ...options });
      return calls.length === 1 ? response({ location: 'https://media.example/image',
        mimeType: 'application/octet-stream', presignedUrl: 'https://storage.example/upload' })
        : new Response(null, { status: 204 });
    }});
    assert.deepEqual(await client.upload(file), { url: 'https://media.example/image', mimeType: 'image/png' });
    assert.equal(calls[1].headers['Content-Type'], 'application/octet-stream');
    assert.equal(calls[1].headers.Authorization, undefined);
    assert.deepEqual(calls[1].body, bytes);
    assert.equal(imageMimeType(Buffer.from([0xff, 0xd8, 0xff])), 'image/jpeg');
    assert.equal(imageMimeType(Buffer.from('GIF89a')), 'image/gif');
    assert.equal(imageMimeType(Buffer.from('RIFF0000WEBP')), 'image/webp');
    assert.equal(imageMimeType(Buffer.from('RIFF0000WAVE')), null);
    assert.equal(imageMimeType(Buffer.from([137, 80])), null);
  } finally { await rm(root, { recursive: true }); }
});

test('API key uses raw Authorization; exact task envelope; fetch ID encoding', async () => {
  const calls = [];
  const client = new LuluLabClient({ apiKey: 'fake-key', fetchImpl: async (url, options) => {
    calls.push({ url: String(url), ...options }); return response({ id: 'task-1', status: 'Created' });
  }});
  const input = { model: 'seedance-2-fast', prompt: 'test', duration: 15, referenceImages: [] };
  await client.submit('VideoGenV2', input);
  assert.equal(calls[0].url, 'https://customer.lululab.ai/tasks');
  assert.equal(calls[0].headers.Authorization, 'fake-key');
  assert.deepEqual(JSON.parse(calls[0].body), { workflowId: 'VideoGenV2', input });
  assert.equal(calls[0].redirect, 'error');
  await client.task('a/b');
  assert.equal(calls[1].url, 'https://customer.lululab.ai/tasks/a%2Fb');
});

test('upload requests destination then PUTs bytes without leaking API key to storage', async () => {
  const root = await mkdtemp(join(tmpdir(), 'lululab-test-'));
  try {
    const file = join(root, 'product.png'); await writeFile(file, 'image-bytes');
    const calls = [];
    const client = new LuluLabClient({ apiKey: 'fake-key', fetchImpl: async (url, options) => {
      calls.push({ url: String(url), ...options });
      return calls.length === 1 ? response({ location: 'https://media.example/image', mimeType: 'image/png',
        presignedUrl: 'https://storage.example/put?signature=test', expiresAt: 'soon' }) : new Response(null, { status: 204 });
    }});
    assert.deepEqual(await client.upload(file), { url: 'https://media.example/image', mimeType: 'image/png' });
    assert.equal(calls[0].url, 'https://customer.lululab.ai/uploads');
    assert.equal(calls[0].method, 'POST');
    assert.equal(calls[1].method, 'PUT');
    assert.equal(calls[1].headers.Authorization, undefined);
    assert.equal(calls[1].headers['Content-Type'], 'image/png');
    assert.equal(calls[1].body.toString(), 'image-bytes');
  } finally { await rm(root, { recursive: true }); }
});

test('credits sum all paginated accounts, including string int64 values', async () => {
  const calls = [];
  const client = new LuluLabClient({ apiKey: 'fake-key', fetchImpl: async url => {
    calls.push(String(url)); return response({ total: '2', items: [{ creditsBalance: calls.length === 1 ? '200' : 30 }] });
  }});
  assert.deepEqual(await client.credits(), { balance: 230 });
  assert.match(calls[1], /page=2&pageSize=100/);
});

test('credit precision errors and incomplete pagination fail closed', async () => {
  for (const value of [{ total: 1, items: [{ creditsBalance: '9007199254740992' }] },
                       { total: 1, items: [] }, { total: 1, items: [{ creditsBalance: null }] }]) {
    const client = new LuluLabClient({ apiKey: 'fake-key', fetchImpl: async () => response(value) });
    await assert.rejects(() => client.credits());
  }
});

test('ambiguous submissions never retry or disclose response details', async () => {
  let calls = 0;
  const client = new LuluLabClient({ apiKey: 'fake-key', fetchImpl: async () => {
    calls++; throw new Error('fake-key network detail');
  }});
  await assert.rejects(() => client.submit('VideoGenV2', {}), error =>
    error.message.includes('禁止自动重提') && !error.message.includes('fake-key'));
  assert.equal(calls, 1);
  const bad = new LuluLabClient({ apiKey: 'fake-key', fetchImpl: async () => new Response('fake-key', { status: 401 }) });
  await assert.rejects(() => bad.task('id'), /HTTP 401/);
});

test('credential precedence and URL transport restrictions', async () => {
  assert.equal(await resolveApiKey({ LULULAB_API_KEY: 'new', LINGZHI_API_KEY: 'old' }), 'new');
  assert.equal(await resolveApiKey({ LZSTUDIO_API_KEY: 'old' }), 'old');
  assert.throws(() => new LuluLabClient({ apiKey: 'x', baseUrl: 'http://external.example' }));
  assert.throws(() => new LuluLabClient({ apiKey: 'x', baseUrl: 'https://user:secret@example.com' }));
});

// Exercise the actual Python -> Node subprocess bridge against a local mock API.
test('Python adapter drives portable Node CLI end to end', async () => {
  const { createServer } = await import('node:http');
  const { execFile } = await import('node:child_process');
  const { promisify } = await import('node:util');
  const root = await mkdtemp(join(tmpdir(), 'lululab-bridge-'));
  const requests = [];
  const server = createServer(async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    requests.push({ path: req.url, method: req.method, auth: req.headers.authorization, body });
    const base = `http://127.0.0.1:${server.address().port}`;
    res.setHeader('Content-Type', 'application/json');
    if (req.url === '/uploads') res.end(JSON.stringify({ location: `${base}/image.png`, mimeType: 'application/octet-stream', presignedUrl: `${base}/storage` }));
    else if (req.url === '/storage') { res.statusCode = 204; res.end(); }
    else if (req.url === '/tasks' && req.method === 'POST') res.end(JSON.stringify({ id: 'task-bridge', status: 'Created' }));
    else if (req.url === '/tasks/task-bridge') res.end(JSON.stringify({ id: 'task-bridge', status: 'Succeeded', input: { referenceImages: [{ url: `${base}/image.png` }] }, output: { url: `${base}/video.mp4` } }));
    else if (req.url.startsWith('/credits?')) res.end(JSON.stringify({ total: 2, items: [{ creditsBalance: '10' }, { creditsBalance: 20 }] }));
    else { res.statusCode = 404; res.end('{}'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const file = join(root, 'product.png'); await writeFile(file, Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    const scriptDir = fileURLToPath(new URL('../scripts/', import.meta.url));
    const code = `import sys\nsys.path.insert(0,sys.argv[1])\nimport server_video_analysis as analysis\nimport local_video_cli as video\ncli=analysis.resolve_cli(None)\nanalysis.verify_key(cli,'fake-key')\nmedia=video.run_lululab_cli(['upload',sys.argv[2]])\nresult=video.run_lululab_cli(['task','submit','--workflow-id','VideoGenV2','--input','{"referenceImages":[]}'])\nassert video.run_lululab_cli(['task','fetch','--id',result['id']])['status']=='Succeeded'\nprint(result['id'])\n`;
    const env = { ...process.env, LULULAB_API_KEY: 'fake-key', LULULAB_API_BASE_URL: `http://127.0.0.1:${server.address().port}`,
      LULULAB_NODE: process.execPath, LULULAB_CLI: fileURLToPath(new URL('../scripts/lululab_cli.mjs', import.meta.url)),
      PYTHONDONTWRITEBYTECODE: '1' };
    const { stdout } = await promisify(execFile)('python3', ['-c', code, scriptDir, file], { env, timeout: 10000 });
    assert.equal(stdout.trim(), 'task-bridge');
    const task = requests.find(item => item.path === '/tasks');
    assert.equal(task.auth, 'fake-key');
    const payload = JSON.parse(task.body);
    assert.equal(payload.workflowId, 'VideoGenV2');
    assert.deepEqual(payload.input.referenceImages, []);
    assert.equal(requests.find(item => item.path === '/storage').auth, undefined);
    assert.equal(requests.filter(item => item.path === '/tasks').length, 1);
  } finally { await new Promise(resolve => server.close(resolve)); await rm(root, { recursive: true }); }
});

test('presigned upload honors server Allow POST after PUT is rejected', async () => {
  const calls = [];
  const client = new LuluLabClient({ apiKey: 'fake-key', fetchImpl: async (url, options) => {
    calls.push({ url: String(url), ...options });
    if (calls.length === 1) return response({ location: 'https://media.example/image', mimeType: 'image/png',
      presignedUrl: 'https://storage.example/upload' });
    if (calls.length === 2) return new Response(null, { status: 405, headers: { Allow: 'POST' } });
    return new Response(null, { status: 204 });
  }});
  const root = await mkdtemp(join(tmpdir(), 'lululab-upload-'));
  try {
    const file = join(root, 'reference.png'); await writeFile(file, 'image-bytes');
    assert.deepEqual(await client.upload(file), { url: 'https://media.example/image', mimeType: 'image/png' });
    assert.deepEqual(calls.map(c => c.method), ['POST', 'PUT', 'POST']);
    assert.equal(calls[2].headers.Authorization, undefined);
  } finally { await rm(root, { recursive: true }); }
});

test('asset POST can require multipart file field', async () => {
  const calls = [];
  const client = new LuluLabClient({ apiKey: 'fake-key', fetchImpl: async (url, options) => {
    calls.push({ url: String(url), ...options });
    if (calls.length === 1) return response({ location: 'https://media.example/image', mimeType: 'image/png',
      presignedUrl: 'https://storage.example/upload' });
    if (calls.length === 2) return new Response(null, { status: 405, headers: { Allow: 'POST' } });
    if (calls.length === 3) return new Response(null, { status: 415 });
    return new Response(null, { status: 204 });
  }});
  const root = await mkdtemp(join(tmpdir(), 'lululab-multipart-'));
  try {
    const file = join(root, 'reference.png'); await writeFile(file, 'image-bytes');
    assert.deepEqual(await client.upload(file), { url: 'https://media.example/image', mimeType: 'image/png' });
    assert.deepEqual(calls.map(c => c.method), ['POST', 'PUT', 'POST', 'POST']);
    assert.ok(calls[3].body instanceof FormData);
    assert.equal(calls[3].body.get('file').name, 'reference.png');
    assert.equal(calls[3].headers?.Authorization, undefined);
  } finally { await rm(root, { recursive: true }); }
});
