#!/usr/bin/env node
/** LuluLab API v1 client. Node.js >=20; no external packages. */
import { readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { basename, join } from 'node:path';
import { pathToFileURL } from 'node:url';

export class LuluLabError extends Error {}

// Media metadata is distinct from the Content-Type required by a signed upload.
// Inspect bytes rather than trusting an extension or a generic storage MIME type.
export function imageMimeType(bytes) {
  if (bytes.length >= 8 && bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])))
    return 'image/png';
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff)
    return 'image/jpeg';
  if (bytes.length >= 6 && ['GIF87a', 'GIF89a'].includes(bytes.toString('ascii', 0, 6)))
    return 'image/gif';
  if (bytes.length >= 12 && bytes.toString('ascii', 0, 4) === 'RIFF' && bytes.toString('ascii', 8, 12) === 'WEBP')
    return 'image/webp';
  return null;
}

export const HELP = `LuluLab Node.js CLI (Node >=20)
  node lululab_cli.mjs upload <file>
  node lululab_cli.mjs task submit --workflow-id <id> --input <JSON>
  node lululab_cli.mjs task fetch --id <id>
  node lululab_cli.mjs user --credits
Credentials: LULULAB_API_KEY (legacy LINGZHI_API_KEY / LZSTUDIO_API_KEY or
~/.recreate-video-lululab/config.json). Never pass credentials in command arguments.
Optional: LULULAB_API_BASE_URL (default https://customer.lululab.ai).
`;

export async function resolveApiKey(env = process.env, home = homedir()) {
  for (const name of ['LULULAB_API_KEY', 'LINGZHI_API_KEY', 'LZSTUDIO_API_KEY']) {
    if (env[name]?.trim()) return env[name].trim();
  }
  let config;
  try { config = JSON.parse(await readFile(join(home, '.recreate-video-lululab', 'config.json'), 'utf8')); }
  catch (error) {
    if (error.code !== 'ENOENT') throw new LuluLabError('无法读取 LuluLab 本地凭证配置。');
  }
  if (typeof config?.apiKey === 'string' && config.apiKey.trim()) return config.apiKey.trim();
  throw new LuluLabError('缺少 LuluLab API Key；请在本机配置 LULULAB_API_KEY。');
}

function webUrl(value, label) {
  let url;
  try { url = new URL(value); } catch { throw new LuluLabError(`${label} 缺少有效 URL。`); }
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password)
    throw new LuluLabError(`${label} URL 不合法。`);
  // HTTP is supported solely for local development and mock testing.
  if (url.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))
    throw new LuluLabError(`${label} 必须使用 HTTPS。`);
  return url;
}

export class LuluLabClient {
  constructor({ apiKey, baseUrl = 'https://customer.lululab.ai', fetchImpl = globalThis.fetch,
                timeoutMs = 120000 } = {}) {
    this.base = webUrl(baseUrl, 'API');
    if (this.base.search || this.base.hash || !['', '/'].includes(this.base.pathname))
      throw new LuluLabError('API 地址必须是服务根地址。');
    if (!apiKey?.trim()) throw new LuluLabError('缺少 LuluLab API Key。');
    this.key = apiKey.trim();
    this.fetch = fetchImpl;
    this.timeoutMs = timeoutMs;
  }

