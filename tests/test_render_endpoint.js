const http = require('http');
const path = require('path');
const fs = require('fs');
const assert = require('assert');

const BASE_DIR = path.resolve(__dirname, '..');
const PROJECTS_DIR = path.join(BASE_DIR, 'projects');

// Require the express server
process.env.PORT = '0'; // ephemeral port
const serverModule = require('../server/server.js');
const app = serverModule.app || serverModule;

async function runTests() {
  const server = app.listen(0);
  const port = server.address().port;
  const baseUrl = `http://127.0.0.1:${port}`;
  console.log(`[Test Server] Running on ${baseUrl}`);

  function makeRequest(method, reqPath, body = null) {
    return new Promise((resolve, reject) => {
      const url = new URL(reqPath, baseUrl);
      const req = http.request(url, {
        method,
        headers: { 'Content-Type': 'application/json' }
      }, (res) => {
        let data = '';
        res.on('data', chunk => data += chunk);
        res.on('end', () => {
          try {
            resolve({ status: res.statusCode, body: JSON.parse(data) });
          } catch (_) {
            resolve({ status: res.statusCode, body: data });
          }
        });
      });
      req.on('error', reject);
      if (body) req.write(JSON.stringify(body));
      req.end();
    });
  }

  try {
    console.log('--- Test 1: Render with path traversal rejected (400) ---');
    const r1 = await makeRequest('POST', '/api/projects/..%2f../render', {});
    assert.strictEqual(r1.status, 400);
    console.log('  ✅ PASS: Path traversal rejected with 400');

    console.log('--- Test 2: Render non-existent project rejected (404) ---');
    const r2 = await makeRequest('POST', '/api/projects/non_existent_project_xyz/render', {});
    assert.strictEqual(r2.status, 404);
    console.log('  ✅ PASS: Non-existent project rejected with 404');

    console.log('--- Test 3: Render project without voice_timeline.json returns 400 ---');
    const emptyProjDir = path.join(PROJECTS_DIR, 'test_empty_proj');
    fs.mkdirSync(emptyProjDir, { recursive: true });
    try {
      const r3 = await makeRequest('POST', '/api/projects/test_empty_proj/render', {});
      assert.strictEqual(r3.status, 400);
      assert.ok(r3.body.error.includes('voice_timeline.json'));
      console.log('  ✅ PASS: Missing timeline returned 400 with helpful message');
    } finally {
      fs.rmSync(emptyProjDir, { recursive: true, force: true });
    }

    console.log('\n====================================================');
    console.log('🎉 All Server Render Endpoint Checks Passed!');
    console.log('====================================================\n');
  } finally {
    server.close();
  }
}

runTests().catch(err => {
  console.error('❌ Test failed:', err);
  process.exit(1);
});
