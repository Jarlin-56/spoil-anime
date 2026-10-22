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
const resolveConcatBinary = serverModule.resolveConcatBinary;

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
    console.log('--- Test 1: Binary Resolver finds installed concat.exe ---');
    const concatBin = resolveConcatBinary();
    console.log(`  Discovered binary: ${concatBin}`);
    assert.ok(concatBin, 'resolveConcatBinary should discover concat.exe on host');
    assert.ok(fs.existsSync(concatBin), 'Discovered concat.exe must exist');
    console.log('  ✅ PASS: concat.exe resolved successfully');

    console.log('--- Test 2: Path Traversal rejected with 400 ---');
    const r1 = await makeRequest('POST', '/api/projects/..%2f../export-concat');
    assert.strictEqual(r1.status, 400);
    const r2 = await makeRequest('POST', '/api/projects/..%2f../open-in-concat');
    assert.strictEqual(r2.status, 400);
    console.log('  ✅ PASS: Path traversal rejected with 400 on both endpoints');

    console.log('--- Test 3: Non-existent project returns 404 ---');
    const r3 = await makeRequest('POST', '/api/projects/non_existent_xyz/export-concat');
    assert.strictEqual(r3.status, 404);
    const r4 = await makeRequest('POST', '/api/projects/non_existent_xyz/open-in-concat');
    assert.strictEqual(r4.status, 404);
    console.log('  ✅ PASS: Non-existent project rejected with 404');

    console.log('--- Test 4: Export demo_project via POST /api/projects/:id/export-concat ---');
    const r5 = await makeRequest('POST', '/api/projects/demo_project/export-concat');
    assert.strictEqual(r5.status, 200);
    assert.strictEqual(r5.body.success, true);
    assert.ok(r5.body.concatJsonPath);
    assert.ok(fs.existsSync(r5.body.concatJsonPath));
    assert.strictEqual(r5.body.timelines.length, 2);
    console.log(`  ✅ PASS: Exported successfully with ${r5.body.timelines.length} timelines`);

    console.log('--- Test 5: Open in Concat with Smart Auto-Sync ---');
    const r6 = await makeRequest('POST', '/api/projects/demo_project/open-in-concat');
    assert.strictEqual(r6.status, 200);
    assert.strictEqual(r6.body.success, true);
    assert.ok(r6.body.pid, 'Must return spawned child process PID');
    assert.strictEqual(r6.body.autoSynced, false, 'Already exported, should not need re-sync');
    console.log(`  ✅ PASS: Concat launched successfully with PID ${r6.body.pid}`);

    console.log('\n🎉 ALL CONCAT API TESTS PASSED!\n');
  } finally {
    server.close();
  }
}

runTests().catch(err => {
  console.error('❌ Test failed:', err);
  process.exit(1);
});
