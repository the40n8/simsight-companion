// SimSight meeting point: one fixed address for the phone app, which then
// finds the Mac wherever its tunnel is today. A meeting point can be shared
// by many players: it only keeps, per computer, an ID, today's tunnel address
// and, while one is shown, its pairing code hashed. No names, no game data.
//
// - Serves the phone app (the same one the Mac serves), marked as hosted here.
// - POST /mp/announce: a Mac says where it is (its tunnel address) and,
//   while one is shown, its pairing code (hashed).
// - GET /mp/where?id=: the phone asks where its Mac is.
// - POST /mp/code: the phone finds which Mac a pairing code belongs to;
//   the Mac itself checks the code and gives the key.
//
// No game data goes through here, only addresses. A Mac's address alone is
// useless without its pairing key, which never comes here.
import { DurableObject } from 'cloudflare:workers';

const ONLINE_MS = 15 * 60 * 1000; // a Mac announces itself every 10 minutes
const FORGET_MS = 30 * 24 * 3600 * 1000;
const CODE_TRIES = 10; // per network address and 10 minutes
const CODE_TRIES_ALL = 300; // per minute, everyone together: guessing stays slow
const CODE = /^(\d{6}|\d{9})$/; // 9 digits when computers share a meeting point
const ID = /^[A-Za-z0-9_-]{20,64}$/;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith('/mp/')) return cors(await api(request, url, env));
    // Only the start page and the manifest come here (wrangler.jsonc), both
    // changed below: always the whole file, never "not modified" for the
    // browser's older changed copy.
    const plain = new Request(request);
    plain.headers.delete('if-none-match');
    plain.headers.delete('if-modified-since');
    const res = await env.ASSETS.fetch(plain);
    if (url.pathname === '/manifest.json') return manifest(request, res);
    if (!(res.headers.get('content-type') || '').includes('text/html')) return res;
    // The app: tell it it is hosted here, and let it reach the Macs' tunnels.
    // Changed here, so not the file's own version tag: a browser revalidating
    // would otherwise keep an older changed copy.
    const page = new Response(res.body, res);
    page.headers.delete('etag');
    page.headers.delete('last-modified');
    page.headers.set('cache-control', 'no-cache');
    return new HTMLRewriter()
      .on('meta[http-equiv="content-security-policy"]', {
        element(el) {
          const csp = el.getAttribute('content') || '';
          // Opened on this computer (a meeting point being tried out): the
          // other local test meeting points too.
          const local = /^(localhost|127\.0\.0\.1)$/.test(url.hostname) ? ' http://localhost:* http://127.0.0.1:*' : '';
          el.setAttribute('content', csp.replace("connect-src 'self'", "connect-src 'self' https:" + local));
        }
      })
      .on('head', {
        element(el) {
          el.append('<meta name="simsight-meeting-point" content="1">', { html: true });
        }
      })
      .transform(page);
  }
};

// Phone apps served by other meeting points ask here too (a player can list
// several): nothing here needs a cookie or key, so any origin may ask.
function cors(res) {
  const out = new Response(res.body, res);
  out.headers.set('access-control-allow-origin', '*');
  out.headers.set('access-control-allow-methods', 'GET, POST, OPTIONS');
  out.headers.set('access-control-allow-headers', 'content-type');
  out.headers.set('access-control-max-age', '86400');
  return out;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' }
  });
}

async function sha256(text) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function body(request) {
  try {
    const text = await request.text();
    return text.length < 2048 ? JSON.parse(text) : null;
  } catch {
    return null;
  }
}

async function api(request, url, env) {
  const registry = env.REGISTRY.get(env.REGISTRY.idFromName('registry'));
  if (request.method === 'OPTIONS') return new Response(null, { status: 204 });
  if (url.pathname === '/mp/hello') return json({ meetingPoint: true, codeDigits: 9 });
  if (url.pathname === '/mp/where' && request.method === 'GET') {
    const id = url.searchParams.get('id') || '';
    if (!ID.test(id)) return json({ error: 'bad id' }, 400);
    const mac = await registry.where(id);
    return mac ? json(mac) : json({ error: 'unknown' }, 404);
  }
  if (request.method !== 'POST') return json({ error: 'method' }, 405);
  const b = await body(request);
  if (!b) return json({ error: 'bad body' }, 400);
  if (url.pathname === '/mp/announce') {
    const { id, secret, url: link = '', code = '', codeSeconds = 0 } = b;
    if (!ID.test(id || '') || typeof secret !== 'string' || secret.length < 20) return json({ error: 'bad id' }, 400);
    if (link && !/^https:\/\/[^/\s]+$/.test(link)) return json({ error: 'bad url' }, 400);
    const ok = await registry.announce({
      id,
      secretHash: await sha256(secret),
      url: link,
      codeHash: CODE.test(code) ? await sha256(code) : '',
      codeUntil: Date.now() + Math.min(Number(codeSeconds) || 0, 600) * 1000
    });
    return ok ? json({ ok: true }) : json({ error: 'not this Mac' }, 403);
  }
  if (url.pathname === '/mp/code') {
    const code = String(b.code || '').replace(/\D/g, '');
    const ip = request.headers.get('cf-connecting-ip') || 'unknown';
    if (!(await registry.tryCode(ip))) return json({ error: 'too many tries' }, 429);
    if (!CODE.test(code)) return json({ error: 'unknown' }, 404);
    const mac = await registry.byCode(await sha256(code));
    return mac ? json(mac) : json({ error: 'unknown' }, 404);
  }
  return json({ error: 'not found' }, 404);
}

