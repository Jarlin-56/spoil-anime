#!/usr/bin/env node
/**
 * Canonical Verification Pipeline Runner (Phase 5)
 * Anime Recap Pipeline — สปอย
 *
 * Provides a reliable, repeatable verification workflow distinguishing:
 *   --fast      : Fast deterministic syntax, import, dependency, and audit checks
 *   --security  : Full security regression suite (Phase 1, 3.2, 3, 4, and 5 failure recovery)
 *   --media     : Python unit tests & real FFmpeg/FFprobe media integration tests
 *   --live      : Network-dependent live Edge-TTS integration test (never masqueraded as pass)
 *   --all       : Runs --fast, --security, --media, AND --live (explicitly documented)
 *   (default)   : Runs deterministic gates (--fast, --security, --media)
 */

const { spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const { resolvePythonInterpreter } = require('../server/server');

const BASE_DIR = path.resolve(__dirname, '..');
const PY_EXE = resolvePythonInterpreter();
const npmBin = process.platform === 'win32' ? 'npm.cmd' : 'npm';

// Parse CLI flags
const args = process.argv.slice(2);
const runAll = args.includes('--all');
const runFast = runAll || args.includes('--fast') || args.length === 0;
const runSecurity = runAll || args.includes('--security') || args.length === 0;
const runMedia = runAll || args.includes('--media') || args.length === 0;
const runLive = runAll || args.includes('--live');

const suiteResults = [];

function recordResult(name, category, status, durationSec, detail = '') {
  suiteResults.push({ name, category, status, durationSec, detail });
}

function runStep(name, category, cmd, cmdArgs, env = {}) {
  process.stdout.write(`  ▶ [${category}] ${name}... `);
  const startTime = Date.now();
  const res = spawnSync(cmd, cmdArgs, {
    cwd: BASE_DIR,
    env: { ...process.env, ...env },
    encoding: 'utf-8',
    windowsHide: true,
    shell: process.platform === 'win32' && typeof cmd === 'string' && cmd.endsWith('.cmd')
  });
  const dur = ((Date.now() - startTime) / 1000).toFixed(2);

  if (res.status === 0) {
    console.log(`✅ PASS (${dur}s)`);
    recordResult(name, category, 'PASSED', dur);
    return true;
  } else {
    console.log(`❌ FAIL (${dur}s) (exit code: ${res.status})`);
    const output = (res.stderr || res.stdout || '').trim();
    if (output) {
      console.error(`\n--- [Output for ${name}] ---`);
      console.error(output.slice(-2000));
      console.error(`----------------------------\n`);
    }
    recordResult(name, category, 'FAILED', dur, `Exit code ${res.status}`);
    return false;
  }
}

async function main() {
  console.log('================================================================');
  console.log('🚀 Anime Recap Pipeline — Canonical Verification Workflow (Phase 5)');
  console.log('================================================================');
  console.log(`📂 Working Directory   : ${BASE_DIR}`);
  console.log(`🐍 Python Interpreter  : ${PY_EXE}`);
  console.log(`📦 Node Version        : ${process.version}`);
  console.log(`⚙️  Active Suite Flags  : fast=${runFast}, security=${runSecurity}, media=${runMedia}, live=${runLive}`);
  if (runAll) {
    console.log(`⚠️  Notice: --all flag is active. Network-dependent live TTS tests will be executed.`);
  } else if (!runLive) {
    console.log(`🔒 Deterministic mode: Network-dependent live TTS suite is excluded by default.`);
  }
  console.log('================================================================\n');

  let anyDeterministicFailed = false;
  let liveSuiteFailed = false;

  // -------------------------------------------------------------
  // A. Fast Deterministic Checks
  // -------------------------------------------------------------
  if (runFast) {
    console.log('--- A. Fast Deterministic Checks ---');

    // 1. Node.js syntax checks
    const nodeFilesToCheck = [
      path.join('server', 'server.js'),
      path.join('server', 'provenance.js'),
      path.join('scripts', 'verify_pipeline.js'),
      path.join('tests', 'test_security_phase1.js'),
      path.join('tests', 'test_host_security.js'),
      path.join('tests', 'test_upload_phase3.js'),
      path.join('tests', 'test_render_endpoint.js'),
      path.join('tests', 'test_trim_frames.js'),
      path.join('tests', 'test_failure_recovery.js'),
      path.join('tests', 'test_provenance_readiness.js')
    ];
    for (const nf of nodeFilesToCheck) {
      const ok = runStep(`Node syntax: ${nf}`, 'Fast', 'node', ['--check', nf]);
      if (!ok) anyDeterministicFailed = true;
    }

    // 2. Python syntax compilation
    const okPyCompile = runStep('Python syntax compilation (py_compile core & tests)', 'Fast', PY_EXE, [
      '-m', 'py_compile',
      path.join('core', 'timing_engine.py'),
      path.join('core', 'motion_compositor.py'),
      path.join('core', 'extractor.py'),
      path.join('core', 'audio_extractor.py'),
      path.join('core', 'op_ed_detector.py'),
      path.join('core', 'script_generator.py'),
      path.join('core', 'tts_engine.py'),
      path.join('tests', 'test_av_synchronization.py'),
      path.join('tests', 'test_subtitle_font_rendering.py'),
      path.join('tests', 'test_timing_pipeline.py'),
      path.join('tests', 'test_narration_generation.py')
    ]);
    if (!okPyCompile) anyDeterministicFailed = true;

    // 3. Python core module imports
    const pyImportScript = `
import sys
for mod in ['timing_engine', 'motion_compositor', 'extractor', 'audio_extractor', 'op_ed_detector', 'script_generator', 'tts_engine']:
    __import__(mod)
print('All core Python modules imported successfully')
`;
    const okPyImports = runStep('Python core module imports', 'Fast', PY_EXE, ['-c', pyImportScript], {
      PYTHONPATH: path.join(BASE_DIR, 'core')
    });
    if (!okPyImports) anyDeterministicFailed = true;

    // 4. Python pip dependency consistency
    const okPipCheck = runStep('Python pip dependency consistency (pip check)', 'Fast', PY_EXE, ['-m', 'pip', 'check']);
    if (!okPipCheck) anyDeterministicFailed = true;

    // 5. Node dependency consistency
    const okNpmLs = runStep('Node dependency consistency (npm ls express multer proxy-addr)', 'Fast', npmBin, ['ls', 'express', 'multer', 'proxy-addr']);
    if (!okNpmLs) anyDeterministicFailed = true;

    // 6. Node vulnerability audit
    const okNpmAudit = runStep('Node vulnerability audit (npm audit)', 'Fast', npmBin, ['audit']);
    if (!okNpmAudit) anyDeterministicFailed = true;

    console.log('');
  }

  // -------------------------------------------------------------
  // B. Security Regression Suite
  // -------------------------------------------------------------
  if (runSecurity) {
    console.log('--- B. Security & Failure-Recovery Regression Suite ---');

    const securitySuites = [
      { name: 'SEC Phase 1 (Traversal & Parameter Hardening)', file: path.join('tests', 'test_security_phase1.js') },
      { name: 'SEC Phase 3.2 (Host-Header & Loopback Protection)', file: path.join('tests', 'test_host_security.js') },
      { name: 'SEC Phase 3 (Upload Validation & Storage Confinement)', file: path.join('tests', 'test_upload_phase3.js') },
      { name: 'SEC Route Protection (Render Endpoint Checks)', file: path.join('tests', 'test_render_endpoint.js') },
      { name: 'SEC Phase 4 (Trim-Frames Canonical Route & Range)', file: path.join('tests', 'test_trim_frames.js') },
      { name: 'SEC Phase 5 (Trim Journal, Checkpoints & Failure Recovery)', file: path.join('tests', 'test_failure_recovery.js') },
      { name: 'PROV Phase 6 (Asset Lineage, Rights Review & Readiness)', file: path.join('tests', 'test_provenance_readiness.js') }
    ];

    for (const s of securitySuites) {
      const ok = runStep(s.name, 'Security', 'node', [s.file]);
      if (!ok) anyDeterministicFailed = true;
    }

    console.log('');
  }

  // -------------------------------------------------------------
  // C. Media Integration Suite
  // -------------------------------------------------------------
  if (runMedia) {
    console.log('--- C. Media Integration & A/V Synchronization Suite ---');

    // Run Python unittest discovery for media integration tests
    const okMediaUnittest = runStep('Python unittest discover tests (A/V Sync, Subtitles, Timing, Narration)', 'Media', PY_EXE, [
      '-m', 'unittest', 'discover', 'tests'
    ]);
    if (!okMediaUnittest) anyDeterministicFailed = true;

    console.log('');
  }

  // -------------------------------------------------------------
  // D. Live External Integration (Network Dependent)
  // -------------------------------------------------------------
  if (runLive) {
    console.log('--- D. Live External Integration (Network Dependent) ---');

    // Pre-flight check: Verify outbound connectivity or live flag
    process.stdout.write('  ▶ [Live] Live Edge-TTS smoke test (tests/test_tts_live.js)... ');
    const startTime = Date.now();
    const res = spawnSync('node', [path.join('tests', 'test_tts_live.js')], {
      cwd: BASE_DIR,
      encoding: 'utf-8',
      windowsHide: true
    });
    const dur = ((Date.now() - startTime) / 1000).toFixed(2);

    if (res.status === 0) {
      console.log(`✅ PASS (${dur}s)`);
      recordResult('Live Edge-TTS smoke test', 'Live', 'PASSED', dur);
    } else {
      console.log(`❌ FAIL (${dur}s) (exit code: ${res.status})`);
      const output = (res.stderr || res.stdout || '').trim();
      if (output) {
        console.error(`\n--- [Output for Live Edge-TTS smoke test] ---`);
        console.error(output.slice(-2000));
        console.error(`---------------------------------------------\n`);
      }
      recordResult('Live Edge-TTS smoke test', 'Live', 'FAILED', dur, `Exit code ${res.status}`);
      liveSuiteFailed = true;
    }
    console.log('');
  }

  // -------------------------------------------------------------
  // Summary Report
  // -------------------------------------------------------------
  console.log('================================================================');
  console.log('📊 Verification Summary Report');
  console.log('================================================================');
  console.log(
    `${'Suite / Check'.padEnd(52)} | ${'Category'.padEnd(10)} | ${'Status'.padEnd(8)} | ${'Time'.padEnd(7)}`
  );
  console.log('-'.repeat(84));

  let passedCount = 0;
  let failedCount = 0;
  let skippedCount = 0;

  for (const r of suiteResults) {
    const statusIcon = r.status === 'PASSED' ? '✅' : (r.status === 'FAILED' ? '❌' : '⏭️');
    console.log(
      `${r.name.padEnd(52)} | ${r.category.padEnd(10)} | ${statusIcon} ${r.status.padEnd(5)} | ${r.durationSec}s`
    );
    if (r.status === 'PASSED') passedCount++;
    else if (r.status === 'FAILED') failedCount++;
    else if (r.status === 'SKIPPED') skippedCount++;
  }
  console.log('='.repeat(84));
  console.log(`Total Checks: ${suiteResults.length} | Passed: ${passedCount} | Failed: ${failedCount} | Skipped: ${skippedCount}\n`);

  if (anyDeterministicFailed) {
    console.error('❌ VERIFICATION FAILED: One or more deterministic checks failed.');
    process.exit(1);
  }

  if (runLive && liveSuiteFailed) {
    console.error('❌ LIVE INTEGRATION FAILED: Network-dependent tests failed.');
    process.exit(1);
  }

  console.log('🎉 ALL VERIFICATION CHECKS PASSED SUCCESSFULLY!');
  process.exit(0);
}

main().catch(err => {
  console.error('[Verification Runner Fatal Error]', err);
  process.exit(1);
});
