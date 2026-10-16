/**
 * Trim-Frames Route Consolidation & Reliability Test Suite (Phase 4)
 *
 * Verifies:
 * 1. Exactly one canonical POST /api/projects/:id/trim-frames route exists in the Express stack.
 * 2. Range-based trimming (introRange, outroRange, customStart/customEnd, introSec, outroSec) operates accurately.
 * 3. frameIds trimming removes specified frames from manifest, storyboard, and disk.
 * 4. Path confinement prevents deletion outside the authorized project directory.
 * 5. Unknown frame IDs, inverted/malformed ranges, and non-existent projects fail safely with consistent HTTP status codes.
 * 6. Frontend contract compatibility (web/app.js payload and response format).
 * 7. Manifest entries stay strictly synchronized with remaining files.
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const { app, resolveValidatedProjectDir } = require('../server/server');

const BASE_DIR = path.resolve(__dirname, '..');
const PROJECTS_DIR = path.join(BASE_DIR, 'projects');

let server;
let serverPort;

function makeRequest(method, endpoint, body = null) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: '127.0.0.1',
      port: serverPort,
      path: endpoint,
      method,
      headers: {
        Host: `127.0.0.1:${serverPort}`
      }
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
          body: text,
          json
        });
      });
    });

    req.on('error', reject);
    if (payload !== null) {
      req.write(payload);
    }
    req.end();
  });
}

// Helper to create synthetic project fixture with fake frame image files
function createSyntheticProject(projectId, frameCount = 6) {
  const pDir = path.join(PROJECTS_DIR, projectId);
  const framesDir = path.join(pDir, 'frames');
  fs.mkdirSync(framesDir, { recursive: true });

  const manifest = [];
  const storyboard = [];

  for (let i = 1; i <= frameCount; i++) {
    const frameId = `frame_${String(i).padStart(4, '0')}`;
    const filename = `${frameId}.jpg`;
    const filePath = path.join(framesDir, filename);
    // Write a dummy 1x1 image or synthetic byte content
    fs.writeFileSync(filePath, `SYNTHETIC_FRAME_DATA_${i}_${frameId}`, 'utf-8');

    const item = {
      id: frameId,
      filename,
      path: path.relative(BASE_DIR, filePath).replace(/\\/g, '/'),
      index: i,
      timestamp: (i - 1) * 30.0, // 0s, 30s, 60s, 90s, 120s, 150s
      time_str: `00:${String((i - 1) * 30).padStart(2, '0')}`
    };
    manifest.push(item);
    storyboard.push({ ...item });
  }

  fs.writeFileSync(path.join(pDir, 'frames_manifest.json'), JSON.stringify(manifest, null, 2), 'utf-8');
  fs.writeFileSync(path.join(framesDir, 'frames_manifest.json'), JSON.stringify(manifest, null, 2), 'utf-8');
  fs.writeFileSync(path.join(pDir, 'storyboard.json'), JSON.stringify(storyboard, null, 2), 'utf-8');

  return { pDir, framesDir, manifest, storyboard };
}

function cleanupProject(projectId) {
  const pDir = path.join(PROJECTS_DIR, projectId);
  if (fs.existsSync(pDir)) {
    fs.rmSync(pDir, { recursive: true, force: true });
  }
}

async function runTests() {
  console.log('====================================================');
  console.log('🧪 Starting Trim-Frames Route Regression Tests (Phase 4)');
  console.log('====================================================\n');

  server = http.createServer(app);
  await new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      serverPort = server.address().port;
      console.log(`[Trim Test Server] Running on http://127.0.0.1:${serverPort}\n`);
      resolve();
    });
  });

  let totalTests = 0;
  let passedTests = 0;

  async function test(name, fn) {
    totalTests++;
    try {
      await fn();
      console.log(`  ✅ PASS: ${name}`);
      passedTests++;
    } catch (err) {
      console.error(`  ❌ FAIL: ${name}`);
      console.error(`     Error: ${err.message}`);
      throw err;
    }
  }

  try {
    // ---------------------------------------------------------------
    // Suite 1: Route Stack Uniqueness
    // ---------------------------------------------------------------
    console.log('--- Suite 1: Single Canonical Route Registration ---');

    await test('Only one POST /api/projects/:id/trim-frames handler is registered in Express', () => {
      const routes = [];
      app._router.stack.forEach((middleware) => {
        if (middleware.route) {
          const path = middleware.route.path;
          const methods = Object.keys(middleware.route.methods);
          if (methods.includes('post') && path === '/api/projects/:id/trim-frames') {
            routes.push(middleware.route);
          }
        }
      });
      assert.strictEqual(routes.length, 1, `Expected exactly 1 trim-frames route, found ${routes.length}`);
    });

    // ---------------------------------------------------------------
    // Suite 2: Explicit frameIds Trimming
    // ---------------------------------------------------------------
    console.log('\n--- Suite 2: Explicit frameIds Trimming & File Deletion ---');

    const testProj1 = 'test_trim_frameids_' + Date.now();
    createSyntheticProject(testProj1, 5);

    await test('Trimming with frameIds removes target frames from disk and manifest', async () => {
      const res = await makeRequest('POST', `/api/projects/${testProj1}/trim-frames`, {
        frameIds: ['frame_0002', 'frame_0004']
      });

      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(res.json.success, true);
      assert.strictEqual(res.json.removed, 2);
      assert.strictEqual(res.json.removedCount, 2);
      assert.strictEqual(res.json.kept, 3);
      assert.strictEqual(res.json.remainingCount, 3);
      assert.strictEqual(res.json.deletedFilesCount, 2);

      const pDir = path.join(PROJECTS_DIR, testProj1);
      // Verify deleted files no longer exist on disk
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0002.jpg')), false);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0004.jpg')), false);

      // Verify kept files STILL exist on disk
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0001.jpg')), true);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0003.jpg')), true);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0005.jpg')), true);

      // Verify manifest was updated and re-indexed (1, 2, 3)
      const updatedManifest = JSON.parse(fs.readFileSync(path.join(pDir, 'frames_manifest.json'), 'utf-8'));
      assert.strictEqual(updatedManifest.length, 3);
      assert.deepStrictEqual(updatedManifest.map(f => f.id), ['frame_0001', 'frame_0003', 'frame_0005']);
      assert.deepStrictEqual(updatedManifest.map(f => f.index), [1, 2, 3]);

      // Verify storyboard was updated
      const updatedStoryboard = JSON.parse(fs.readFileSync(path.join(pDir, 'storyboard.json'), 'utf-8'));
      assert.strictEqual(updatedStoryboard.length, 3);
      assert.deepStrictEqual(updatedStoryboard.map(f => f.id), ['frame_0001', 'frame_0003', 'frame_0005']);
    });

    cleanupProject(testProj1);

    // ---------------------------------------------------------------
    // Suite 3: Range-Based Trimming
    // ---------------------------------------------------------------
    console.log('\n--- Suite 3: Range-Based Trimming ---');

    const testProj2 = 'test_trim_range_' + Date.now();
    // 6 frames at timestamps: 0s, 30s, 60s, 90s, 120s, 150s
    createSyntheticProject(testProj2, 6);

    await test('introRange "00:00-01:00" removes frames within intro window', async () => {
      // 0s and 30s are within 0 to 60s
      const res = await makeRequest('POST', `/api/projects/${testProj2}/trim-frames`, {
        introRange: '00:00-00:45'
      });

      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(res.json.success, true);
      // Frame 1 (0s) and Frame 2 (30s) are <= 45s (+ 0.5s margin)
      assert.strictEqual(res.json.removed, 2);
      assert.strictEqual(res.json.remainingCount, 4);

      const pDir = path.join(PROJECTS_DIR, testProj2);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0001.jpg')), false);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0002.jpg')), false);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0003.jpg')), true);
    });

    cleanupProject(testProj2);

    const testProj3 = 'test_trim_outro_' + Date.now();
    createSyntheticProject(testProj3, 6); // 0s, 30s, 60s, 90s, 120s, 150s

    await test('outroRange "02:00-03:00" removes frames within outro window', async () => {
      // 120s (02:00) and 150s (02:30) fall within 120s-180s
      const res = await makeRequest('POST', `/api/projects/${testProj3}/trim-frames`, {
        outroRange: '02:00-03:00'
      });

      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(res.json.success, true);
      assert.strictEqual(res.json.removed, 2);
      assert.strictEqual(res.json.remainingCount, 4);

      const pDir = path.join(PROJECTS_DIR, testProj3);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0005.jpg')), false);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0006.jpg')), false);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0001.jpg')), true);
    });

    cleanupProject(testProj3);

    const testProj4 = 'test_trim_custom_' + Date.now();
    createSyntheticProject(testProj4, 6);

    await test('customStart and customEnd remove targeted middle range', async () => {
      // customStart: 50, customEnd: 100 -> removes frame_0003 (60s) and frame_0004 (90s)
      const res = await makeRequest('POST', `/api/projects/${testProj4}/trim-frames`, {
        customStart: 50,
        customEnd: 100
      });

      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(res.json.success, true);
      assert.strictEqual(res.json.removed, 2);
      assert.strictEqual(res.json.remainingCount, 4);

      const pDir = path.join(PROJECTS_DIR, testProj4);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0003.jpg')), false);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0004.jpg')), false);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0001.jpg')), true);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0006.jpg')), true);
    });

    cleanupProject(testProj4);

    // ---------------------------------------------------------------
    // Suite 4: Boundary & Confinement Protection
    // ---------------------------------------------------------------
    console.log('\n--- Suite 4: Boundary & Confinement Protection ---');

    const testProj5 = 'test_trim_sec_' + Date.now();
    createSyntheticProject(testProj5, 3);

    await test('Path traversal in project ID is rejected with 400', async () => {
      const res = await makeRequest('POST', '/api/projects/..%2f..%2fetc/trim-frames', {
        frameIds: ['frame_0001']
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.json.success, false);
    });

    await test('Non-existent project returns 404', async () => {
      const res = await makeRequest('POST', '/api/projects/non_existent_project_99999/trim-frames', {
        frameIds: ['frame_0001']
      });
      assert.strictEqual(res.statusCode, 404);
      assert.strictEqual(res.json.success, false);
    });

    await test('Unknown frameIds are rejected with 400', async () => {
      const res = await makeRequest('POST', `/api/projects/${testProj5}/trim-frames`, {
        frameIds: ['non_existent_frame_id', 'frame_0001']
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.json.success, false);
      assert.ok(res.json.error.includes('Unknown frame IDs'));

      // Ensure no files were deleted when validation failed
      const pDir = path.join(PROJECTS_DIR, testProj5);
      assert.strictEqual(fs.existsSync(path.join(pDir, 'frames', 'frame_0001.jpg')), true);
    });

    await test('Inverted introRange (start >= end) is rejected with 400', async () => {
      const res = await makeRequest('POST', `/api/projects/${testProj5}/trim-frames`, {
        introRange: '02:00-01:00'
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.json.success, false);
    });

    await test('Malformed range strings are rejected with 400', async () => {
      const res = await makeRequest('POST', `/api/projects/${testProj5}/trim-frames`, {
        introRange: 'invalid_range_no_hyphen'
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.json.success, false);
    });

    await test('Inverted customStart >= customEnd is rejected with 400', async () => {
      const res = await makeRequest('POST', `/api/projects/${testProj5}/trim-frames`, {
        customStart: 100,
        customEnd: 50
      });
      assert.strictEqual(res.statusCode, 400);
      assert.strictEqual(res.json.success, false);
    });

    await test('Malicious frame path outside project cannot be deleted', async () => {
      const pDir = path.join(PROJECTS_DIR, testProj5);
      const manifestPath = path.join(pDir, 'frames_manifest.json');
      const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));

      // Create an external canary file in BASE_DIR (outside project)
      const canaryFile = path.join(BASE_DIR, 'canary_external_test.txt');
      fs.writeFileSync(canaryFile, 'CANARY_DONT_DELETE', 'utf-8');

      // Inject malicious path entry into project manifest
      manifest.push({
        id: 'frame_traversal',
        filename: '../../canary_external_test.txt',
        path: '../../canary_external_test.txt',
        index: 99,
        timestamp: 999
      });
      fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), 'utf-8');

      const res = await makeRequest('POST', `/api/projects/${testProj5}/trim-frames`, {
        frameIds: ['frame_traversal']
      });

      assert.strictEqual(res.statusCode, 200);
      // External canary file MUST NOT be unlinked
      assert.strictEqual(fs.existsSync(canaryFile), true);
      assert.strictEqual(fs.readFileSync(canaryFile, 'utf-8'), 'CANARY_DONT_DELETE');

      // Cleanup canary
      fs.unlinkSync(canaryFile);
    });

    cleanupProject(testProj5);

    // ---------------------------------------------------------------
    // Suite 5: Frontend Contract Compatibility
    // ---------------------------------------------------------------
    console.log('\n--- Suite 5: Frontend Contract Compatibility (web/app.js) ---');

    const testProj6 = 'test_trim_fe_' + Date.now();
    createSyntheticProject(testProj6, 4);

    await test('Response format exactly matches web/app.js expectations (success, removed, frames, storyboard)', async () => {
      // web/app.js line 1388: POST /api/projects/${id}/trim-frames { frameIds: [...] }
      // web/app.js line 1395: if (data.success) { ... alert(`ลบ ${data.removed} ภาพ`) }
      const res = await makeRequest('POST', `/api/projects/${testProj6}/trim-frames`, {
        frameIds: ['frame_0001']
      });

      assert.strictEqual(res.statusCode, 200);
      assert.strictEqual(typeof res.json.success, 'boolean');
      assert.strictEqual(res.json.success, true);
      assert.strictEqual(typeof res.json.removed, 'number');
      assert.strictEqual(res.json.removed, 1);
      assert.strictEqual(typeof res.json.remainingCount, 'number');
      assert.strictEqual(res.json.remainingCount, 3);
      assert.ok(Array.isArray(res.json.frames));
      assert.ok(Array.isArray(res.json.storyboard));
    });

    cleanupProject(testProj6);

    console.log('\n====================================================');
    console.log(`📊 Trim-Frames Regression Test Summary: ${passedTests} Passed, 0 Failed`);
    console.log('====================================================\n');

  } finally {
    if (server) {
      server.close();
    }
  }
}

runTests().catch((err) => {
  console.error('\n❌ Suite execution failed with error:', err);
  process.exit(1);
});
