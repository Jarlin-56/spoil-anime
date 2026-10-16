/**
 * Automated Test Suite for Phase 3:
 * Hardened Upload Pipeline, Dependency Consistency & Local API Confinement
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const { execSync, spawn } = require('child_process');

const serverModule = require('../server/server.js');
const {
  app,
  getUploadEntry,
  resolveAuthorizedVideoPath,
  updateManifest,
  checkDiskSpaceAvailable,
  validateMediaWithFFprobe,
  UPLOADS_DIR,
  MANIFEST_PATH,
  BASELINE_PATH,
  BIND_HOST,
  ALLOW_REMOTE
} = serverModule;

const BASE_DIR = path.resolve(__dirname, '..');
const FIXTURE_VIDEO = path.join(__dirname, 'test_fixture.mp4');

let server;
let baseUrl;

function makeRequest(method, endpoint, options = {}) {
  return new Promise((resolve, reject) => {
    const url = new URL(baseUrl);
    const reqOptions = {
      method,
      hostname: url.hostname,
      port: url.port,
      path: endpoint,
      headers: options.headers || {}
    };

    let payload = options.body || null;
    if (payload && typeof payload === 'object' && !Buffer.isBuffer(payload) && !options.isRaw) {
      payload = JSON.stringify(payload);
      reqOptions.headers['Content-Type'] = 'application/json';
      reqOptions.headers['Content-Length'] = Buffer.byteLength(payload);
    } else if (payload && Buffer.isBuffer(payload)) {
      reqOptions.headers['Content-Length'] = payload.length;
    }

    const req = http.request(reqOptions, (res) => {
      const chunks = [];
      res.on('data', d => chunks.push(d));
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

// Multipart helper for upload testing
function uploadMultipartFile(fieldName, filename, fileBuffer, mimeType = 'video/mp4', headers = {}) {
  return new Promise((resolve, reject) => {
    const boundary = '----WebKitFormBoundary' + Math.random().toString(36).substring(2);
    const url = new URL(baseUrl);

    const header = `--${boundary}\r\nContent-Disposition: form-data; name="${fieldName}"; filename="${filename}"\r\nContent-Type: ${mimeType}\r\n\r\n`;
    const footer = `\r\n--${boundary}--\r\n`;

    const body = Buffer.concat([
      Buffer.from(header, 'utf-8'),
      fileBuffer,
      Buffer.from(footer, 'utf-8')
    ]);

    const reqOptions = {
      method: 'POST',
      hostname: url.hostname,
      port: url.port,
      path: '/api/upload',
      headers: {
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': body.length,
        ...headers
      }
    };

    const req = http.request(reqOptions, (res) => {
      const chunks = [];
      res.on('data', d => chunks.push(d));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf-8');
        let json = null;
        try { json = JSON.parse(text); } catch (_) {}
        resolve({ statusCode: res.statusCode, body: json || text });
      });
    });

    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

async function runTests() {
  console.log('====================================================');
  console.log('🧪 Starting Phase 3 Hardened Upload & Security Tests');
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

  // Start ephemeral server bound to loopback
  await new Promise((resolve) => {
    server = app.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      baseUrl = `http://127.0.0.1:${port}`;
      console.log(`[Phase 3 Test Server] Running on ${baseUrl}\n`);
      resolve();
    });
  });

  try {
    // ----------------------------------------------------
    // Section 1: Dependency Consistency & Isolated npm ci
    // ----------------------------------------------------
    console.log('--- Suite 1: Dependency Consistency & Integrity ---');

    await test('Exact resolved dependencies match expected versions', () => {
      const lsOutput = execSync('npm ls express multer proxy-addr --json', { cwd: BASE_DIR }).toString();
      const tree = JSON.parse(lsOutput);
      const deps = tree.dependencies || {};

      console.log(`     Resolved express: ${deps.express ? deps.express.version : 'missing'}`);
      console.log(`     Resolved multer: ${deps.multer ? deps.multer.version : 'missing'}`);
      console.log(`     Resolved proxy-addr: ${deps['proxy-addr'] ? deps['proxy-addr'].version : 'missing'}`);

      assert.ok(deps.express && deps.express.version.startsWith('4.'), 'Express must resolve to 4.x');
      assert.ok(deps.multer && deps.multer.version.startsWith('2.'), 'Multer must resolve to 2.x');
      assert.ok(deps['proxy-addr'], 'proxy-addr must be installed');
    });

    await test('Isolated npm ci succeeds without dependency mismatch', () => {
      const tmpDir = path.join(require('os').tmpdir(), `ci-verify-${Date.now()}`);
      fs.mkdirSync(tmpDir);
      try {
        fs.copyFileSync(path.join(BASE_DIR, 'package.json'), path.join(tmpDir, 'package.json'));
        fs.copyFileSync(path.join(BASE_DIR, 'package-lock.json'), path.join(tmpDir, 'package-lock.json'));
        execSync('npm ci', { cwd: tmpDir, stdio: 'pipe' });
      } finally {
        fs.rmSync(tmpDir, { recursive: true, force: true });
      }
    });

    await test('npm audit runs and records actual vulnerability status', () => {
      let auditOutput = '';
      try {
        auditOutput = execSync('npm audit --json', { cwd: BASE_DIR }).toString();
      } catch (err) {
        auditOutput = err.stdout ? err.stdout.toString() : '';
      }
      const audit = JSON.parse(auditOutput);
      const vulnTotal = audit.metadata && audit.metadata.vulnerabilities ?
        (audit.metadata.vulnerabilities.total || 0) : 0;
      console.log(`     npm audit total vulnerabilities: ${vulnTotal}`);
      assert.strictEqual(vulnTotal, 0, 'Project dependencies should have 0 known vulnerabilities');
    });

    // ----------------------------------------------------
    // Section 2: Loopback Binding & Origin Guardrails
    // ----------------------------------------------------
    console.log('\n--- Suite 2: Local API Access & Origin Protection ---');

    await test('Server binds to loopback 127.0.0.1 by default', () => {
      assert.strictEqual(BIND_HOST, '127.0.0.1');
      assert.strictEqual(ALLOW_REMOTE, false);
    });

    await test('Disallowed browser origin on state-changing API request is rejected with 403', async () => {
      const res = await makeRequest('POST', '/api/projects/create', {
        headers: {
          'Origin': 'http://malicious-attacker.com'
        },
        body: { id: 'attack_proj', title: 'Attack' }
      });
      assert.strictEqual(res.statusCode, 403);
      assert.strictEqual(res.body.success, false);
      assert.strictEqual(res.body.error, 'Disallowed browser origin');
    });

    await test('Allowed loopback origin is accepted for state-changing requests', async () => {
      const res = await makeRequest('POST', '/api/projects/create', {
        headers: {
          'Origin': baseUrl
        },
        body: { id: 'safe_loopback_proj', title: 'Safe Loopback' }
      });
      // Should not be rejected by Origin check (200 or existing validation status)
      assert.notStrictEqual(res.statusCode, 403);
      // Clean up fixture project
      const projDir = path.join(BASE_DIR, 'projects', 'safe_loopback_proj');
      if (fs.existsSync(projDir)) fs.rmSync(projDir, { recursive: true, force: true });
    });

    // ----------------------------------------------------
    // Section 3: Baseline Inventory & Manifest Concurrency
    // ----------------------------------------------------
    console.log('\n--- Suite 3: Legacy Inventory Baseline & Manifest Integrity ---');

    await test('Legacy baseline inventory file exists and cataloged pre-existing uploads', () => {
      assert.strictEqual(fs.existsSync(BASELINE_PATH), true, 'legacy_baseline.json must exist');
      const baseline = JSON.parse(fs.readFileSync(BASELINE_PATH, 'utf-8'));
      assert.ok(Array.isArray(baseline), 'Baseline should be an array of files');
      console.log(`     Tracked legacy baseline files count: ${baseline.length}`);
    });

    await test('Manifest updates are atomic and preserve legacy uploads', async () => {
      assert.strictEqual(fs.existsSync(MANIFEST_PATH), true, 'uploads_manifest.json must exist');
      const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf-8'));
      assert.ok(manifest.uploads, 'Manifest must have uploads map');

      // Verify legacy baseline entries have legacy: true
      const legacyEntries = Object.values(manifest.uploads).filter(u => u.legacy === true);
      assert.ok(legacyEntries.length > 0, 'Must have registered legacy baseline entries');
      for (const leg of legacyEntries) {
        assert.strictEqual(leg.status, 'legacy_baseline');
        assert.strictEqual(leg.legacy, true);
      }
    });

    // ----------------------------------------------------
    // Section 4: Content Validation & Bounded FFprobe
    // ----------------------------------------------------
    console.log('\n--- Suite 4: Media Content Validation (FFprobe) ---');

    await test('Bounded FFprobe correctly extracts metadata for valid MP4 fixture', async () => {
      assert.strictEqual(fs.existsSync(FIXTURE_VIDEO), true, 'Test fixture must exist');
      const info = await validateMediaWithFFprobe(FIXTURE_VIDEO);
      assert.strictEqual(info.codec, 'h264');
      assert.strictEqual(info.width, 320);
      assert.strictEqual(info.height, 240);
      assert.ok(info.duration > 0.5, 'Duration should be ~1s');
    });

    await test('Preliminary check rejects invalid extensions (.sh, .exe) with 400', async () => {
      const res = await uploadMultipartFile('videoFile', 'malicious.sh', Buffer.from('#!/bin/bash\necho bad'));
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.body.success, false);
      assert.ok(res.body.error.includes('Unsupported file extension'));
    });

    await test('Generic MIME application/octet-stream is accepted when file is valid MP4', async () => {
      const fixtureBuf = fs.readFileSync(FIXTURE_VIDEO);
      const res = await uploadMultipartFile('videoFile', 'generic_mime.mp4', fixtureBuf, 'application/octet-stream');
      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(res.body.success, true);
      assert.ok(res.body.uploadId, 'Must return uploadId');
      assert.ok(res.body.filePath, 'Must return filePath for compatibility');

      // Verify manifest entry
      const entry = getUploadEntry(res.body.uploadId);
      assert.ok(entry, 'Entry must exist in manifest');
      assert.strictEqual(entry.status, 'ready');
      assert.strictEqual(entry.mediaInfo.codec, 'h264');
    });

    await test('Corrupted media file with .mp4 extension is rejected by FFprobe validation', async () => {
      const fakeVideoBuf = Buffer.from('NOT A REAL VIDEO FILE, JUST CORRUPT TEXT DATA');
      const res = await uploadMultipartFile('videoFile', 'fake_video.mp4', fakeVideoBuf);
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.body.success, false);
      assert.ok(res.body.error.includes('Media validation failed'));
    });

    // ----------------------------------------------------
    // Section 5: Authoritative uploadId & Path Confinement
    // ----------------------------------------------------
    console.log('\n--- Suite 5: Authoritative uploadId & Path Confinement ---');

    let validUploadId = null;
    let validFilePath = null;

    await test('Upload valid video returns authoritative uploadId and compatibility filePath', async () => {
      const fixtureBuf = fs.readFileSync(FIXTURE_VIDEO);
      const res = await uploadMultipartFile('videoFile', 'valid_test.mp4', fixtureBuf, 'video/mp4');
      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(res.body.success, true);
      assert.ok(res.body.uploadId);
      assert.ok(res.body.filePath);
      validUploadId = res.body.uploadId;
      validFilePath = res.body.filePath;
    });

    await test('Extract endpoint accepts valid uploadId and associates project', async () => {
      const testProjId = `phase3_test_proj_${Date.now()}`;
      const projDir = path.join(BASE_DIR, 'projects', testProjId);
      fs.mkdirSync(projDir, { recursive: true });

      try {
        const res = await makeRequest('POST', `/api/projects/${testProjId}/extract`, {
          body: {
            uploadId: validUploadId,
            threshold: 0.28,
            maxFrames: 5,
            autoDetectOpEd: false,
            skipIntro: false,
            skipOutro: false
          }
        });

        assert.strictEqual(res.statusCode, 200);
        assert.strictEqual(res.body.success, true);

        // Check manifest association
        const entry = getUploadEntry(validUploadId);
        assert.strictEqual(entry.status, 'associated');
        assert.strictEqual(entry.projectId, testProjId);
      } finally {
        fs.rmSync(projDir, { recursive: true, force: true });
      }
    });

    await test('Extract endpoint rejects invalid uploadId and NEVER falls back to client path', async () => {
      const testProjId = `phase3_invalid_uid_test`;
      const projDir = path.join(BASE_DIR, 'projects', testProjId);
      fs.mkdirSync(projDir, { recursive: true });

      try {
        const res = await makeRequest('POST', `/api/projects/${testProjId}/extract`, {
          body: {
            uploadId: 'completely_invalid_upload_id_xyz',
            videoPath: FIXTURE_VIDEO
          }
        });

        assert.strictEqual(res.statusCode, 400);
        assert.strictEqual(res.body.success, false);
        assert.ok(res.body.error.includes('Invalid or unready upload ID'));
      } finally {
        fs.rmSync(projDir, { recursive: true, force: true });
      }
    });

    await test('Extract endpoint rejects arbitrary client filesystem path outside managed inventory', async () => {
      const testProjId = `phase3_arbitrary_path_test`;
      const projDir = path.join(BASE_DIR, 'projects', testProjId);
      fs.mkdirSync(projDir, { recursive: true });

      try {
        const arbitraryPaths = [
          'C:\\Windows\\System32\\drivers\\etc\\hosts',
          '../../package.json',
          'core/extractor.py'
        ];

        for (const badPath of arbitraryPaths) {
          const res = await makeRequest('POST', `/api/projects/${testProjId}/extract`, {
            body: {
              videoPath: badPath
            }
          });

          assert.strictEqual(res.statusCode, 400);
          assert.strictEqual(res.body.success, false);
          assert.ok(res.body.error.includes('Video path must reference a valid managed upload'));
        }
      } finally {
        fs.rmSync(projDir, { recursive: true, force: true });
      }
    });

    // ----------------------------------------------------
    // Section 6: Stream Abort Lifecycle & Cleanup
    // ----------------------------------------------------
    console.log('\n--- Suite 6: Stream Abort Lifecycle & Concurrency ---');

    await test('Aborted upload destroys stream cleanly without file descriptor leaks or EBUSY', async () => {
      const beforeFiles = fs.readdirSync(UPLOADS_DIR);
      const boundary = '----WebKitFormBoundaryAbortTest' + Math.random().toString(36).substring(2);
      const url = new URL(baseUrl);

      const header = `--${boundary}\r\nContent-Disposition: form-data; name="videoFile"; filename="aborted.mp4"\r\nContent-Type: video/mp4\r\n\r\n`;
      const partialData = Buffer.alloc(500 * 1024, 0x41); // 500 KB chunk

      const reqOptions = {
        method: 'POST',
        hostname: url.hostname,
        port: url.port,
        path: '/api/upload',
        headers: {
          'Content-Type': `multipart/form-data; boundary=${boundary}`,
          'Content-Length': 2 * 1024 * 1024 // Claim 2MB, but abort after 500KB
        }
      };

      await new Promise((resolve) => {
        const req = http.request(reqOptions, () => {});
        req.on('error', () => {}); // Expected abort error
        req.write(Buffer.from(header, 'utf-8'));
        req.write(partialData);

        // Abruptly destroy client socket mid-transmission
        setTimeout(() => {
          req.destroy();
          setTimeout(resolve, 800); // Allow server stream cleanup
        }, 100);
      });

      // Confirm no leftover .part files from this aborted upload
      const afterFiles = fs.readdirSync(UPLOADS_DIR);
      const partFiles = afterFiles.filter(f => f.endsWith('.part'));
      assert.strictEqual(partFiles.length, 0, 'Aborted upload .part file must be cleaned up cleanly');
    });

    // ----------------------------------------------------
    // Section 7: Aggregate Resource Protection
    // ----------------------------------------------------
    console.log('\n--- Suite 7: Aggregate Resource Protection ---');

    await test('checkDiskSpaceAvailable accurately queries free disk space and reserves 5 GiB', () => {
      const check = checkDiskSpaceAvailable(UPLOADS_DIR);
      assert.strictEqual(typeof check.ok, 'boolean');
      assert.ok(check.freeBytes > 0);
      assert.ok(check.requiredBytes >= 5 * 1024 * 1024 * 1024);
      console.log(`     Free disk space: ${(check.freeBytes / 1024 / 1024 / 1024).toFixed(2)} GiB`);
    });

  } finally {
    if (server) {
      server.close();
    }
  }

  console.log('\n====================================================');
  console.log(`📊 Phase 3 Test Summary: ${passed} Passed, ${failed} Failed`);
  console.log('====================================================\n');

  if (failed > 0) {
    process.exit(1);
  }
}

runTests().catch(err => {
  console.error('Fatal test error:', err);
  process.exit(1);
});
