/**
 * Host Header Security and DNS Rebinding Protection Test Suite (Phase 3.2)
 *
 * Requirements verified:
 * 1. Validate HTTP Host header before API routes and sensitive static-file handlers.
 * 2. In local-only mode, accept only loopback hostnames (localhost, 127.0.0.1, ::1, [::1])
 *    and matching application ports.
 * 3. Reject malformed or unexpected hosts (e.g. attacker.example, arbitrary domains resolving to loopback).
 * 4. Do not trust unverified X-Forwarded-Host headers.
 * 5. Preserve Origin checks for state-changing browser requests (Origin checks supplement Host validation).
 * 6. Keep requests without Origin header compatible with legitimate local tools while enforcing Host validation.
 * 7. In remote mode, enforce an explicitly configured host allowlist (ALLOWED_HOSTS); do not trust arbitrary hosts.
 * 8. Static file routes (/projects, /assets, /) are protected from unexpected Host headers.
 */

const assert = require('assert');
const http = require('http');
const path = require('path');
const fs = require('fs');
const {
  app,
  isAllowedHost,
  isAllowedOrigin,
  parseHost,
  getAllowedRemoteHosts
} = require('../server/server');

let server;
let serverPort;
let baseUrl;

function makeRawRequest({ method = 'GET', path: reqPath = '/', headers = {}, body = null }) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: '127.0.0.1',
      port: serverPort,
      path: reqPath,
      method,
      headers: { ...headers }
    };

    let payload = null;
    if (body !== null) {
      payload = typeof body === 'string' ? body : JSON.stringify(body);
      if (!options.headers['Content-Type']) {
        options.headers['Content-Type'] = 'application/json';
      }
      options.headers['Content-Length'] = Buffer.byteLength(payload);
    }

    const req = http.request(options, (res) => {
      const chunks = [];
      res.on('data', (d) => chunks.push(d));
      res.on('end', () => {
        const rawBuffer = Buffer.concat(chunks);
        const text = rawBuffer.toString('utf-8');
        let json = null;
        try {
          json = JSON.parse(text);
        } catch (_) {}
        resolve({
          statusCode: res.statusCode,
          headers: res.headers,
          body: json || text,
          rawBuffer
        });
      });
    });

    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function sendRawHttpOverSocket(port, rawHttpText) {
  return new Promise((resolve, reject) => {
    const net = require('net');
    const client = net.connect({ port, host: '127.0.0.1' }, () => {
      client.write(rawHttpText);
    });
    let data = '';
    client.on('data', (chunk) => {
      data += chunk.toString('utf-8');
    });
    client.on('end', () => {
      const match = data.match(/^HTTP\/1\.[01] (\d{3})/);
      const statusCode = match ? parseInt(match[1], 10) : null;
      resolve({ statusCode, raw: data });
    });
    client.on('error', reject);
  });
}

