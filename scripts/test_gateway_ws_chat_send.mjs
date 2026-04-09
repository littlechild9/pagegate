#!/usr/bin/env node
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function getDefaultToken() {
  const authPath = path.join(os.homedir(), '.openclaw', 'identity', 'device-auth.json');
  try {
    const auth = readJson(authPath);
    return auth?.tokens?.operator?.token || '';
  } catch {}
  const configPath = path.join(os.homedir(), '.openclaw', 'openclaw.json');
  const cfg = readJson(configPath);
  return cfg?.gateway?.auth?.token || '';
}

const sessionKey = process.env.OPENCLAW_SESSION_KEY || process.argv[2];
const message = process.env.OPENCLAW_TEST_MESSAGE || process.argv[3] || 'gateway ws rpc direct test';
const gatewayUrl = process.env.OPENCLAW_GATEWAY_URL || 'ws://127.0.0.1:18789';
const gatewayToken = process.env.OPENCLAW_GATEWAY_TOKEN || getDefaultToken();

if (!sessionKey) {
  console.error('Missing OPENCLAW_SESSION_KEY or first positional arg');
  process.exit(2);
}
if (!gatewayToken) {
  console.error('Missing OPENCLAW_GATEWAY_TOKEN and no token found in ~/.openclaw/openclaw.json');
  process.exit(2);
}

const reqId = crypto.randomUUID();
const idem = `pagegate-ws-${Date.now()}`;
let challengeSeen = false;
let connected = false;

const ws = new WebSocket(gatewayUrl);
const timeout = setTimeout(() => {
  console.error('Timed out waiting for gateway response');
  try { ws.close(); } catch {}
  process.exit(1);
}, 15000);

ws.addEventListener('open', () => {
  console.log(`WS opened: ${gatewayUrl}`);
});

ws.addEventListener('message', (event) => {
  const text = typeof event.data === 'string' ? event.data : String(event.data);
  let msg;
  try {
    msg = JSON.parse(text);
  } catch {
    console.log('NON_JSON', text);
    return;
  }

  if (msg.type === 'event' && msg.event === 'connect.challenge' && !challengeSeen) {
    challengeSeen = true;
    const connectReq = {
      type: 'req',
      id: crypto.randomUUID(),
      method: 'connect',
      params: {
        minProtocol: 3,
        maxProtocol: 3,
        client: {
          id: 'cli',
          version: '2026.4.5',
          platform: process.platform,
          mode: 'cli',
        },
        role: 'operator',
        scopes: ['operator.read', 'operator.write'],
        caps: [],
        commands: [],
        permissions: {},
        auth: { token: gatewayToken },
        locale: 'zh-CN',
        userAgent: 'openclaw-control-ui',
      },
    };
    ws.send(JSON.stringify(connectReq));
    return;
  }

  if (msg.type === 'res' && msg.ok && msg.payload?.type === 'hello-ok' && !connected) {
    connected = true;
    const sendReq = {
      type: 'req',
      id: reqId,
      method: 'chat.send',
      params: {
        sessionKey,
        message,
        idempotencyKey: idem,
      },
    };
    ws.send(JSON.stringify(sendReq));
    return;
  }

  if (msg.type === 'res' && msg.id === reqId) {
    clearTimeout(timeout);
    console.log(JSON.stringify(msg, null, 2));
    ws.close();
    process.exit(msg.ok ? 0 : 1);
  }
});

ws.addEventListener('error', (err) => {
  clearTimeout(timeout);
  console.error('WebSocket error', err.message || err);
  process.exit(1);
});

ws.addEventListener('close', (ev) => {
  if (!connected) {
    console.error(`WebSocket closed early: code=${ev.code} reason=${ev.reason || ''}`);
  }
});
