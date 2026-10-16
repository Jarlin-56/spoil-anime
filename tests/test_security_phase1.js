/**
 * Security Regression Test Suite for Phase 1 (SEC-01 & SEC-02)
 *
 * Tests:
 * 1. SEC-01 Safe TTS preview execution:
 *    - Valid inputs work and return audio
 *    - Injection payloads in text/voice/rate cannot execute arbitrary code
 *    - Voice allowlist enforcement
 *    - Speech rate validation (-50% to +100%)
 *    - Text length limits (1 - 500 chars)
 *    - Temporary file cleanup on success and error
 * 2. SEC-02 Centralized project path validation:
 *    - Valid project IDs are accepted
 *    - Path traversal sequences (.., ../.., ..\, encoded) are rejected with 400
 *    - Forbidden prefixes (_temp, _uploads, .git) are rejected with 400
 *    - Windows reserved names (CON, PRN, AUX, NUL) are rejected with 400
 *    - Rejection is enforced consistently across all :id routes
 *    - Root and external directory deletion cannot occur
 *    - Non-existent projects return 404
 *    - Legitimate project deletion only deletes the isolated target project
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const { app, validateProjectId, resolveValidatedProjectDir } = require('../server/server');

const BASE_DIR = path.resolve(__dirname, '..');
const PROJECTS_DIR = path.join(BASE_DIR, 'projects');
const TEMP_DIR = path.join(PROJECTS_DIR, '_temp');

let server;
let baseUrl;

function makeRequest(method, endpoint, body = null) {
  return new Promise((resolve, reject) => {
    const url = new URL(baseUrl);
    const options = {
      method,
      hostname: url.hostname,
      port: url.port,
      path: endpoint,
      headers: {}
    };

    let payload = null;
    if (body !== null) {
      payload = typeof body === 'string' ? body : JSON.stringify(body);
      options.headers['Content-Type'] = 'application/json';
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

async function runTests() {
  console.log('====================================================');
  console.log('🧪 Starting Security Regression Tests (Phase 1)');
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

  // Start ephemeral server
  await new Promise((resolve) => {
    server = app.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      baseUrl = `http://127.0.0.1:${port}`;
      console.log(`[Test Server] Running on ${baseUrl}\n`);
      resolve();
    });
  });

  try {
    // ----------------------------------------------------
    // SEC-02: Unit Tests on Centralized Project Path Validation
    // ----------------------------------------------------
    console.log('--- Suite 1: SEC-02 Path Validation Unit Checks ---');

    await test('validateProjectId accepts standard alphanumeric, hyphen, underscore IDs', () => {
      assert.strictEqual(validateProjectId('demo_project'), true);
      assert.strictEqual(validateProjectId('my-recap-1'), true);
      assert.strictEqual(validateProjectId('Project123_test'), true);
    });

    await test('validateProjectId rejects path traversal attempts', () => {
      assert.strictEqual(validateProjectId('..'), false);
      assert.strictEqual(validateProjectId('../..'), false);
      assert.strictEqual(validateProjectId('../../projects'), false);
      assert.strictEqual(validateProjectId('..\\..\\windows'), false);
      assert.strictEqual(validateProjectId('../etc/passwd'), false);
    });

    await test('validateProjectId rejects root, hidden, and internal prefixes', () => {
      assert.strictEqual(validateProjectId('.'), false);
      assert.strictEqual(validateProjectId('.git'), false);
      assert.strictEqual(validateProjectId('_temp'), false);
      assert.strictEqual(validateProjectId('_uploads'), false);
      assert.strictEqual(validateProjectId(''), false);
      assert.strictEqual(validateProjectId('   '), false);
      assert.strictEqual(validateProjectId(null), false);
      assert.strictEqual(validateProjectId(undefined), false);
    });

    await test('validateProjectId rejects special characters, spaces, and Windows devices', () => {
      assert.strictEqual(validateProjectId('proj/sub'), false);
      assert.strictEqual(validateProjectId('proj\\sub'), false);
      assert.strictEqual(validateProjectId('proj:id'), false);
      assert.strictEqual(validateProjectId('proj*id'), false);
      assert.strictEqual(validateProjectId('proj id'), false);
      assert.strictEqual(validateProjectId('CON'), false);
      assert.strictEqual(validateProjectId('prn'), false);
      assert.strictEqual(validateProjectId('aux'), false);
      assert.strictEqual(validateProjectId('NUL'), false);
    });

    await test('resolveValidatedProjectDir bounds targets strictly within PROJECTS_DIR', () => {
      const canonicalBase = path.resolve(PROJECTS_DIR);
      const valid = resolveValidatedProjectDir('demo_project');
      assert.strictEqual(valid, path.join(canonicalBase, 'demo_project'));

      // Traversal returns null
      assert.strictEqual(resolveValidatedProjectDir('..'), null);
      assert.strictEqual(resolveValidatedProjectDir('../..'), null);
      assert.strictEqual(resolveValidatedProjectDir('../../'), null);
      assert.strictEqual(resolveValidatedProjectDir('..\\projects'), null);
    });

    // ----------------------------------------------------
    // SEC-02: Route-Wide Traversal Rejection Across Endpoints
    // ----------------------------------------------------
    console.log('\n--- Suite 2: SEC-02 Centralized Route-Wide Protection ---');

    const traversalVectors = ['..', '../..', '..%2f..', '%2e%2e', '_temp', '_uploads', '.git', 'CON', 'invalid/path'];

    for (const vector of traversalVectors) {
      await test(`GET /api/projects/${encodeURIComponent(vector)} is rejected with HTTP 400`, async () => {
        const res = await makeRequest('GET', `/api/projects/${encodeURIComponent(vector)}`);
        assert.strictEqual(res.statusCode, 400);
        assert.strictEqual(res.body.success, false);
      });

      await test(`DELETE /api/projects/${encodeURIComponent(vector)} is rejected with HTTP 400`, async () => {
        const res = await makeRequest('DELETE', `/api/projects/${encodeURIComponent(vector)}`);
        assert.strictEqual(res.statusCode, 400);
        assert.strictEqual(res.body.success, false);
      });
    }

    await test('POST /api/projects/:id/detect-op-ed rejects traversal with 400', async () => {
      const res = await makeRequest('POST', '/api/projects/..%2f../detect-op-ed', {});
      assert.strictEqual(res.statusCode, 400);
    });

    await test('POST /api/projects/:id/extract rejects traversal with 400', async () => {
      const res = await makeRequest('POST', '/api/projects/..%2f../extract', {});
      assert.strictEqual(res.statusCode, 400);
    });

    await test('POST /api/projects/:id/transcribe rejects traversal with 400', async () => {
      const res = await makeRequest('POST', '/api/projects/..%2f../transcribe', {});
      assert.strictEqual(res.statusCode, 400);
    });

    await test('POST /api/projects/:id/script rejects traversal with 400', async () => {
      const res = await makeRequest('POST', '/api/projects/..%2f../script', { script: {} });
      assert.strictEqual(res.statusCode, 400);
    });

    await test('POST /api/projects/:id/tts rejects traversal with 400', async () => {
      const res = await makeRequest('POST', '/api/projects/..%2f../tts', {});
      assert.strictEqual(res.statusCode, 400);
    });

    await test('POST /api/projects/:id/render rejects traversal with 400', async () => {
      const res = await makeRequest('POST', '/api/projects/..%2f../render', {});
      assert.strictEqual(res.statusCode, 400);
    });

    await test('POST /api/projects/:id/trim-frames rejects traversal with 400', async () => {
      const res = await makeRequest('POST', '/api/projects/..%2f../trim-frames', { frameIds: [1] });
      assert.strictEqual(res.statusCode, 400);
    });

    // ----------------------------------------------------
    // SEC-02: Project Deletion Confinement & Integrity
    // ----------------------------------------------------
    console.log('\n--- Suite 3: SEC-02 Safe Project Deletion Confinement ---');

    await test('DELETE cannot remove projects root or workspace directory', async () => {
      const res = await makeRequest('DELETE', '/api/projects/..');
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(fs.existsSync(PROJECTS_DIR), true);
      assert.strictEqual(fs.existsSync(BASE_DIR), true);
    });

    await test('DELETE non-existent project returns 404 without altering disk', async () => {
      const res = await makeRequest('DELETE', '/api/projects/non_existent_project_9999');
      assert.strictEqual(res.statusCode, 404);
      assert.strictEqual(res.body.success, false);
    });

    await test('Legitimate project deletion only deletes the authorized target project', async () => {
      // Create temporary project fixture
      const fixtureId = `test_fixture_${Date.now()}`;
      const fixtureDir = path.join(PROJECTS_DIR, fixtureId);
      fs.mkdirSync(fixtureDir, { recursive: true });
      fs.writeFileSync(path.join(fixtureDir, 'marker.txt'), 'safe fixture', 'utf-8');

      assert.strictEqual(fs.existsSync(fixtureDir), true);

      const delRes = await makeRequest('DELETE', `/api/projects/${fixtureId}`);
      assert.strictEqual(delRes.statusCode, 200);
      assert.strictEqual(delRes.body.success, true);
      assert.strictEqual(fs.existsSync(fixtureDir), false);
      assert.strictEqual(fs.existsSync(PROJECTS_DIR), true);
    });

    await test('Valid existing project demo_project reading remains functional', async () => {
      const res = await makeRequest('GET', '/api/projects/demo_project');
      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(res.body.success, true);
      assert.strictEqual(res.body.project.id, 'demo_project');
    });

    // ----------------------------------------------------
    // SEC-01: TTS Preview Safe Execution & Code Injection Protection
    // ----------------------------------------------------
    console.log('\n--- Suite 4: SEC-01 TTS Preview Security & Validation ---');

    await test('Normal TTS preview generates audio successfully and cleans up temp file', async () => {
      const beforeFiles = fs.existsSync(TEMP_DIR) ? fs.readdirSync(TEMP_DIR) : [];
      const res = await makeRequest('POST', '/api/tts-preview', {
        text: 'ทดสอบเสียงพากย์สปอยล์',
        voice: 'th-TH-NiwatNeural',
        rate: '+5%'
      });

      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(res.headers['content-type'], 'audio/mpeg');
      assert.ok(res.rawBuffer.length > 1000);

      // Verify temp file cleanup
      const afterFiles = fs.existsSync(TEMP_DIR) ? fs.readdirSync(TEMP_DIR) : [];
      assert.strictEqual(afterFiles.length, beforeFiles.length);
    });

    await test('Code injection payloads in text are treated strictly as text and cannot execute code', async () => {
      const markerPath = path.join(TEMP_DIR, 'pwned_marker.txt');
      if (fs.existsSync(markerPath)) fs.unlinkSync(markerPath);

      const injectionPayloads = [
        `'; import sys, os; open(r'${markerPath}', 'w').write('pwned'); sys.exit(0); '`,
        `'''\nimport os\nos.system('echo pwned > "${markerPath}"')\n'''`,
        `"; os.system('calc.exe'); "`
      ];

      for (const payload of injectionPayloads) {
        const res = await makeRequest('POST', '/api/tts-preview', {
          text: payload,
          voice: 'th-TH-NiwatNeural',
          rate: '+0%'
        });

        // The request must either succeed (synthesizing text safely) or fail safely
        // But the injected Python code MUST NEVER execute
        assert.strictEqual(fs.existsSync(markerPath), false, 'Injection marker file must not be created!');
      }
    });

    await test('Invalid voice parameter is rejected with HTTP 400', async () => {
      const res = await makeRequest('POST', '/api/tts-preview', {
        text: 'ทดสอบ',
        voice: 'en-US-GuyNeural; import os',
        rate: '+5%'
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.body.success, false);
      assert.ok(res.body.error.includes('voice'));
    });

    await test('Invalid speech rate parameter is rejected with HTTP 400', async () => {
      const badRates = ['+500%', '-100%', 'fast', '+5%; os.system()', '1000%'];
      for (const badRate of badRates) {
        const res = await makeRequest('POST', '/api/tts-preview', {
          text: 'ทดสอบ',
          voice: 'th-TH-NiwatNeural',
          rate: badRate
        });
        assert.strictEqual(res.statusCode, 400);
        assert.strictEqual(res.body.success, false);
      }
    });

    await test('Oversized text (> 500 characters) is rejected with HTTP 400', async () => {
      const longText = 'ก'.repeat(501);
      const res = await makeRequest('POST', '/api/tts-preview', {
        text: longText,
        voice: 'th-TH-NiwatNeural',
        rate: '+5%'
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.body.success, false);
      assert.ok(res.body.error.includes('Text length'));
    });

    await test('Empty or non-string text is rejected with HTTP 400', async () => {
      const res1 = await makeRequest('POST', '/api/tts-preview', { text: '' });
      assert.strictEqual(res1.statusCode, 400);

      const res2 = await makeRequest('POST', '/api/tts-preview', { text: '   ' });
      assert.strictEqual(res2.statusCode, 400);

      const res3 = await makeRequest('POST', '/api/tts-preview', {});
      assert.strictEqual(res3.statusCode, 400);
    });

    await test('Arbitrary client filesystem paths in request body are ignored', async () => {
      const arbitraryDest = path.join(BASE_DIR, 'arbitrary_overwrite.txt');
      if (fs.existsSync(arbitraryDest)) fs.unlinkSync(arbitraryDest);

      const res = await makeRequest('POST', '/api/tts-preview', {
        text: 'ทดสอบการส่ง path',
        voice: 'th-TH-NiwatNeural',
        rate: '+5%',
        output_path: arbitraryDest,
        tempFile: arbitraryDest,
        file: arbitraryDest
      });

      assert.strictEqual(fs.existsSync(arbitraryDest), false, 'Client-supplied output path must be ignored');
    });

    await test('Sensitive server details or stack traces are not leaked in error responses', async () => {
      const res = await makeRequest('POST', '/api/tts-preview', {
        text: 'ทดสอบ',
        voice: 'invalid-voice',
        rate: '+5%'
      });
      assert.strictEqual(res.statusCode, 400);
      const bodyStr = JSON.stringify(res.body);
      assert.strictEqual(bodyStr.includes('Error:'), false);
      assert.strictEqual(bodyStr.includes('at '), false);
      assert.strictEqual(bodyStr.includes(BASE_DIR), false);
    });

  } finally {
    // Teardown server
    if (server) {
      server.close();
    }
  }

  console.log('\n====================================================');
  console.log(`📊 Security Test Summary: ${passed} Passed, ${failed} Failed`);
  console.log('====================================================');

  if (failed > 0) {
    process.exit(1);
  }
}

runTests().catch((err) => {
  console.error('Fatal test error:', err);
  process.exit(1);
});