// The Home Screen app has its own storage on iOS: like the Mac's own server,
// put the phone's games in the install's start address (cookie set by the app).
async function manifest(request, res) {
  const cookie = request.headers.get('cookie') || '';
  const m = /(?:^|;\s*)ssg=([A-Za-z0-9_.~-]{20,2000})/.exec(cookie);
  if (!m || !res.ok) return res;
  const data = await res.json();
  data.start_url = './#g=' + m[1];
  return new Response(JSON.stringify(data), {
    headers: { 'content-type': 'application/manifest+json', 'cache-control': 'no-store' }
  });
}

export class Registry extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS macs (
      id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, name TEXT, url TEXT,
      seen INTEGER, code_hash TEXT, code_until INTEGER)`);
    this.sql.exec('CREATE TABLE IF NOT EXISTS tries (ip TEXT PRIMARY KEY, n INTEGER, since INTEGER)');
  }

  announce({ id, secretHash, url, codeHash, codeUntil }) {
    const now = Date.now();
    this.sql.exec('DELETE FROM macs WHERE seen < ?', now - FORGET_MS);
    const row = this.sql.exec('SELECT secret_hash FROM macs WHERE id = ?', id).toArray()[0];
    if (row && row.secret_hash !== secretHash) return false; // first come keeps the id
    this.sql.exec(
      `INSERT INTO macs (id, secret_hash, name, url, seen, code_hash, code_until) VALUES (?, ?, '', ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET name = '', url = excluded.url, seen = excluded.seen,
         code_hash = excluded.code_hash, code_until = excluded.code_until`,
      id, secretHash, url, now, codeHash, codeUntil
    );
    return true;
  }

  #public(row) {
    const online = !!row.url && Date.now() - row.seen < ONLINE_MS;
    return { id: row.id, url: online ? row.url : '', online, seen: row.seen };
  }

  where(id) {
    const row = this.sql.exec('SELECT * FROM macs WHERE id = ?', id).toArray()[0];
    return row ? this.#public(row) : null;
  }

  byCode(codeHash) {
    const row = this.sql
      .exec('SELECT * FROM macs WHERE code_hash = ? AND code_until > ? ORDER BY seen DESC LIMIT 1', codeHash, Date.now())
      .toArray()[0];
    return row ? this.#public(row) : null;
  }

  tryCode(ip) {
    const now = Date.now();
    // Everyone together: a guesser with many network addresses stays slow.
    const minute = Math.floor(now / 60000);
    const all = this.sql.exec("SELECT n, since FROM tries WHERE ip = '*'").toArray()[0];
    if (!all || all.since !== minute) {
      this.sql.exec("INSERT OR REPLACE INTO tries (ip, n, since) VALUES ('*', 1, ?)", minute);
    } else if (all.n >= CODE_TRIES_ALL) {
      return false;
    } else {
      this.sql.exec("UPDATE tries SET n = n + 1 WHERE ip = '*'");
    }
    const row = this.sql.exec('SELECT n, since FROM tries WHERE ip = ?', ip).toArray()[0];
    if (!row || now - row.since > 10 * 60 * 1000) {
      this.sql.exec('INSERT OR REPLACE INTO tries (ip, n, since) VALUES (?, 1, ?)', ip, now);
      this.sql.exec("DELETE FROM tries WHERE ip != '*' AND since < ?", now - 3600 * 1000);
      return true;
    }
    if (row.n >= CODE_TRIES) return false;
    this.sql.exec('UPDATE tries SET n = n + 1 WHERE ip = ?', ip);
    return true;
  }
}