  async request(path, { method = 'GET', body } = {}) {
    let response;
    try {
      response = await this.fetch(new URL(path, this.base), {
        method, headers: { Authorization: this.key, Accept: 'application/json',
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        redirect: 'error', signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch {
      throw new LuluLabError(method === 'POST' && path === '/tasks'
        ? 'LuluLab 提交响应未确认；任务可能已创建，禁止自动重提。'
        : 'LuluLab API 网络错误或超时。');
    }
    // Do not print server response text, which could contain private data.
    if (!response.ok) throw new LuluLabError(`LuluLab API HTTP ${response.status}。${path === '/tasks' && method === 'POST' ? '禁止自动重提。' : ''}`);
    try { return await response.json(); }
    catch { throw new LuluLabError('LuluLab API 返回无效 JSON；提交任务时禁止自动重提。'); }
  }

  async upload(file) {
    const bytes = await readFile(file);
    if (!bytes.length) throw new LuluLabError('上传文件为空。');
    const actualMimeType = imageMimeType(bytes);
    const destination = await this.request('/uploads', { method: 'POST' });
    const presigned = webUrl(destination.presignedUrl, '上传');
    const location = webUrl(destination.location, '媒体');
    if (typeof destination.mimeType !== 'string' || !destination.mimeType.trim())
      throw new LuluLabError('上传响应缺少 mimeType。');
    const send = method => this.fetch(presigned, { method, body: bytes,
      headers: { 'Content-Type': destination.mimeType }, redirect: 'error',
      signal: AbortSignal.timeout(this.timeoutMs) });
    let response;
    try {
      const method = String(destination.method || destination.httpMethod || 'PUT').toUpperCase();
      if (!['PUT', 'POST'].includes(method)) throw new LuluLabError('上传响应含不支持的 HTTP 方法。');
      response = await send(method);
      if (method === 'PUT' && response.status === 405 &&
          /(?:^|,|\s)POST(?:$|,|\s)/i.test(response.headers.get('allow') || '')) {
        response = await send('POST');
      }
    } catch (error) {
      if (error instanceof LuluLabError) throw error;
      throw new LuluLabError('文件上传网络错误或超时。');
    }
    if (response.status === 415 && response.headers.get('allow') !== 'PUT') {
      const form = new FormData();
      form.append('file', new Blob([bytes], { type: destination.mimeType }), basename(file));
      try {
        response = await this.fetch(presigned, { method: 'POST', body: form,
          redirect: 'error', signal: AbortSignal.timeout(this.timeoutMs) });
      } catch { throw new LuluLabError('文件上传网络错误或超时。'); }
    }
    if (!response.ok) throw new LuluLabError(`文件上传 HTTP ${response.status}（主机 ${presigned.hostname}；Allow ${response.headers.get('allow') || '未提供'}）。`);
    // expiresAt describes the upload URL expiry, not the stored media lifetime.
    return { url: location.href, mimeType: actualMimeType || destination.mimeType };
  }

  async submit(workflowId, input) {
    if (typeof workflowId !== 'string' || !workflowId.trim()) throw new LuluLabError('workflow ID 不能为空。');
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new LuluLabError('任务 input 必须是 JSON 对象。');
    const result = await this.request('/tasks', { method: 'POST', body: { workflowId, input } });
    if (typeof result?.id !== 'string' || !result.id.trim())
      throw new LuluLabError('提交响应缺少任务 ID；禁止自动重提。');
    return result;
  }

  async task(id) {
    if (typeof id !== 'string' || !id.trim()) throw new LuluLabError('任务 ID 不能为空。');
    return this.request(`/tasks/${encodeURIComponent(id.trim())}`);
  }

  async credits() {
    let page = 1, seen = 0, balance = 0n;
    for (;;) {
      const result = await this.request(`/credits?page=${page}&pageSize=100`);
      const total = Number(result.total);
      if (!Number.isSafeInteger(total) || total < 0 || !Array.isArray(result.items))
        throw new LuluLabError('积分响应缺少有效分页信息。');
      for (const account of result.items) {
        const raw = account.creditsBalance;
        if ((typeof raw !== 'string' && typeof raw !== 'number') ||
            !/^-?(0|[1-9]\d*)$/.test(String(raw)) ||
            (typeof raw === 'number' && !Number.isSafeInteger(raw)))
          throw new LuluLabError('积分响应包含无效 creditsBalance。');
        balance += BigInt(raw);
      }
      seen += result.items.length;
      if (seen >= total) break;
      if (!result.items.length || page >= 10000) throw new LuluLabError('积分分页未完成。');
      page++;
    }
    if (balance > BigInt(Number.MAX_SAFE_INTEGER) || balance < BigInt(Number.MIN_SAFE_INTEGER))
      throw new LuluLabError('积分余额超过安全整数范围。');
    return { balance: Number(balance) };
  }
}

export async function main(args = process.argv.slice(2)) {
  if (Number(process.versions.node.split('.')[0]) < 20) throw new LuluLabError('需要 Node.js >=20。');
  if (args.includes('--help') || !args.length) {
    process.stdout.write(HELP); return;
  }
  const option = name => {
    const index = args.indexOf(name);
    if (index < 0 || !args[index + 1]) throw new LuluLabError(`缺少 ${name}。`);
    return args[index + 1];
  };
  // Validate commands before reading credentials or accessing the network.
  let action;
  if (args[0] === 'upload' && args.length === 2) action = client => client.upload(args[1]);
  else if (args[0] === 'user' && args.length === 2 && args[1] === '--credits') action = client => client.credits();
  else if (args[0] === 'task' && args[1] === 'fetch') {
    const id = option('--id'); action = client => client.task(id);
  } else if (args[0] === 'task' && args[1] === 'submit') {
    const workflow = option('--workflow-id');
    let input;
    try { input = JSON.parse(option('--input')); } catch { throw new LuluLabError('--input 必须是有效 JSON。'); }
    action = client => client.submit(workflow, input);
  } else throw new LuluLabError('未知命令；使用 --help 查看用法。');
  if (args.includes('--api-key')) throw new LuluLabError('请通过本机环境变量配置凭证。');
  const client = new LuluLabClient({ apiKey: await resolveApiKey(),
    baseUrl: process.env.LULULAB_API_BASE_URL || 'https://customer.lululab.ai' });
  process.stdout.write(`${JSON.stringify(await action(client))}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {
    // Error messages are controlled by this module; filesystem errors can include paths.
    const diagnostic = error instanceof LuluLabError ? error.message : 'LuluLab Node.js invocation failed';
    const errorCode = args => args[0] === 'task' && args[1] === 'submit'
      ? 'LULULAB_SUBMISSION_UNCERTAIN' : 'LULULAB_CLI_FAILED';
    process.stderr.write(JSON.stringify({ ok: false, errorCode: errorCode(process.argv.slice(2)), diagnostic }) + '\n');
    process.exitCode = 1;
  });
}