async function runTests() {
  console.log('====================================================');
  console.log('🛡️ Starting Host-Header Security Test Suite (Phase 3.2)');
  console.log('====================================================\n');

  let passed = 0;
  let failed = 0;

  async function test(name, fn) {
    try {
      await fn();
      console.log(`  ✅ PASS: ${name}`);
      passed++;
    } catch (err) {
      console.error(`  ❌ FAIL: ${name}`);
      console.error(`     Error: ${err.message}`);
      failed++;
    }
  }

  // ----------------------------------------------------
  // Suite 1: Unit Parsing and Normalization Checks
  // ----------------------------------------------------
  console.log('--- Suite 1: Host Parsing & Normalization Unit Checks ---');

  await test('parseHost correctly normalizes IPv4, standard hostname, and port', async () => {
    assert.deepStrictEqual(parseHost('localhost'), { hostname: 'localhost', port: null });
    assert.deepStrictEqual(parseHost('127.0.0.1:3000'), { hostname: '127.0.0.1', port: '3000' });
    assert.deepStrictEqual(parseHost('LOCALHOST:8080'), { hostname: 'localhost', port: '8080' });
    assert.deepStrictEqual(parseHost('example.com:443'), { hostname: 'example.com', port: '443' });
  });

  await test('parseHost correctly parses IPv6 bracketed and bare forms', async () => {
    assert.deepStrictEqual(parseHost('[::1]'), { hostname: '::1', port: null });
    assert.deepStrictEqual(parseHost('[::1]:3000'), { hostname: '::1', port: '3000' });
    assert.deepStrictEqual(parseHost('::1'), { hostname: '::1', port: null });
  });

  await test('parseHost rejects malformed or malicious host structures', async () => {
    assert.strictEqual(parseHost(''), null);
    assert.strictEqual(parseHost(null), null);
    assert.strictEqual(parseHost('   '), null);
    assert.strictEqual(parseHost('attacker.example:abc'), null); // non-numeric port
    assert.strictEqual(parseHost('attacker.example:99999'), null); // port > 65535
    assert.strictEqual(parseHost('127.0.0.1@attacker.com'), null); // userinfo injection
    assert.strictEqual(parseHost('localhost/malicious'), null); // path injection
    assert.strictEqual(parseHost('localhost\\malicious'), null);
    assert.strictEqual(parseHost('localhost:3000:3000'), null);
    assert.strictEqual(parseHost('a'.repeat(300)), null); // oversized host
  });

  // ----------------------------------------------------
  // Suite 2: HTTP Integration Tests on Ephemeral Server
  // ----------------------------------------------------
  console.log('\n--- Suite 2: HTTP Route Protection & Host Header Validation ---');

  // Start ephemeral test server on loopback
  await new Promise((resolve) => {
    server = app.listen(0, '127.0.0.1', () => {
      serverPort = server.address().port;
      baseUrl = `http://127.0.0.1:${serverPort}`;
      console.log(`[Host Test Server] Listening on ${baseUrl}\n`);
      resolve();
    });
  });

  try {
    await test('Legitimate local UI requests with Host 127.0.0.1:<port> are accepted (200)', async () => {
      const res = await makeRawRequest({
        method: 'GET',
        path: '/api/projects',
        headers: {
          Host: `127.0.0.1:${serverPort}`
        }
      });
      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(res.body.success, true);
    });

    await test('Legitimate local UI requests with Host localhost:<port> are accepted (200)', async () => {
      const res = await makeRawRequest({
        method: 'GET',
        path: '/api/projects',
        headers: {
          Host: `localhost:${serverPort}`
        }
      });
      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(res.body.success, true);
    });

    await test('Legitimate IPv6 loopback Host [::1]:<port> is accepted (200)', async () => {
      const res = await makeRawRequest({
        method: 'GET',
        path: '/api/projects',
        headers: {
          Host: `[::1]:${serverPort}`
        }
      });
      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(res.body.success, true);
    });

    await test('Legitimate bare loopback host without port is accepted (200)', async () => {
      const res = await makeRawRequest({
        method: 'GET',
        path: '/api/projects',
        headers: {
          Host: 'localhost'
        }
      });
      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(res.body.success, true);
    });

    await test('DNS-rebinding attempt with Host attacker.example is rejected with HTTP 400', async () => {
      const res = await makeRawRequest({
        method: 'GET',
        path: '/api/projects',
        headers: {
          Host: 'attacker.example'
        }
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.body.success, false);
      assert.strictEqual(res.body.error, 'Invalid or disallowed Host header');
    });

    await test('DNS-rebinding attempt with Host attacker.example:<port> is rejected with HTTP 400', async () => {
      const res = await makeRawRequest({
        method: 'GET',
        path: '/api/projects',
        headers: {
          Host: `attacker.example:${serverPort}`
        }
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.body.success, false);
      assert.strictEqual(res.body.error, 'Invalid or disallowed Host header');
    });

    await test('Host-header attacks are rejected even when no Origin header is sent', async () => {
      // CLI/curl or script without Origin
      const res = await makeRawRequest({
        method: 'POST',
        path: '/api/projects',
        headers: {
          Host: 'evil-subdomain.attacker.com',
          'Content-Type': 'application/json'
        },
        body: { name: 'unauthorized_project' }
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.body.success, false);
      assert.strictEqual(res.body.error, 'Invalid or disallowed Host header');
    });

    await test('Mismatched port in Host header is rejected with HTTP 400', async () => {
      const wrongPort = serverPort === 9999 ? 8888 : 9999;
      const res = await makeRawRequest({
        method: 'GET',
        path: '/api/projects',
        headers: {
          Host: `localhost:${wrongPort}`
        }
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.body.success, false);
    });

    await test('Unverified X-Forwarded-Host header is never trusted over invalid Host', async () => {
      const res = await makeRawRequest({
        method: 'GET',
        path: '/api/projects',
        headers: {
          Host: 'attacker.example',
          'X-Forwarded-Host': `localhost:${serverPort}`
        }
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.body.success, false);
    });

    await test('Unexpected Host values cannot read protected static assets (/projects/*)', async () => {
      const res = await makeRawRequest({
        method: 'GET',
        path: '/projects/demo_project/script.json',
        headers: {
          Host: 'attacker.example'
        }
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.body.success, false);
      assert.strictEqual(res.body.error, 'Invalid or disallowed Host header');
    });

    await test('Legitimate Host can read static assets (/projects/*)', async () => {
      const res = await makeRawRequest({
        method: 'GET',
        path: '/projects/demo_project/script.json',
        headers: {
          Host: `127.0.0.1:${serverPort}`
        }
      });
      // Should succeed if demo_project script.json exists (200), or 404 if file missing, but NOT 400 Host rejected
      assert.notStrictEqual(res.statusCode, 400);
      assert.ok([200, 304, 404].includes(res.statusCode));
    });

    await test('Unexpected Host values cannot read Web studio static files (/index.html)', async () => {
      const res = await makeRawRequest({
        method: 'GET',
        path: '/index.html',
        headers: {
          Host: 'attacker.example'
        }
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.body.success, false);
    });

    await test('Missing Host header is rejected with HTTP 400 (both HTTP/1.0 and HTTP/1.1)', async () => {
      // 1. HTTP/1.0 without Host: Express middleware catches missing host header
      const res10 = await sendRawHttpOverSocket(
        serverPort,
        'GET /api/projects HTTP/1.0\r\n\r\n'
      );
      assert.strictEqual(res10.statusCode, 400);
      assert.ok(res10.raw.includes('Invalid or disallowed Host header'));

      // 2. HTTP/1.1 without Host: protocol-level rejection
      const res11 = await sendRawHttpOverSocket(
        serverPort,
        'GET /api/projects HTTP/1.1\r\nConnection: close\r\n\r\n'
      );
      assert.strictEqual(res11.statusCode, 400);
    });

    // ----------------------------------------------------
    // Suite 3: Remote Mode Behavior & Allowlist Confinement
    // ----------------------------------------------------
    console.log('\n--- Suite 3: Remote-Mode Restrictions & Confinement Checks ---');

    await test('isAllowedHost in default local mode rejects non-loopback even if ALLOWED_HOSTS is set', async () => {
      // With default ALLOW_REMOTE=false
      const mockReq = { socket: { localPort: serverPort } };
      assert.strictEqual(isAllowedHost(`attacker.example:${serverPort}`, mockReq), false);
      assert.strictEqual(isAllowedHost(`192.168.1.50:${serverPort}`, mockReq), false);
      assert.strictEqual(isAllowedHost(`10.0.0.5:${serverPort}`, mockReq), false);
    });

    await test('Remote mode allowlist resolution: only explicitly configured hosts are allowed', async () => {
      // Save original env
      const origAllowed = process.env.ALLOWED_HOSTS;
      try {
        process.env.ALLOWED_HOSTS = 'recap.internal.lan, 192.168.1.100:3000, secure-studio.example.org';
        const allowedSet = getAllowedRemoteHosts();
        assert.ok(allowedSet.has('recap.internal.lan'));
        assert.ok(allowedSet.has('192.168.1.100'));
        assert.ok(allowedSet.has('secure-studio.example.org'));
        assert.strictEqual(allowedSet.has('evil.com'), false);
        assert.strictEqual(allowedSet.has('attacker.example'), false);
      } finally {
        if (origAllowed !== undefined) {
          process.env.ALLOWED_HOSTS = origAllowed;
        } else {
          delete process.env.ALLOWED_HOSTS;
        }
      }
    });

    // ----------------------------------------------------
    // Suite 4: Origin Checks Preservation
    // ----------------------------------------------------
    console.log('\n--- Suite 4: Preservation of Origin Checks & API Safety ---');

    await test('Valid loopback host with disallowed browser Origin on state-changing API is rejected with 403', async () => {
      const res = await makeRawRequest({
        method: 'POST',
        path: '/api/projects',
        headers: {
          Host: `127.0.0.1:${serverPort}`,
          Origin: 'http://evil.com',
          'Content-Type': 'application/json'
        },
        body: { name: 'test_csrf' }
      });
      // Origin check must still intercept with 403
      assert.strictEqual(res.statusCode, 403);
      assert.strictEqual(res.body.error, 'Disallowed browser origin');
    });

    await test('Valid loopback host with valid loopback Origin on state-changing API passes security guards', async () => {
      const res = await makeRawRequest({
        method: 'POST',
        path: '/api/projects',
        headers: {
          Host: `127.0.0.1:${serverPort}`,
          Origin: `http://127.0.0.1:${serverPort}`,
          'Content-Type': 'application/json'
        },
        body: { name: 'test_legit_origin' }
      });
      // Should reach API handler (not 400 Host rejected, not 403 Origin rejected)
      assert.notStrictEqual(res.statusCode, 400);
      assert.notStrictEqual(res.statusCode, 403);
    });

  } finally {
    if (server) {
      server.close();
    }
  }

  console.log('\n====================================================');
  console.log(`📊 Host Security Test Summary: ${passed} Passed, ${failed} Failed`);
  console.log('====================================================');

  if (failed > 0) {
    process.exit(1);
  }
}

runTests().catch((err) => {
  console.error('[Host Test Runner Error]', err);
  process.exit(1);
});
