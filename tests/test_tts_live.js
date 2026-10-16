/**
 * Live Edge-TTS Integration Smoke Test Suite (Phase 3.2)
 *
 * Requirements:
 * 1. Live synthesis smoke test using a short, harmless Thai sentence and supported voice.
 * 2. Verify upstream Edge-TTS service accepts request (edge-tts 7.3.1, no 403 error).
 * 3. Verify non-empty playable audio file is generated.
 * 4. FFprobe confirms valid audio stream (mp3) and positive duration.
 * 5. Temporary output files are cleaned up on both success and failure.
 * 6. Existing preview endpoint (/api/tts-preview) returns HTTP 200 success response.
 * 7. Verify SentenceBoundary metadata extraction and integration with voice timeline.
 * 8. Transparent reporting: live upstream errors are reported honestly, never falsely passed.
 */

const assert = require('assert');
const http = require('http');
const path = require('path');
const fs = require('fs');
const { spawnSync } = require('child_process');
const { app, resolvePythonInterpreter } = require('../server/server');

const BASE_DIR = path.resolve(__dirname, '..');
const PROJECTS_DIR = path.join(BASE_DIR, 'projects');
const TEMP_DIR = path.join(PROJECTS_DIR, '_temp');
const PY_EXE = resolvePythonInterpreter();

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
  console.log('🎙️ Starting Live Edge-TTS Integration Smoke Tests (Phase 3.2)');
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

  // 1. Verify runtime Python environment & installed package
  console.log('--- Suite 1: Runtime Environment & Package Verification ---');
  await test('Application configured Python interpreter imports edge-tts 7.3.1', async () => {
    const res = spawnSync(PY_EXE, ['-c', 'import edge_tts; print(edge_tts.__version__)'], {
      encoding: 'utf-8'
    });
    assert.strictEqual(res.status, 0, `Python invocation failed: ${res.stderr}`);
    const version = res.stdout.trim();
    console.log(`     Detected edge-tts version: ${version}`);
    assert.strictEqual(version, '7.3.1', `Expected edge-tts version 7.3.1, found ${version}`);
  });

  // Start ephemeral server
  await new Promise((resolve) => {
    server = app.listen(0, '127.0.0.1', () => {
      serverPort = server.address().port;
      console.log(`[TTS Test Server] Listening on http://127.0.0.1:${serverPort}\n`);
      resolve();
    });
  });

  try {
    // 2. HTTP Preview Endpoint Live Smoke Test
    console.log('--- Suite 2: HTTP Preview Endpoint Live Smoke Test ---');
    await test('POST /api/tts-preview returns 200, valid playable MP3, and verifies duration with FFprobe', async () => {
      const beforeFiles = fs.existsSync(TEMP_DIR) ? fs.readdirSync(TEMP_DIR) : [];

      const res = await makeRequest('POST', '/api/tts-preview', {
        text: 'สวัสดีครับ นี่คือการทดสอบเสียงพากย์อนิเมะ',
        voice: 'th-TH-NiwatNeural',
        rate: '+5%'
      });

      assert.strictEqual(
        res.statusCode,
        200,
        `Expected HTTP 200, got ${res.statusCode}: ${JSON.stringify(res.body)}`
      );
      assert.strictEqual(res.headers['content-type'], 'audio/mpeg');
      assert.ok(res.rawBuffer.length > 2000, `Audio buffer too small: ${res.rawBuffer.length} bytes`);

      // Inspect with FFprobe
      const probeTmp = path.join(TEMP_DIR, `probe_live_preview_${Date.now()}.mp3`);
      fs.writeFileSync(probeTmp, res.rawBuffer);

      try {
        const probeResult = spawnSync(
          'ffprobe',
          [
            '-v', 'error',
            '-show_entries', 'stream=codec_name:format=duration',
            '-of', 'json',
            probeTmp
          ],
          { encoding: 'utf-8' }
        );

        assert.strictEqual(probeResult.status, 0, `FFprobe failed: ${probeResult.stderr}`);
        const meta = JSON.parse(probeResult.stdout);
        assert.ok(meta.streams && meta.streams.length > 0, 'No audio stream detected by FFprobe');
        assert.strictEqual(meta.streams[0].codec_name, 'mp3', 'Audio codec is not MP3');

        const duration = parseFloat(meta.format.duration);
        assert.ok(!isNaN(duration) && duration > 0.5, `Invalid or non-positive audio duration: ${duration}s`);
        console.log(`     Synthesized audio verified: ${res.rawBuffer.length} bytes, ${duration.toFixed(3)}s duration`);
      } finally {
        if (fs.existsSync(probeTmp)) {
          fs.unlinkSync(probeTmp);
        }
      }

      // Verify temp cleanup of endpoint internal file
      const afterFiles = fs.existsSync(TEMP_DIR) ? fs.readdirSync(TEMP_DIR) : [];
      assert.strictEqual(
        afterFiles.length,
        beforeFiles.length,
        `Temp files were not cleanly removed. Before: ${beforeFiles.length}, After: ${afterFiles.length}`
      );
    });

    await test('POST /api/tts-preview cleans up temp file when input is invalid or fails', async () => {
      const beforeFiles = fs.existsSync(TEMP_DIR) ? fs.readdirSync(TEMP_DIR) : [];

      const res = await makeRequest('POST', '/api/tts-preview', {
        text: '',
        voice: 'th-TH-NiwatNeural'
      });

      assert.strictEqual(res.statusCode, 400);

      const afterFiles = fs.existsSync(TEMP_DIR) ? fs.readdirSync(TEMP_DIR) : [];
      assert.strictEqual(afterFiles.length, beforeFiles.length);
    });

    // 3. SentenceBoundary Metadata & Pipeline Integration
    console.log('\n--- Suite 3: SentenceBoundary Metadata Extraction & Pipeline Integration ---');
    await test('edge-tts captures SentenceBoundary timestamps and integrates into timeline schema', async () => {
      const pyScript = `
import asyncio
import json
import os
import sys
from pathlib import Path

# Add core directory to sys.path
BASE_DIR = Path(r"${BASE_DIR.replace(/\\/g, '\\\\')}")
sys.path.insert(0, str(BASE_DIR / "core"))

from tts_engine import synthesize_line_cached

async def run_check():
    cache_dir = BASE_DIR / "projects" / "_temp" / "test_tts_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_audio = cache_dir / "test_boundary.mp3"
    
    test_text = "นี่คือประโยคแรกของการทดสอบ\\nและนี่คือประโยคที่สอง"
    dur, boundaries = await synthesize_line_cached(
        text=test_text,
        voice="th-TH-NiwatNeural",
        rate="+5%",
        output_path=str(out_audio),
        cache_dir=cache_dir
    )
    
    result = {
        "duration": dur,
        "boundaries_count": len(boundaries),
        "boundaries": boundaries,
        "audio_exists": out_audio.exists(),
        "audio_size": out_audio.stat().st_size if out_audio.exists() else 0
    }
    print(json.dumps(result))

asyncio.run(run_check())
`;

      const pyResult = spawnSync(PY_EXE, ['-c', pyScript], {
        encoding: 'utf-8',
        cwd: BASE_DIR
      });

      assert.strictEqual(pyResult.status, 0, `Python boundary script failed: ${pyResult.stderr}`);
      const output = JSON.parse(pyResult.stdout.trim());

      assert.ok(output.audio_exists, 'Synthesized audio does not exist');
      assert.ok(output.audio_size > 1000, `Audio size too small: ${output.audio_size}`);
      assert.ok(output.duration > 0.5, `Duration too short: ${output.duration}`);
      assert.ok(output.boundaries_count >= 1, `Expected SentenceBoundary count >= 1, got ${output.boundaries_count}`);

      const firstB = output.boundaries[0];
      assert.ok(firstB.text, 'SentenceBoundary missing text');
      assert.ok(typeof firstB.offset === 'number', 'SentenceBoundary offset must be number');
      assert.ok(typeof firstB.duration === 'number', 'SentenceBoundary duration must be number');

      console.log(`     SentenceBoundary confirmed: ${output.boundaries_count} segments captured, first duration: ${firstB.duration}s`);

      // Cleanup test cache
      const testCacheDir = path.join(TEMP_DIR, 'test_tts_cache');
      if (fs.existsSync(testCacheDir)) {
        fs.rmSync(testCacheDir, { recursive: true, force: true });
      }
    });

  } finally {
    if (server) {
      server.close();
    }
  }

  console.log('\n====================================================');
  console.log(`📊 Live TTS Smoke Test Summary: ${passed} Passed, ${failed} Failed`);
  console.log('====================================================');

  if (failed > 0) {
    process.exit(1);
  }
}

runTests().catch((err) => {
  console.error('[Live TTS Test Runner Error]', err);
  process.exit(1);
});
