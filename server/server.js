const express = require('express');
const cors = require('cors');
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { spawn, spawnSync } = require('child_process');
const {
  loadProjectProvenance,
  migrateProjectProvenance,
  registerExportProvenance,
  updateAssetProvenance,
  evaluatePublicationReadiness,
  recomputeAndStoreAssetHash,
  verifyAssetContentHash,
  computeFileSha256,
  safeWriteJson,
  RIGHTS_STATUS,
  ASSET_TYPE,
  READINESS_STATUS,
  READINESS_LABELS
} = require('./provenance');

const app = express();
const PORT = process.env.PORT || 3000;

const BASE_DIR = path.resolve(__dirname, '..');
const PROJECTS_DIR = path.join(BASE_DIR, 'projects');
const CORE_DIR = path.join(BASE_DIR, 'core');
const ASSETS_DIR = path.join(BASE_DIR, 'assets');

// Consistent Python interpreter resolution with strict precedence:
// 1. Explicit process.env.PYTHON_PATH (validated, fails clearly if missing/unrunnable)
// 2. process.env.VIRTUAL_ENV interpreter (platform-specific)
// 3. Machine-specific legacy fallback (preserved for backward compatibility)
function resolvePythonInterpreter() {
  if (process.env.PYTHON_PATH) {
    const customPath = process.env.PYTHON_PATH;
    if (path.isAbsolute(customPath) || customPath.includes('/') || customPath.includes('\\')) {
      if (!fs.existsSync(customPath)) {
        throw new Error(`Configured PYTHON_PATH does not exist: ${customPath}`);
      }
    }
    const test = spawnSync(customPath, ['--version'], { encoding: 'utf-8', windowsHide: true });
    if (test.error || test.status !== 0) {
      const detail = test.error ? test.error.message : `exit code ${test.status}`;
      throw new Error(`Configured PYTHON_PATH cannot run (${customPath}): ${detail}`);
    }
    return customPath;
  }

  if (process.env.VIRTUAL_ENV) {
    const venvPython = process.platform === 'win32'
      ? path.join(process.env.VIRTUAL_ENV, 'Scripts', 'python.exe')
      : path.join(process.env.VIRTUAL_ENV, 'bin', 'python');
    if (fs.existsSync(venvPython)) {
      const test = spawnSync(venvPython, ['--version'], { encoding: 'utf-8', windowsHide: true });
      if (!test.error && test.status === 0) {
        return venvPython;
      }
    }
  }

  // 3. Project workspace .venv (preferred over host-global python fallback)
  const projectVenv = process.platform === 'win32'
    ? path.join(BASE_DIR, '.venv', 'Scripts', 'python.exe')
    : path.join(BASE_DIR, '.venv', 'bin', 'python');
  if (fs.existsSync(projectVenv)) {
    const test = spawnSync(projectVenv, ['--version'], { encoding: 'utf-8', windowsHide: true });
    if (!test.error && test.status === 0) {
      return projectVenv;
    }
  }

  const legacyFallback = path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python312', 'python.exe');
  if (fs.existsSync(legacyFallback)) {
    return legacyFallback;
  }

  return 'python';
}

const PYTHON_PATH = resolvePythonInterpreter();

const UPLOADS_DIR = path.join(PROJECTS_DIR, '_uploads');
const MANIFEST_PATH = path.join(UPLOADS_DIR, 'uploads_manifest.json');
const BASELINE_PATH = path.join(UPLOADS_DIR, 'legacy_baseline.json');

// Ensure uploads directory exists
fs.mkdirSync(UPLOADS_DIR, { recursive: true });

// Baseline inventory of pre-existing legacy files & manifest management
function initBaselineAndManifest() {
  let baselineFiles = [];
  if (fs.existsSync(BASELINE_PATH)) {
    try {
      baselineFiles = JSON.parse(fs.readFileSync(BASELINE_PATH, 'utf-8'));
    } catch (e) {
      console.warn('[Baseline] Error reading baseline file:', e.message);
    }
  } else {
    try {
      const diskFiles = fs.readdirSync(UPLOADS_DIR);
      baselineFiles = diskFiles.filter(f => {
        const full = path.join(UPLOADS_DIR, f);
        return fs.statSync(full).isFile() && !f.endsWith('.part') && !f.endsWith('.tmp') && f !== 'uploads_manifest.json' && f !== 'legacy_baseline.json';
      });
      fs.writeFileSync(BASELINE_PATH, JSON.stringify(baselineFiles, null, 2), 'utf-8');
      console.log(`[Baseline] Established baseline inventory with ${baselineFiles.length} pre-existing legacy files.`);
    } catch (e) {
      console.warn('[Baseline] Could not establish initial baseline:', e.message);
    }
  }

  const baselineSet = new Set(baselineFiles);

  let manifest = { version: 1, uploads: {} };
  if (fs.existsSync(MANIFEST_PATH)) {
    try {
      manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf-8'));
      if (!manifest.uploads) manifest.uploads = {};
    } catch (e) {
      console.warn('[Manifest] Error parsing manifest, rebuilding:', e.message);
    }
  }

  // Populate baseline legacy entries if missing in manifest
  for (const bFile of baselineFiles) {
    const full = path.join(UPLOADS_DIR, bFile);
    if (fs.existsSync(full)) {
      const stat = fs.statSync(full);
      const bId = `legacy_${crypto.createHash('sha256').update(bFile).digest('hex').substring(0, 16)}`;
      if (!manifest.uploads[bId]) {
        manifest.uploads[bId] = {
          uploadId: bId,
          filename: bFile,
          storedFilename: bFile,
          relPath: `projects/_uploads/${bFile}`,
          size: stat.size,
          mimeType: 'video/mp4',
          status: 'legacy_baseline',
          uploadedAt: stat.birthtime ? stat.birthtime.toISOString() : new Date().toISOString(),
          legacy: true
        };
      }
    }
  }

  // Reconcile disk files vs manifest
  try {
    const currentDiskFiles = fs.readdirSync(UPLOADS_DIR);
    const now = Date.now();
    const ONE_HOUR_MS = 60 * 60 * 1000;

    for (const dFile of currentDiskFiles) {
      const full = path.join(UPLOADS_DIR, dFile);
      if (!fs.statSync(full).isFile()) continue;
      if (dFile === 'uploads_manifest.json' || dFile === 'legacy_baseline.json') continue;

      if (dFile.endsWith('.part') || dFile.endsWith('.tmp')) {
        const stat = fs.statSync(full);
        if (now - stat.mtimeMs > ONE_HOUR_MS) {
          try {
            fs.unlinkSync(full);
            console.log(`[Reconcile] Cleaned up abandoned temporary file: ${dFile}`);
          } catch (_) {}
        }
        continue;
      }

      const isInBaseline = baselineSet.has(dFile);
      const isInManifest = Object.values(manifest.uploads).some(u => u.storedFilename === dFile);

      if (!isInBaseline && !isInManifest) {
        const stat = fs.statSync(full);
        const reviewId = `review_${crypto.createHash('sha256').update(dFile + stat.mtimeMs).digest('hex').substring(0, 16)}`;
        manifest.uploads[reviewId] = {
          uploadId: reviewId,
          filename: dFile,
          storedFilename: dFile,
          relPath: `projects/_uploads/${dFile}`,
          size: stat.size,
          mimeType: 'application/octet-stream',
          status: 'needs_review',
          uploadedAt: stat.mtime.toISOString(),
          legacy: false,
          needsReview: true
        };
        console.log(`[Reconcile] Preserved unclassified file for manual review: ${dFile} (ID: ${reviewId})`);
      }
    }

    const tmpManifest = path.join(UPLOADS_DIR, `manifest.tmp.${Date.now()}`);
    fs.writeFileSync(tmpManifest, JSON.stringify(manifest, null, 2), 'utf-8');
    fs.renameSync(tmpManifest, MANIFEST_PATH);
  } catch (err) {
    console.warn('[Reconcile] Reconciliation warning:', err.message);
  }

  return manifest;
}

let uploadManifestCache = initBaselineAndManifest();

let manifestQueue = Promise.resolve();
function updateManifest(modifierFn) {
  manifestQueue = manifestQueue.then(async () => {
    try {
      if (fs.existsSync(MANIFEST_PATH)) {
        uploadManifestCache = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf-8'));
      }
    } catch (_) {}
    if (!uploadManifestCache.uploads) uploadManifestCache.uploads = {};

    modifierFn(uploadManifestCache);

    const tmpPath = path.join(UPLOADS_DIR, `manifest.tmp.${Date.now()}.${crypto.randomBytes(4).toString('hex')}`);
    fs.writeFileSync(tmpPath, JSON.stringify(uploadManifestCache, null, 2), 'utf-8');
    fs.renameSync(tmpPath, MANIFEST_PATH);
  }).catch(err => {
    console.error('[Manifest Error]', err);
  });
  return manifestQueue;
}

function getUploadEntry(uploadId) {
  if (!uploadId || !uploadManifestCache || !uploadManifestCache.uploads) return null;
  return uploadManifestCache.uploads[uploadId] || null;
}

function resolveAuthorizedVideoPath(clientVideoPath, pId) {
  if (!clientVideoPath || typeof clientVideoPath !== 'string') return null;

  const byId = getUploadEntry(clientVideoPath);
  if (byId && (byId.status === 'ready' || byId.status === 'associated' || byId.status === 'legacy_baseline')) {
    const p = path.join(UPLOADS_DIR, byId.storedFilename);
    if (fs.existsSync(p)) return p;
  }

  const canonicalPath = path.resolve(clientVideoPath);
  const canonicalUploadsDir = path.resolve(UPLOADS_DIR);

  if (canonicalPath.startsWith(canonicalUploadsDir + path.sep) || canonicalPath === canonicalUploadsDir) {
    const filename = path.basename(canonicalPath);
    const inManifest = Object.values(uploadManifestCache.uploads || {}).some(
      u => u.storedFilename === filename && (u.status === 'ready' || u.status === 'associated' || u.status === 'legacy_baseline')
    );
    if (inManifest && fs.existsSync(canonicalPath)) {
      return canonicalPath;
    }
    return null;
  }

  if (pId) {
    const canonicalProjectDir = path.resolve(path.join(PROJECTS_DIR, pId));
    if (canonicalPath.startsWith(canonicalProjectDir + path.sep)) {
      if (fs.existsSync(canonicalPath)) {
        return canonicalPath;
      }
    }
  }

  return null;
}

// Bounded FFprobe validation (child process argument array, no shell, 10s timeout, SIGKILL)
const SUPPORTED_VIDEO_CODECS = new Set([
  'h264',
  'hevc',
  'vp8',
  'vp9',
  'av1',
  'mpeg4',
  'mjpeg'
]);

function validateMediaWithFFprobe(filePath) {
  return new Promise((resolve, reject) => {
    const args = [
      '-v', 'error',
      '-select_streams', 'v:0',
      '-show_entries', 'stream=codec_name,width,height',
      '-show_entries', 'format=format_name,duration',
      '-of', 'json',
      filePath
    ];

    const proc = spawn('ffprobe', args, {
      shell: false,
      stdio: ['ignore', 'pipe', 'pipe']
    });

    let stdout = '';
    let stderr = '';
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      try { proc.kill('SIGKILL'); } catch (_) {}
      reject(new Error('FFprobe media validation timed out (exceeded 10s limit)'));
    }, 10000);

    proc.stdout.on('data', chunk => stdout += chunk.toString('utf-8'));
    proc.stderr.on('data', chunk => stderr += chunk.toString('utf-8'));

    proc.on('close', (code) => {
      clearTimeout(timer);
      if (timedOut) return;
      if (code !== 0) {
        return reject(new Error(`FFprobe inspection failed with code ${code}: ${stderr || stdout}`));
      }
      try {
        const info = JSON.parse(stdout);
        const streams = info.streams || [];
        const format = info.format || {};

        if (!streams || streams.length === 0) {
          return reject(new Error('No video stream detected in file'));
        }

        const v = streams[0];
        if (!v.codec_name || !v.width || !v.height || v.width <= 0 || v.height <= 0) {
          return reject(new Error('Invalid video dimensions or codec'));
        }

        const codec = v.codec_name.toLowerCase();
        if (!SUPPORTED_VIDEO_CODECS.has(codec)) {
          return reject(new Error(`Unsupported video codec: ${codec}. Supported: ${Array.from(SUPPORTED_VIDEO_CODECS).join(', ')}`));
        }

        resolve({
          codec,
          width: v.width,
          height: v.height,
          duration: parseFloat(format.duration || '0'),
          format: format.format_name
        });
      } catch (err) {
        reject(new Error(`Failed to parse media probe data: ${err.message}`));
      }
    });

    proc.on('error', (err) => {
      clearTimeout(timer);
      reject(new Error(`Failed to execute FFprobe: ${err.message}`));
    });
  });
}

// Resource protection: disk space & concurrent upload bounds
const MAX_CONCURRENT_UPLOADS = parseInt(process.env.MAX_CONCURRENT_UPLOADS || '3', 10);
let activeUploadCount = 0;
const MIN_FREE_DISK_BYTES = 5 * 1024 * 1024 * 1024; // 5 GiB reserve for media processing

function checkDiskSpaceAvailable(targetDir, additionalBytes = 0) {
  try {
    if (typeof fs.statfsSync === 'function') {
      const stats = fs.statfsSync(targetDir);
      const freeBytes = stats.bavail * stats.bsize;
      return {
        ok: freeBytes >= (MIN_FREE_DISK_BYTES + additionalBytes),
        freeBytes,
        requiredBytes: MIN_FREE_DISK_BYTES + additionalBytes
      };
    }
  } catch (err) {
    console.warn('[Storage] Could not query disk space:', err.message);
  }
  return { ok: true, freeBytes: Infinity, requiredBytes: 0 };
}

// Lifecycle-aware Multer storage with abort cleanup
function CustomHardenedStorage() {}

CustomHardenedStorage.prototype._handleFile = function(req, file, cb) {
  if (activeUploadCount >= MAX_CONCURRENT_UPLOADS) {
    const err = new Error('Upload capacity reached. Please wait for active uploads to finish.');
    err.statusCode = 429;
    return cb(err);
  }

  const diskCheck = checkDiskSpaceAvailable(UPLOADS_DIR);
  if (!diskCheck.ok) {
    const err = new Error('Insufficient disk space. Minimum 5 GiB must be reserved for media processing.');
    err.statusCode = 507;
    return cb(err);
  }

  activeUploadCount++;
  const uploadId = crypto.randomUUID();
  const stagedFilename = `staged_${uploadId}.part`;
  const stagedPath = path.join(UPLOADS_DIR, stagedFilename);

  const outStream = fs.createWriteStream(stagedPath);
  let aborted = false;
  let finished = false;
  let bytesWritten = 0;

  file.stream.on('data', chunk => {
    bytesWritten += chunk.length;
  });

  const cleanupStagedFile = () => {
    try {
      if (fs.existsSync(stagedPath)) {
        fs.unlinkSync(stagedPath);
      }
    } catch (_) {
      setTimeout(() => {
        try { if (fs.existsSync(stagedPath)) fs.unlinkSync(stagedPath); } catch (_) {}
      }, 500);
    }
  };

  const onAbortOrClose = () => {
    if (!finished && !aborted) {
      aborted = true;
      activeUploadCount = Math.max(0, activeUploadCount - 1);
      file.stream.destroy();
      outStream.destroy();
      outStream.on('close', () => {
        cleanupStagedFile();
      });
    }
  };

  req.on('aborted', onAbortOrClose);
  req.on('close', () => {
    if (!req.complete) {
      onAbortOrClose();
    }
  });

  outStream.on('error', (err) => {
    if (!aborted) {
      activeUploadCount = Math.max(0, activeUploadCount - 1);
      cleanupStagedFile();
      cb(err);
    }
  });

  outStream.on('finish', () => {
    if (aborted) return;
    finished = true;
    activeUploadCount = Math.max(0, activeUploadCount - 1);
    cb(null, {
      uploadId,
      stagedPath,
      stagedFilename,
      size: bytesWritten,
      cleanup: cleanupStagedFile
    });
  });

  file.stream.pipe(outStream);
};

CustomHardenedStorage.prototype._removeFile = function(req, file, cb) {
  try {
    if (file.stagedPath && fs.existsSync(file.stagedPath)) {
      fs.unlinkSync(file.stagedPath);
    }
    cb(null);
  } catch (err) {
    cb(err);
  }
};

const customHardenedStorage = new CustomHardenedStorage();

const upload = multer({
  storage: customHardenedStorage,
  limits: {
    fileSize: 2 * 1024 * 1024 * 1024, // 2GB max file size
    files: 1,
    fields: 10,
    parts: 20,
    fieldNameSize: 100,
    fieldSize: 1024 * 1024, // 1MB
    fieldNestingDepth: 10,
    fieldArrayIndexLimit: 100
  },
  fileFilter: (req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase();
    const ALLOWED_EXTS = new Set(['.mp4', '.mkv', '.mov', '.avi', '.webm', '.flv', '.ts', '.m4v']);
    if (!ALLOWED_EXTS.has(ext)) {
      const err = new Error(`Unsupported file extension: ${ext}. Supported: ${Array.from(ALLOWED_EXTS).join(', ')}`);
      err.statusCode = 400;
      return cb(err);
    }
    if (!file.mimetype.startsWith('video/') &&
        file.mimetype !== 'application/octet-stream' &&
        file.mimetype !== 'application/x-matroska') {
      const err = new Error(`Invalid MIME type: ${file.mimetype}`);
      err.statusCode = 400;
      return cb(err);
    }
    cb(null, true);
  }
});

// Network binding & Origin / Host validation
const ALLOW_REMOTE = process.env.ALLOW_REMOTE === 'true';
const DEFAULT_HOST = '127.0.0.1';
const CONFIGURED_HOST = process.env.HOST || DEFAULT_HOST;
let BIND_HOST = DEFAULT_HOST;

if (CONFIGURED_HOST !== '127.0.0.1' && CONFIGURED_HOST !== 'localhost') {
  if (ALLOW_REMOTE) {
    BIND_HOST = CONFIGURED_HOST;
    console.warn(`[SECURITY WARNING] Server is binding to non-loopback interface (${BIND_HOST}) because ALLOW_REMOTE=true. Ensure network firewall and access controls are configured.`);
  } else {
    console.warn(`[SECURITY] Non-loopback HOST "${CONFIGURED_HOST}" requested without ALLOW_REMOTE=true. Defaulting to loopback 127.0.0.1.`);
    BIND_HOST = DEFAULT_HOST;
  }
}

// Ensure Express never trusts unverified reverse-proxy headers like X-Forwarded-Host
app.set('trust proxy', false);

function parseHost(rawHost) {
  if (!rawHost || typeof rawHost !== 'string') return null;
  const trimmed = rawHost.trim().toLowerCase();
  if (!trimmed || trimmed.length > 255) return null;

  // Reject invalid control characters, spaces, or URI components
  if (/[\s@\/\\]/.test(trimmed)) return null;

  // Bracketed IPv6: [::1] or [::1]:port
  if (trimmed.startsWith('[')) {
    const closeIdx = trimmed.indexOf(']');
    if (closeIdx === -1) return null;
    const hostname = trimmed.substring(1, closeIdx);
    const rest = trimmed.substring(closeIdx + 1);
    let port = null;
    if (rest.startsWith(':')) {
      port = rest.substring(1);
      if (!/^\d{1,5}$/.test(port) || parseInt(port, 10) > 65535) return null;
    } else if (rest.length > 0) {
      return null;
    }
    return { hostname, port };
  }

  // IPv4 or standard hostname: host or host:port
  const colonCount = (trimmed.match(/:/g) || []).length;
  if (colonCount === 0) {
    return { hostname: trimmed, port: null };
  } else if (colonCount === 1) {
    const [hostname, port] = trimmed.split(':');
    if (!hostname || !port || !/^\d{1,5}$/.test(port) || parseInt(port, 10) > 65535) {
      return null;
    }
    return { hostname, port };
  } else {
    // Bare IPv6 address (e.g., "::1")
    if (trimmed === '::1') {
      return { hostname: '::1', port: null };
    }
    return null;
  }
}

function getAllowedRemoteHosts() {
  const allowed = new Set();
  const configuredHosts = process.env.ALLOWED_HOSTS || '';
  if (configuredHosts) {
    configuredHosts.split(',').forEach(item => {
      const parsed = parseHost(item.trim());
      if (parsed && parsed.hostname) {
        allowed.add(parsed.hostname.toLowerCase());
      }
    });
  }
  if (BIND_HOST && BIND_HOST !== '0.0.0.0' && BIND_HOST !== DEFAULT_HOST) {
    allowed.add(BIND_HOST.toLowerCase());
  }
  return allowed;
}

const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1']);

function isAllowedHost(rawHost, req) {
  if (!rawHost || typeof rawHost !== 'string') return false;

  const parsed = parseHost(rawHost);
  if (!parsed || !parsed.hostname) return false;

  const hostname = parsed.hostname;

  // Expected port check: if port is present in Host header, it must match listening port
  if (parsed.port !== null) {
    const expectedPort = req && req.socket && req.socket.localPort
      ? String(req.socket.localPort)
      : String(PORT);
    if (parsed.port !== expectedPort) {
      return false;
    }
  }

  // Loopback hosts are always permitted
  if (LOOPBACK_HOSTS.has(hostname)) {
    return true;
  }

  // Remote mode explicitly requires host to be in documented allowlist
  if (ALLOW_REMOTE) {
    const remoteAllowed = getAllowedRemoteHosts();
    if (remoteAllowed.has(hostname)) {
      return true;
    }
  }

  return false;
}

function isAllowedOrigin(origin) {
  if (!origin) return true;
  try {
    const parsed = new URL(origin);
    const host = parsed.hostname.toLowerCase();
    if (LOOPBACK_HOSTS.has(host) || host === '[::1]') {
      return true;
    }
    if (ALLOW_REMOTE) {
      const remoteAllowed = getAllowedRemoteHosts();
      if (remoteAllowed.has(host)) {
        return true;
      }
    }
    return false;
  } catch (_) {
    return false;
  }
}

// 1. Strict Host-Header Validation Guard (RFC 9110 / DNS-Rebinding Defense)
// Must execute BEFORE all static routes, CORS, and API handlers.
app.use((req, res, next) => {
  const rawHost = req.headers.host;
  if (!rawHost || !isAllowedHost(rawHost, req)) {
    return res.status(400).json({ success: false, error: 'Invalid or disallowed Host header' });
  }
  next();
});

app.use(cors({
  origin: (origin, callback) => {
    if (!origin || isAllowedOrigin(origin)) {
      callback(null, true);
    } else {
      callback(null, false);
    }
  }
}));

// State-changing request origin & host guard
app.use((req, res, next) => {
  const origin = req.headers.origin;
  const method = req.method;

  if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method) && req.path.startsWith('/api/')) {
    if (origin && !isAllowedOrigin(origin)) {
      return res.status(403).json({ success: false, error: 'Disallowed browser origin' });
    }
  }
  next();
});

app.use(express.json({ limit: '50mb' }));

// Global Path Traversal Guard for API routes (SEC-02)
app.use('/api', (req, res, next) => {
  try {
    const rawUrl = req.originalUrl || req.url;
    let decodedUrl = rawUrl;
    try {
      decodedUrl = decodeURIComponent(rawUrl);
    } catch (_) {}

    if (rawUrl.includes('..') || decodedUrl.includes('..') || rawUrl.includes('%2e%2e') || rawUrl.includes('%2E%2E')) {
      return res.status(400).json({ success: false, error: 'Invalid project ID' });
    }
  } catch (_) {
    return res.status(400).json({ success: false, error: 'Invalid project ID' });
  }
  next();
});

app.use(express.static(path.join(BASE_DIR, 'web')));
app.use('/projects', express.static(PROJECTS_DIR));
app.use('/assets', express.static(ASSETS_DIR));

// Helper to run python script with UTF-8
function runPythonScript(args, cwd = BASE_DIR) {
  return new Promise((resolve, reject) => {
    let pyExe;
    try {
      pyExe = resolvePythonInterpreter();
    } catch (err) {
      return reject(err);
    }
    const env = { ...process.env, PYTHONIOENCODING: 'utf-8' };
    const proc = spawn(pyExe, args, { cwd, env });
    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data) => {
      stdout += data.toString('utf-8');
      console.log(`[Python] ${data.toString('utf-8').trim()}`);
    });

    proc.stderr.on('data', (data) => {
      stderr += data.toString('utf-8');
      console.error(`[Python Err] ${data.toString('utf-8').trim()}`);
    });

    proc.on('close', (code) => {
      if (code === 0) {
        resolve(stdout);
      } else {
        reject(new Error(`Process exited with code ${code}: ${stderr || stdout}`));
      }
    });
  });
}

// ==========================================
// Concat NLE Desktop Resolver (Step 2)
// ==========================================
function resolveConcatBinary() {
  if (process.env.CONCAT_BIN) {
    if (fs.existsSync(process.env.CONCAT_BIN)) {
      return process.env.CONCAT_BIN;
    }
  }

  const localAppData = process.env.LOCALAPPDATA || '';
  if (localAppData) {
    const defaultInstall = path.join(localAppData, 'Programs', 'Concat', 'concat.exe');
    if (fs.existsSync(defaultInstall)) {
      return defaultInstall;
    }
  }

  const programFiles = process.env.ProgramFiles || 'C:\\Program Files';
  const pfInstall = path.join(programFiles, 'Concat', 'concat.exe');
  if (fs.existsSync(pfInstall)) {
    return pfInstall;
  }

  const whichCmd = process.platform === 'win32' ? 'where' : 'which';
  const test = spawnSync(whichCmd, ['concat.exe'], { encoding: 'utf-8', windowsHide: true });
  if (!test.error && test.status === 0 && test.stdout) {
    const firstLine = test.stdout.split(/\r?\n/)[0].trim();
    if (firstLine && fs.existsSync(firstLine)) {
      return firstLine;
    }
  }

  return null;
}

// ==========================================
// Centralized Security & Validation (SEC-01 & SEC-02)
// ==========================================

const ALLOWED_TTS_VOICES = new Set([
  'th-TH-NiwatNeural',
  'th-TH-PremwadeeNeural'
]);
const TTS_RATE_REGEX = /^[+-]?\d{1,3}%$/;
const TTS_PITCH_REGEX = /^[+-]?\d{1,2}Hz$/;
const ALLOWED_ACTION_TYPES = new Set(['normal', 'battle', 'shock', 'drama']);
const ALLOWED_VOICE_DIRECTIONS = new Set(['conversational', 'suspenseful', 'energetic', 'surprised', 'dramatic', 'dry_humor']);
const MAX_PREVIEW_TEXT_LENGTH = 500;
const TTS_TIMEOUT_MS = 15000;
const MAX_AUDIO_BYTES = 10 * 1024 * 1024; // 10MB

const PROJECT_ID_REGEX = /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$/;
const WINDOWS_RESERVED_NAMES = new Set([
  'con', 'prn', 'aux', 'nul',
  'com1', 'com2', 'com3', 'com4', 'com5', 'com6', 'com7', 'com8', 'com9',
  'lpt1', 'lpt2', 'lpt3', 'lpt4', 'lpt5', 'lpt6', 'lpt7', 'lpt8', 'lpt9'
]);

function validateProjectId(projectId) {
  if (!projectId || typeof projectId !== 'string') return false;
  if (!PROJECT_ID_REGEX.test(projectId)) return false;
  if (projectId.startsWith('_') || projectId.startsWith('.')) return false;
  if (WINDOWS_RESERVED_NAMES.has(projectId.toLowerCase())) return false;
  return true;
}

function resolveValidatedProjectDir(projectId) {
  if (!validateProjectId(projectId)) return null;

  const baseProjectsDir = path.resolve(PROJECTS_DIR);
  const targetDir = path.resolve(baseProjectsDir, projectId);

  // Robust path boundary check: targetDir must be an immediate child of baseProjectsDir
  const relative = path.relative(baseProjectsDir, targetDir);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative) || relative !== projectId) {
    return null;
  }

  // Account for symbolic links or filesystem conditions escaping the authorized directory
  if (fs.existsSync(targetDir)) {
    try {
      const realBase = fs.realpathSync(baseProjectsDir);
      const realTarget = fs.realpathSync(targetDir);
      const realRel = path.relative(realBase, realTarget);
      if (!realRel || realRel.startsWith('..') || path.isAbsolute(realRel) || path.dirname(realRel) !== '.') {
        return null;
      }
      if (realRel.toLowerCase() !== projectId.toLowerCase()) {
        return null;
      }
    } catch (_) {
      return null;
    }
  }

  return targetDir;
}

// Centralized Express param interceptor for all :id routes
app.param('id', (req, res, next, id) => {
  const validatedDir = resolveValidatedProjectDir(id);
  if (!validatedDir) {
    return res.status(400).json({ success: false, error: 'Invalid project ID' });
  }
  req.projectDir = validatedDir;
  req.projectId = id;
  next();
});

// 1. List all projects
app.get('/api/projects', (req, res) => {
  try {
    fs.mkdirSync(PROJECTS_DIR, { recursive: true });
    const items = fs.readdirSync(PROJECTS_DIR, { withFileTypes: true });
    const projects = [];

    for (const item of items) {
      if (item.isDirectory() && !item.name.startsWith('_')) {
        const pDir = path.join(PROJECTS_DIR, item.name);
        const hasFrames = fs.existsSync(path.join(pDir, 'frames_manifest.json'));
        const hasScript = fs.existsSync(path.join(pDir, 'script.json'));
        const hasAudio = fs.existsSync(path.join(pDir, 'voiceover_master.mp3'));
        const hasTranscript = fs.existsSync(path.join(pDir, 'transcript.txt'));
        const has16_9 = fs.existsSync(path.join(pDir, 'recap_16_9_master.mp4'));
        const has9_16 = fs.existsSync(path.join(pDir, 'recap_9_16_master.mp4'));

        let title = item.name;
        if (hasScript) {
          try {
            const sc = JSON.parse(fs.readFileSync(path.join(pDir, 'script.json'), 'utf-8'));
            if (sc.title) title = sc.title;
          } catch (_) {}
        }

        projects.push({
          id: item.name,
          title,
          hasFrames,
          hasScript,
          hasAudio,
          hasTranscript,
          has16_9,
          has9_16,
          updatedAt: fs.statSync(pDir).mtime
        });
      }
    }
    projects.sort((a, b) => b.updatedAt - a.updatedAt);
    res.json({ success: true, projects });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 2. Get project details
app.get('/api/projects/:id', (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);

  if (!pDir) {
    return res.status(400).json({ success: false, error: 'Invalid project ID' });
  }

  if (!fs.existsSync(pDir)) {
    return res.status(404).json({ success: false, error: 'Project not found' });
  }

  // Idempotently recover any interrupted trim transaction before reading project
  recoverProjectTrimTransactions(pDir);

  const manifestPath = path.join(pDir, 'frames_manifest.json');
  const scriptPath = path.join(pDir, 'script.json');
  const timelinePath = path.join(pDir, 'voice_timeline.json');
  const transcriptPath = path.join(pDir, 'transcript.txt');
  const storyboardPath = path.join(pDir, 'storyboard.json');

  let frames = fs.existsSync(manifestPath) ? JSON.parse(fs.readFileSync(manifestPath, 'utf-8')) : [];
  // Ensure timestamp and time_str exist on all frames
  frames = frames.map((f, idx) => {
    if (!f.time_str) {
      const ts = f.timestamp !== undefined ? f.timestamp : (idx * 3.5);
      const m = Math.floor(ts / 60);
      const s = Math.floor(ts % 60);
      f.timestamp = ts;
      f.time_str = `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }
    return f;
  });

  const script = fs.existsSync(scriptPath) ? JSON.parse(fs.readFileSync(scriptPath, 'utf-8')) : { title: pId, items: [] };
  const timeline = fs.existsSync(timelinePath) ? JSON.parse(fs.readFileSync(timelinePath, 'utf-8')) : null;
  const transcript = fs.existsSync(transcriptPath) ? fs.readFileSync(transcriptPath, 'utf-8') : '';
  const storyboard = fs.existsSync(storyboardPath) ? JSON.parse(fs.readFileSync(storyboardPath, 'utf-8')) : [];

  // Read transcription error log if exists
  const errorLogPath = path.join(pDir, 'transcript_error.json');
  let transcriptError = '';
  if (fs.existsSync(errorLogPath)) {
    try {
      const errData = JSON.parse(fs.readFileSync(errorLogPath, 'utf-8'));
      transcriptError = errData.error || '';
    } catch (_) {}
  }

  // Read OP/ED detection info if exists
  const opEdPath = path.join(pDir, 'op_ed_detection.json');
  let opEdDetection = null;
  if (fs.existsSync(opEdPath)) {
    try {
      opEdDetection = JSON.parse(fs.readFileSync(opEdPath, 'utf-8'));
    } catch (_) {}
  }

  res.json({
    success: true,
    project: {
      id: pId,
      frames,
      script,
      timeline,
      transcript,
      transcriptError,
      storyboard,
      opEdDetection,
      video16_9: fs.existsSync(path.join(pDir, 'recap_16_9_master.mp4')) ? `/projects/${pId}/recap_16_9_master.mp4` : null,
      video9_16: fs.existsSync(path.join(pDir, 'recap_9_16_master.mp4')) ? `/projects/${pId}/recap_9_16_master.mp4` : null,
      audioMaster: fs.existsSync(path.join(pDir, 'voiceover_master.mp3')) ? `/projects/${pId}/voiceover_master.mp3` : null
    }
  });
});

// 3. Create Project
app.post('/api/projects/create', (req, res) => {
  try {
    const { id, title } = req.body;
    let cleanId = (id || 'project_' + Date.now()).replace(/[^a-zA-Z0-9_\-]/g, '_');
    if (cleanId.startsWith('_')) {
      cleanId = 'p' + cleanId;
    }
    const pDir = resolveValidatedProjectDir(cleanId);
    if (!pDir) {
      return res.status(400).json({ success: false, error: 'Invalid project ID' });
    }
    fs.mkdirSync(path.join(pDir, 'frames'), { recursive: true });

    const initialScript = {
      title: title || cleanId,
      voice: 'th-TH-NiwatNeural',
      items: []
    };
    fs.writeFileSync(path.join(pDir, 'script.json'), JSON.stringify(initialScript, null, 2), 'utf-8');
    fs.writeFileSync(path.join(pDir, 'frames_manifest.json'), JSON.stringify([], null, 2), 'utf-8');

    res.json({ success: true, id: cleanId });
  } catch (err) {
    res.status(500).json({ success: false, error: 'Failed to create project' });
  }
});

// 3.9 Protect Projects Root Directory Deletion
app.delete('/api/projects', (req, res) => {
  return res.status(400).json({ success: false, error: 'Cannot delete projects root directory' });
});

// 4. Delete Project
app.delete('/api/projects/:id', (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);

  if (!pDir) {
    return res.status(400).json({ success: false, error: 'Invalid project ID' });
  }

  if (!fs.existsSync(pDir)) {
    return res.status(404).json({ success: false, error: 'Project not found' });
  }

  const baseProjectsDir = path.resolve(PROJECTS_DIR);
  if (path.resolve(pDir) === baseProjectsDir) {
    return res.status(400).json({ success: false, error: 'Cannot delete projects root directory' });
  }

  try {
    fs.rmSync(pDir, { recursive: true, force: true });
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ success: false, error: 'Failed to delete project' });
  }
});

// 4.5 Detect OP/ED Automatically from Video
app.post('/api/projects/:id/detect-op-ed', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  const { videoPath } = req.body;

  let targetVideo = videoPath;
  if (!targetVideo) {
    const pMetaFile = path.join(pDir, 'video_meta.json');
    if (fs.existsSync(pMetaFile)) {
      try {
        const vm = JSON.parse(fs.readFileSync(pMetaFile, 'utf-8'));
        targetVideo = vm.videoPath;
      } catch (_) {}
    }
  }

  if (!targetVideo || !fs.existsSync(targetVideo)) {
    return res.status(400).json({ success: false, error: 'ไม่พบไฟล์วิดีโอ กรุณาระบุที่อยู่ไฟล์' });
  }

  try {
    const detectorFile = path.join(CORE_DIR, 'op_ed_detector.py');
    const resultRaw = await runPythonScript([detectorFile, '--video', targetVideo, '--json']);
    let detection = {};
    try {
      detection = JSON.parse(resultRaw);
    } catch (_) {
      const match = resultRaw.match(/\{[\s\S]*\}/);
      if (match) detection = JSON.parse(match[0]);
    }

    // Save to op_ed_detection.json in project
    fs.writeFileSync(path.join(pDir, 'op_ed_detection.json'), JSON.stringify(detection, null, 2), 'utf-8');

    res.json({ success: true, detection });
  } catch (err) {
    console.error('Error detecting OP/ED:', err);
    res.status(500).json({ success: false, error: err.message });
  }
});

// 5. Extract Keyframes & Optional Transcribe
app.post('/api/projects/:id/extract', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });
  recoverProjectTrimTransactions(pDir);
  const {
    uploadId,
    videoPath: clientVideoPath,
    threshold = 0.28,
    maxFrames = 250,
    doTranscribe = false,
    apiKey = '',
    autoDetectOpEd = true,
    skipIntro = true,
    skipIntroSec = 90,
    introRange = '',
    skipOutro = true,
    skipOutroSec = 90,
    outroRange = ''
  } = req.body;

  // Resolve video path with authoritative uploadId & path confinement
  let videoPath = null;
  if (uploadId) {
    const uploadEntry = getUploadEntry(uploadId);
    if (!uploadEntry || (uploadEntry.status !== 'ready' && uploadEntry.status !== 'associated' && uploadEntry.status !== 'legacy_baseline')) {
      return res.status(400).json({ success: false, error: `Invalid or unready upload ID: ${uploadId}` });
    }
    const resolvedUploadPath = path.join(UPLOADS_DIR, uploadEntry.storedFilename || path.basename(uploadEntry.relPath));
    if (!fs.existsSync(resolvedUploadPath)) {
      return res.status(404).json({ success: false, error: 'Uploaded video file not found on disk' });
    }
    videoPath = resolvedUploadPath;
    await updateManifest((m) => {
      if (m.uploads[uploadId]) {
        m.uploads[uploadId].status = 'associated';
        m.uploads[uploadId].projectId = pId;
      }
    });
  } else if (clientVideoPath) {
    const resolvedPath = resolveAuthorizedVideoPath(clientVideoPath, pId);
    if (!resolvedPath) {
      return res.status(400).json({
        success: false,
        error: 'Video path must reference a valid managed upload or a file within the authorized project directory'
      });
    }
    videoPath = resolvedPath;
  } else {
    return res.status(400).json({ success: false, error: 'Either uploadId or valid managed videoPath is required' });
  }

  // Save video metadata for later reference
  try {
    fs.writeFileSync(
      path.join(pDir, 'video_meta.json'),
      JSON.stringify({ videoPath, uploadId: uploadId || null, updatedAt: new Date().toISOString() }, null, 2),
      'utf-8'
    );
  } catch (_) {}

  try {
    let effectiveIntroRange = introRange;
    let effectiveOutroRange = outroRange;
    let opEdDetection = null;

    // Option B: Auto-detect OP/ED from video directly
    if (autoDetectOpEd && (!introRange || !outroRange)) {
      try {
        console.log(`[*] 🔍 Auto-detecting OP/ED ranges from video: ${videoPath}`);
        const detectorFile = path.join(CORE_DIR, 'op_ed_detector.py');
        const detRaw = await runPythonScript([detectorFile, '--video', videoPath, '--json']);
        try {
          opEdDetection = JSON.parse(detRaw);
        } catch (_) {
          const m = detRaw.match(/\{[\s\S]*\}/);
          if (m) opEdDetection = JSON.parse(m[0]);
        }

        if (opEdDetection && opEdDetection.success) {
          // Save detection result in project directory
          fs.writeFileSync(
            path.join(pDir, 'op_ed_detection.json'),
            JSON.stringify(opEdDetection, null, 2),
            'utf-8'
          );

          if (!effectiveIntroRange && opEdDetection.op) {
            effectiveIntroRange = `${opEdDetection.op.start_str}-${opEdDetection.op.end_str}`;
            console.log(`[+] 🎯 Using Auto-detected OP: ${effectiveIntroRange} (Conf: ${opEdDetection.op.confidence})`);
          }
          if (!effectiveOutroRange && opEdDetection.ed) {
            effectiveOutroRange = `${opEdDetection.ed.start_str}-${opEdDetection.ed.end_str}`;
            console.log(`[+] 🎯 Using Auto-detected ED: ${effectiveOutroRange} (Conf: ${opEdDetection.ed.confidence})`);
          }
        }
      } catch (detErr) {
        console.warn('[!] Auto OP/ED detection warning (proceeding with fallback):', detErr.message);
      }
    }

    // 1. Extract Keyframes with OP/ED Shield
    const extractorFile = path.join(CORE_DIR, 'extractor.py');
    const framesDir = path.join(pDir, 'frames');
    const extractArgs = [
      extractorFile,
      '--video', videoPath,
      '--out', framesDir,
      '--threshold', String(threshold),
      '--max', String(maxFrames),
      '--skip-intro', skipIntro ? String(skipIntroSec) : '0',
      '--skip-outro', skipOutro ? String(skipOutroSec) : '0'
    ];
    if (effectiveIntroRange) extractArgs.push('--intro-range', String(effectiveIntroRange));
    if (effectiveOutroRange) extractArgs.push('--outro-range', String(effectiveOutroRange));
    if (autoDetectOpEd) extractArgs.push('--auto-detect-op-ed');

    console.log(`[*] Running extractor with args: ${extractArgs.join(' ')}`);
    await runPythonScript(extractArgs);

    const manifestPath = path.join(framesDir, 'frames_manifest.json');
    const pManifest = path.join(pDir, 'frames_manifest.json');
    if (fs.existsSync(manifestPath)) {
      fs.copyFileSync(manifestPath, pManifest);
    }
    const frames = JSON.parse(fs.readFileSync(pManifest, 'utf-8'));

    // 2. Extract Audio & Transcribe if selected (with OP/ED Mute)
    let transcriptText = '';
    if (doTranscribe) {
      const audioExtractorFile = path.join(CORE_DIR, 'audio_extractor.py');
      const transcribeArgs = [
        audioExtractorFile,
        '--video', videoPath,
        '--project', pDir,
        '--skip-intro', skipIntro ? String(skipIntroSec) : '0',
        '--skip-outro', skipOutro ? String(skipOutroSec) : '0'
      ];
      if (effectiveIntroRange) transcribeArgs.push('--intro-range', String(effectiveIntroRange));
      if (effectiveOutroRange) transcribeArgs.push('--outro-range', String(effectiveOutroRange));
      if (autoDetectOpEd) transcribeArgs.push('--auto-detect-op-ed');
      if (apiKey) transcribeArgs.push('--key', apiKey);

      console.log(`[*] Running audio extractor with args: ${transcribeArgs.join(' ')}`);
      await runPythonScript(transcribeArgs);

      const transcriptFile = path.join(pDir, 'transcript.txt');
      if (fs.existsSync(transcriptFile)) {
        transcriptText = fs.readFileSync(transcriptFile, 'utf-8');
      }
    }

    res.json({
      success: true,
      count: frames.length,
      frames,
      transcript: transcriptText,
      opEdDetection,
      detectedRanges: {
        op: effectiveIntroRange || null,
        ed: effectiveOutroRange || null
      }
    });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 6. Transcribe Audio directly (with OP/ED Mute support)
app.post('/api/projects/:id/transcribe', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  const {
    videoPath,
    apiKey = '',
    skipIntro = true,
    skipIntroSec = 90,
    introRange = '',
    skipOutro = true,
    skipOutroSec = 90,
    outroRange = ''
  } = req.body;

  if (!videoPath || !fs.existsSync(videoPath)) {
    return res.status(400).json({ success: false, error: 'Video path not found' });
  }

  try {
    const audioExtractorFile = path.join(CORE_DIR, 'audio_extractor.py');
    const transcribeArgs = [
      audioExtractorFile,
      '--video', videoPath,
      '--project', pDir,
      '--skip-intro', skipIntro ? String(skipIntroSec) : '0',
      '--skip-outro', skipOutro ? String(skipOutroSec) : '0'
    ];
    if (introRange) transcribeArgs.push('--intro-range', String(introRange));
    if (outroRange) transcribeArgs.push('--outro-range', String(outroRange));
    if (apiKey) transcribeArgs.push('--key', apiKey);

    await runPythonScript(transcribeArgs);

    const transcriptFile = path.join(pDir, 'transcript.txt');
    const transcriptText = fs.existsSync(transcriptFile) ? fs.readFileSync(transcriptFile, 'utf-8') : '';
    
    // Read error info if exists
    const errorLogFile = path.join(pDir, 'transcript_error.json');
    let transcriptError = '';
    if (fs.existsSync(errorLogFile)) {
      try {
        const errData = JSON.parse(fs.readFileSync(errorLogFile, 'utf-8'));
        transcriptError = errData.error || '';
      } catch (_) {}
    }
    
    res.json({ success: true, transcript: transcriptText, transcriptError });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 6.05 Retry Transcription (clears old error & re-runs with exponential backoff)
app.post('/api/projects/:id/retry-transcribe', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  const {
    videoPath,
    apiKey = '',
    skipIntro = true,
    skipIntroSec = 90,
    introRange = '',
    skipOutro = true,
    skipOutroSec = 90,
    outroRange = ''
  } = req.body;

  // Clear old error transcript before retrying
  const transcriptFile = path.join(pDir, 'transcript.txt');
  const errorLogFile = path.join(pDir, 'transcript_error.json');
  
  if (fs.existsSync(errorLogFile)) {
    try { fs.unlinkSync(errorLogFile); } catch (_) {}
  }
  // Clear old error-containing transcript
  const oldTranscript = fs.existsSync(transcriptFile) ? fs.readFileSync(transcriptFile, 'utf-8') : '';
  const isOldError = oldTranscript.includes('เกิดข้อผิดพลาด') || oldTranscript.includes('503 Service Unavailable') || oldTranscript.trim() === '';
  if (isOldError) {
    fs.writeFileSync(transcriptFile, '', 'utf-8');
  }

  // Find video path: use provided path or try to find audio file
  let actualVideoPath = videoPath;
  if (!actualVideoPath) {
    // Try to find the original video from upload
    const uploadsDir = path.join(PROJECTS_DIR, '_uploads');
    if (fs.existsSync(uploadsDir)) {
      const uploads = fs.readdirSync(uploadsDir).filter(f => /\.(mp4|mkv|avi|webm)$/i.test(f));
      if (uploads.length > 0) {
        actualVideoPath = path.join(uploadsDir, uploads[uploads.length - 1]);
      }
    }
  }

  // If we still don't have a video path, check if extracted audio already exists
  const existingAudio = path.join(pDir, 'extracted_audio.mp3');
  const hasExistingAudio = fs.existsSync(existingAudio);

  if (!actualVideoPath && !hasExistingAudio) {
    return res.status(400).json({ 
      success: false, 
      error: 'ไม่พบไฟล์วิดีโอหรือไฟล์เสียง กรุณาระบุ path ของวิดีโอ หรือสกัดภาพ+เสียงใหม่ก่อน' 
    });
  }

  if (!apiKey) {
    return res.status(400).json({ 
      success: false, 
      error: 'กรุณาใส่ Gemini API Key เพื่อถอดเสียง' 
    });
  }

  try {
    const audioExtractorFile = path.join(CORE_DIR, 'audio_extractor.py');
    const transcribeArgs = [
      audioExtractorFile,
      '--video', actualVideoPath || existingAudio,
      '--project', pDir,
      '--skip-intro', skipIntro ? String(skipIntroSec) : '0',
      '--skip-outro', skipOutro ? String(skipOutroSec) : '0'
    ];
    if (introRange) transcribeArgs.push('--intro-range', String(introRange));
    if (outroRange) transcribeArgs.push('--outro-range', String(outroRange));
    transcribeArgs.push('--key', apiKey);

    await runPythonScript(transcribeArgs);

    const transcriptText = fs.existsSync(transcriptFile) ? fs.readFileSync(transcriptFile, 'utf-8') : '';
    
    // Check if still has error
    let retryError = '';
    if (fs.existsSync(errorLogFile)) {
      try {
        const errData = JSON.parse(fs.readFileSync(errorLogFile, 'utf-8'));
        retryError = errData.error || '';
      } catch (_) {}
    }

    res.json({ 
      success: true, 
      transcript: transcriptText,
      transcriptError: retryError,
      retried: true
    });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Per-project concurrency serialization lock (Phase 5)
const projectLockQueue = new Map();
async function runWithProjectLock(pId, task) {
  while (projectLockQueue.has(pId)) {
    try {
      await projectLockQueue.get(pId);
    } catch (_) {}
  }
  let release;
  const p = new Promise(r => release = r);
  projectLockQueue.set(pId, p);
  try {
    return await task();
  } finally {
    projectLockQueue.delete(pId);
    release();
  }
}

// Transaction crash recovery for trim operations (Phase 5)
// Ensures recovery is idempotent and can be safely retried
function recoverProjectTrimTransactions(pDir) {
  if (!pDir || !fs.existsSync(pDir)) return;
  let entries = [];
  try {
    entries = fs.readdirSync(pDir);
  } catch (_) {
    return;
  }

  for (const entry of entries) {
    if (entry.startsWith('.trim_tx_')) {
      const txDir = path.join(pDir, entry);
      try {
        const stat = fs.statSync(txDir);
        if (!stat.isDirectory()) continue;
      } catch (_) {
        continue;
      }

      const commitFile = path.join(txDir, 'commit.json');
      const journalFile = path.join(txDir, 'journal.json');
      const trashDir = path.join(txDir, 'trash');
      const backupDir = path.join(txDir, 'backup');

      const hasCommit = fs.existsSync(commitFile);

      if (hasCommit) {
        // Committed transaction: finish cleanup of staged trash without rolling committed metadata back
        try {
          if (fs.existsSync(trashDir)) {
            fs.rmSync(trashDir, { recursive: true, force: true });
          }
          if (fs.existsSync(backupDir)) {
            fs.rmSync(backupDir, { recursive: true, force: true });
          }
          fs.rmSync(txDir, { recursive: true, force: true });
          console.log(`[trim-recovery] Cleaned up committed transaction: ${entry}`);
        } catch (e) {
          console.warn(`[trim-recovery] Warning cleaning committed tx ${entry}: ${e.message}`);
        }
      } else {
        // Interrupted/uncommitted transaction: roll back staged frame files and original metadata
        console.log(`[trim-recovery] Rolling back interrupted trim transaction: ${entry}`);
        let journal = null;
        if (fs.existsSync(journalFile)) {
          try {
            journal = JSON.parse(fs.readFileSync(journalFile, 'utf-8'));
          } catch (_) {}
        }

        // 1. Roll back metadata files
        if (journal && Array.isArray(journal.metadataBackups)) {
          for (const meta of journal.metadataBackups) {
            const target = meta.targetPath;
            if (meta.existed && meta.backupName) {
              const bkp = path.join(backupDir, meta.backupName);
              if (fs.existsSync(bkp)) {
                try {
                  fs.copyFileSync(bkp, target);
                } catch (err) {
                  console.warn(`[trim-recovery] Error restoring metadata ${target}: ${err.message}`);
                }
              }
            } else if (!meta.existed) {
              // Metadata did not exist before transaction: remove if created
              if (fs.existsSync(target)) {
                try {
                  fs.unlinkSync(target);
                } catch (_) {}
              }
            }
          }
        }

        // 2. Roll back staged frame files
        if (journal && Array.isArray(journal.stagedFiles)) {
          for (const sf of journal.stagedFiles) {
            const trashFile = path.join(trashDir, sf.trashName);
            if (fs.existsSync(trashFile) && !fs.existsSync(sf.originalPath)) {
              try {
                fs.renameSync(trashFile, sf.originalPath);
              } catch (_) {
                try {
                  fs.copyFileSync(trashFile, sf.originalPath);
                  fs.unlinkSync(trashFile);
                } catch (_) {}
              }
            }
          }
        } else if (fs.existsSync(trashDir)) {
          // Fallback if journal unreadable: restore any files in trash/ to frames/
          try {
            const trashFiles = fs.readdirSync(trashDir);
            for (const tf of trashFiles) {
              const src = path.join(trashDir, tf);
              const origName = tf.includes('_') ? tf.substring(tf.indexOf('_') + 1) : tf;
              const dst = path.join(pDir, 'frames', origName);
              if (!fs.existsSync(dst)) {
                try {
                  fs.renameSync(src, dst);
                } catch (_) {}
              }
            }
          } catch (_) {}
        }

        // 3. Clean up transaction directory
        try {
          fs.rmSync(txDir, { recursive: true, force: true });
        } catch (_) {}
      }
    }
  }
}

// 6.08 Execute Trim Transaction with Journaling, Staging, and Checkpoints (Phase 5)
async function executeTrimTransaction(pDir, pId, options = {}, testHook = null) {
  // Always recover any interrupted transaction first
  recoverProjectTrimTransactions(pDir);

  const {
    trimIntro = false,
    introSec = 90,
    introRange = '',
    trimOutro = false,
    outroSec = 90,
    outroRange = '',
    customStart = null,
    customEnd = null,
    frameIds = null
  } = options;

  const manifestPath = path.join(pDir, 'frames_manifest.json');
  const storyboardPath = path.join(pDir, 'storyboard.json');
  const legacyFramesPath = path.join(pDir, 'frames.json');
  const framesSubManifest = path.join(pDir, 'frames', 'frames_manifest.json');

  if (!fs.existsSync(manifestPath)) {
    if (fs.existsSync(legacyFramesPath)) {
      fs.copyFileSync(legacyFramesPath, manifestPath);
    } else {
      return { success: false, statusCode: 404, error: 'No frames found in project' };
    }
  }

  let allFrames;
  try {
    allFrames = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));
  } catch (parseErr) {
    return { success: false, statusCode: 500, error: 'Corrupt frames_manifest.json: ' + parseErr.message };
  }

  if (!Array.isArray(allFrames)) {
    return { success: false, statusCode: 500, error: 'frames_manifest.json does not contain an array' };
  }

  const totalBefore = allFrames.length;
  if (totalBefore === 0) {
    return {
      success: true,
      removedCount: 0,
      removed: 0,
      remainingCount: 0,
      kept: 0,
      count: 0,
      deletedFilesCount: 0,
      frames: [],
      storyboard: []
    };
  }

  const existingIdSet = new Set(allFrames.map(f => String(f.id)));
  let removeFrames = [];
  let keptFrames = [];

  const parseTime = (val) => {
    if (val === undefined || val === null || val === '') return null;
    const s = String(val).trim();
    if (!s) return null;
    if (s.includes(':')) {
      const p = s.split(':');
      if (p.length === 2) {
        const m = parseFloat(p[0]);
        const sec = parseFloat(p[1]);
        if (isNaN(m) || isNaN(sec)) return null;
        return m * 60 + sec;
      } else if (p.length === 3) {
        const h = parseFloat(p[0]);
        const m = parseFloat(p[1]);
        const sec = parseFloat(p[2]);
        if (isNaN(h) || isNaN(m) || isNaN(sec)) return null;
        return h * 3600 + m * 60 + sec;
      }
      return null;
    }
    const num = parseFloat(s);
    return isNaN(num) ? null : num;
  };

  const parseRange = (rStr) => {
    if (!rStr || (typeof rStr !== 'string' && typeof rStr !== 'number')) return null;
    const s = String(rStr).trim();
    if (!s) return null;
    if (!s.includes('-') && !s.includes('–')) {
      return { valid: false, error: 'Range must contain a hyphen separator (e.g. 01:30-03:00)' };
    }
    const parts = s.split(/[-–]/).map(x => x.trim()).filter(Boolean);
    if (parts.length < 2) {
      return { valid: false, error: 'Range must have start and end times' };
    }
    const startSec = parseTime(parts[0]);
    const endSec = parseTime(parts[1]);
    if (startSec === null || endSec === null) {
      return { valid: false, error: `Invalid numeric timestamp in range: ${s}` };
    }
    if (endSec <= startSec) {
      return { valid: false, error: `Range end (${endSec}s) must be greater than start (${startSec}s)` };
    }
    return { valid: true, range: [startSec, endSec] };
  };

  if (frameIds !== null && frameIds !== undefined) {
    if (!Array.isArray(frameIds)) {
      return { success: false, statusCode: 400, error: 'frameIds must be an array' };
    }
    const unknownIds = frameIds.filter(id => !existingIdSet.has(String(id)));
    if (unknownIds.length > 0) {
      return { success: false, statusCode: 400, error: `Unknown frame IDs: ${unknownIds.join(', ')}` };
    }
    const idSet = new Set(frameIds.map(String));
    keptFrames = allFrames.filter(f => !idSet.has(String(f.id)));
    removeFrames = allFrames.filter(f => idSet.has(String(f.id)));
  } else {
    let iRange = null;
    if (introRange) {
      const pr = parseRange(introRange);
      if (!pr.valid) return { success: false, statusCode: 400, error: `Invalid introRange: ${pr.error}` };
      iRange = pr.range;
    } else if (trimIntro) {
      const parsedIntroSec = parseTime(introSec);
      if (parsedIntroSec === null || parsedIntroSec < 0) {
        return { success: false, statusCode: 400, error: 'Invalid introSec value' };
      }
      iRange = [0, parsedIntroSec];
    }

    const maxTs = allFrames.reduce((max, f) => Math.max(max, f.timestamp || 0), 0);

    let oRange = null;
    if (outroRange) {
      const pr = parseRange(outroRange);
      if (!pr.valid) return { success: false, statusCode: 400, error: `Invalid outroRange: ${pr.error}` };
      oRange = pr.range;
    } else if (trimOutro) {
      const parsedOutroSec = parseTime(outroSec);
      if (parsedOutroSec === null || parsedOutroSec < 0) {
        return { success: false, statusCode: 400, error: 'Invalid outroSec value' };
      }
      if (maxTs > 0) {
        oRange = [Math.max(0, maxTs - parsedOutroSec), maxTs + 9999];
      }
    }

    let cRange = null;
    if ((customStart !== null && customStart !== '') || (customEnd !== null && customEnd !== '')) {
      const cs = parseTime(customStart);
      const ce = parseTime(customEnd);
      if (cs === null || ce === null) {
        return { success: false, statusCode: 400, error: 'customStart and customEnd must both be valid numbers/timestamps' };
      }
      if (ce <= cs) {
        return { success: false, statusCode: 400, error: 'customEnd must be greater than customStart' };
      }
      cRange = [cs, ce];
    }

    const isFrameInCutRange = (ts) => {
      if (iRange && ts >= iRange[0] - 0.5 && ts <= iRange[1] + 0.5) return true;
      if (oRange && ts >= oRange[0] - 0.5 && ts <= oRange[1] + 0.5) return true;
      if (cRange && ts >= cRange[0] - 0.5 && ts <= cRange[1] + 0.5) return true;
      return false;
    };

    keptFrames = allFrames.filter(f => !isFrameInCutRange(f.timestamp || 0));
    removeFrames = allFrames.filter(f => isFrameInCutRange(f.timestamp || 0));
  }

  // Strict path confinement resolver for frame files
  const resolveConfinedFrameFile = (frame) => {
    const candidates = [];
    if (frame.filename) {
      const b = path.basename(frame.filename);
      if (b && !b.includes('..') && !b.startsWith('.')) {
        candidates.push(path.join(pDir, 'frames', b));
        candidates.push(path.join(pDir, b));
      }
    }
    if (frame.path) {
      const b = path.basename(frame.path);
      if (b && !b.includes('..') && !b.startsWith('.')) {
        candidates.push(path.join(pDir, 'frames', b));
        candidates.push(path.join(pDir, b));
      }
      const fullRel = path.isAbsolute(frame.path) ? frame.path : path.resolve(BASE_DIR, frame.path);
      candidates.push(fullRel);
    }
    if (frame.id) {
      const idSafe = String(frame.id).replace(/[^a-zA-Z0-9_-]/g, '');
      if (idSafe) {
        candidates.push(path.join(pDir, 'frames', `${idSafe}.jpg`));
        candidates.push(path.join(pDir, `${idSafe}.jpg`));
      }
    }

    const resolvedPDir = path.resolve(pDir);
    for (const cand of candidates) {
      const resolved = path.resolve(cand);
      const rel = path.relative(resolvedPDir, resolved);
      // STRICT BOUNDARY CHECK: candidate MUST be within pDir and not inside a dotfile directory
      if (!rel.startsWith('..') && !path.isAbsolute(rel) && !rel.includes('.trim_tx_') && fs.existsSync(resolved)) {
        return resolved;
      }
    }
    return null;
  };

  if (removeFrames.length === 0) {
    let currentSb = [];
    if (fs.existsSync(storyboardPath)) {
      try { currentSb = JSON.parse(fs.readFileSync(storyboardPath, 'utf-8')); } catch (_) {}
    }
    return {
      success: true,
      removedCount: 0,
      removed: 0,
      remainingCount: keptFrames.length,
      kept: keptFrames.length,
      count: keptFrames.length,
      deletedFilesCount: 0,
      frames: keptFrames,
      storyboard: currentSb
    };
  }

  // --- CHECKPOINT 1: Before any frame is staged ---
  if (testHook && (testHook === 'before_stage' || testHook.checkpoint === 'before_stage')) {
    throw new Error('Test injected failure: before_stage');
  }

  // Transaction Setup
  const txId = `tx_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`;
  const txDir = path.join(pDir, `.trim_tx_${txId}`);
  const trashDir = path.join(txDir, 'trash');
  const backupDir = path.join(txDir, 'backup');

  fs.mkdirSync(trashDir, { recursive: true });
  fs.mkdirSync(backupDir, { recursive: true });

  const metadataBackups = [
    { fileKey: 'manifest', targetPath: manifestPath, existed: fs.existsSync(manifestPath), backupName: 'frames_manifest.json' },
    { fileKey: 'subManifest', targetPath: framesSubManifest, existed: fs.existsSync(framesSubManifest), backupName: 'sub_frames_manifest.json' },
    { fileKey: 'legacyFrames', targetPath: legacyFramesPath, existed: fs.existsSync(legacyFramesPath), backupName: 'frames.json' },
    { fileKey: 'storyboard', targetPath: storyboardPath, existed: fs.existsSync(storyboardPath), backupName: 'storyboard.json' }
  ];

  for (const mb of metadataBackups) {
    if (mb.existed) {
      fs.copyFileSync(mb.targetPath, path.join(backupDir, mb.backupName));
    }
  }

  const journal = {
    txId,
    projectId: pId,
    startedAt: new Date().toISOString(),
    stagedFiles: [],
    metadataBackups,
    committed: false
  };

  const rollback = () => {
    console.log(`[trim-tx] Executing rollback for ${txId}...`);
    // Restore metadata files
    for (const mb of metadataBackups) {
      if (mb.existed) {
        const bkp = path.join(backupDir, mb.backupName);
        if (fs.existsSync(bkp)) {
          try { fs.copyFileSync(bkp, mb.targetPath); } catch (_) {}
        }
      } else {
        if (fs.existsSync(mb.targetPath)) {
          try { fs.unlinkSync(mb.targetPath); } catch (_) {}
        }
      }
    }
    // Restore staged frame files
    for (const sf of journal.stagedFiles) {
      const trashFile = path.join(trashDir, sf.trashName);
      if (fs.existsSync(trashFile) && !fs.existsSync(sf.originalPath)) {
        try {
          fs.renameSync(trashFile, sf.originalPath);
        } catch (_) {
          try {
            fs.copyFileSync(trashFile, sf.originalPath);
            fs.unlinkSync(trashFile);
          } catch (_) {}
        }
      }
    }
    // Cleanup transaction directory
    try {
      fs.rmSync(txDir, { recursive: true, force: true });
    } catch (_) {}
  };

  try {
    // 1. Stage frame removals into trashDir
    let stagedCount = 0;
    for (let i = 0; i < removeFrames.length; i++) {
      const f = removeFrames[i];
      const fileToUnlink = resolveConfinedFrameFile(f);
      if (fileToUnlink && fs.existsSync(fileToUnlink)) {
        const trashName = `${i}_${path.basename(fileToUnlink)}`;
        const trashTarget = path.join(trashDir, trashName);
        fs.renameSync(fileToUnlink, trashTarget);
        journal.stagedFiles.push({
          originalPath: fileToUnlink,
          trashName
        });
        stagedCount++;
      }
    }

    fs.writeFileSync(path.join(txDir, 'journal.json'), JSON.stringify(journal, null, 2), 'utf-8');

    // --- CHECKPOINT 2: After a frame has been staged ---
    if (testHook && (testHook === 'after_stage' || testHook.checkpoint === 'after_stage')) {
      throw new Error('Test injected failure: after_stage');
    }

    // Re-index remaining frames
    keptFrames.forEach((f, idx) => {
      f.index = idx + 1;
    });

    let keptStoryboard = [];
    if (fs.existsSync(storyboardPath)) {
      const sb = JSON.parse(fs.readFileSync(storyboardPath, 'utf-8'));
      const removeIdSet = new Set(removeFrames.map(f => String(f.id)));
      keptStoryboard = sb.filter(f => !removeIdSet.has(String(f.id)));
    }

    // --- CHECKPOINT 3: Before the first metadata commit ---
    if (testHook && (testHook === 'before_first_meta' || testHook.checkpoint === 'before_first_meta')) {
      throw new Error('Test injected failure: before_first_meta');
    }

    const safeWriteJson = (targetFile, data) => {
      const dir = path.dirname(targetFile);
      if (!fs.existsSync(dir)) return;
      const tmpFile = path.join(dir, `.tmp_${path.basename(targetFile)}_${Date.now()}`);
      fs.writeFileSync(tmpFile, JSON.stringify(data, null, 2), 'utf-8');
      try {
        fs.renameSync(tmpFile, targetFile);
      } catch (_) {
        fs.writeFileSync(targetFile, JSON.stringify(data, null, 2), 'utf-8');
        try { fs.unlinkSync(tmpFile); } catch (_) {}
      }
    };

    // Commit first metadata file: manifestPath
    safeWriteJson(manifestPath, keptFrames);

    // --- CHECKPOINT 4: After one metadata file has been committed but before remaining metadata files are committed ---
    if (testHook && (testHook === 'after_first_meta' || testHook.checkpoint === 'after_first_meta')) {
      throw new Error('Test injected failure: after_first_meta');
    }

    if (fs.existsSync(path.dirname(framesSubManifest))) {
      safeWriteJson(framesSubManifest, keptFrames);
    }
    if (fs.existsSync(legacyFramesPath)) {
      safeWriteJson(legacyFramesPath, keptFrames);
    }
    if (metadataBackups.find(mb => mb.fileKey === 'storyboard' && mb.existed)) {
      safeWriteJson(storyboardPath, keptStoryboard);
    }

    // --- CHECKPOINT 5: Immediately before commit-marker persistence ---
    if (testHook && (testHook === 'before_commit_marker' || testHook.checkpoint === 'before_commit_marker')) {
      throw new Error('Test injected failure: before_commit_marker');
    }

    // Persist commit marker
    fs.writeFileSync(
      path.join(txDir, 'commit.json'),
      JSON.stringify({ txId, committedAt: new Date().toISOString(), status: 'committed' }, null, 2),
      'utf-8'
    );

    // --- CHECKPOINT 6: After commit-marker persistence but before trash cleanup finishes ---
    if (testHook && (testHook === 'after_commit_marker' || testHook.checkpoint === 'after_commit_marker')) {
      throw new Error('Test injected failure: after_commit_marker');
    }

    // Finalize cleanup: purge trash & remove txDir
    fs.rmSync(trashDir, { recursive: true, force: true });
    fs.rmSync(backupDir, { recursive: true, force: true });
    fs.rmSync(txDir, { recursive: true, force: true });

    const removedCount = totalBefore - keptFrames.length;
    console.log(`[trim-frames] Project ${pId}: removed ${removedCount} frames (${stagedCount} files unlinked), kept ${keptFrames.length} frames`);

    return {
      success: true,
      removedCount,
      removed: removedCount,
      remainingCount: keptFrames.length,
      kept: keptFrames.length,
      deletedFilesCount: stagedCount,
      frames: keptFrames,
      storyboard: keptStoryboard
    };
  } catch (err) {
    if (fs.existsSync(path.join(txDir, 'commit.json'))) {
      console.log(`[trim-tx] Error occurred after commit marker; finishing recovery rather than rolling back.`);
      recoverProjectTrimTransactions(pDir);
      throw err;
    } else {
      rollback();
      throw err;
    }
  }
}

// 6.08 Trim / Filter OP and ED frames from an existing project (Canonical Phase 4 & Phase 5 Hardened)
app.post('/api/projects/:id/trim-frames', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  // Note: testHook is strictly ignored from client requests to prevent external failure injection
  const {
    trimIntro = false,
    introSec = 90,
    introRange = '',
    trimOutro = false,
    outroSec = 90,
    outroRange = '',
    customStart = null,
    customEnd = null,
    frameIds = null
  } = req.body;

  try {
    const result = await runWithProjectLock(pId, async () => {
      return await executeTrimTransaction(pDir, pId, {
        trimIntro,
        introSec,
        introRange,
        trimOutro,
        outroSec,
        outroRange,
        customStart,
        customEnd,
        frameIds
      });
    });

    if (!result.success) {
      return res.status(result.statusCode || 400).json(result);
    }

    return res.json(result);
  } catch (err) {
    console.error('[trim-frames] Error:', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

// 6.1 Save or Update Transcript Manually
app.post('/api/projects/:id/transcript', (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });
  const { transcript } = req.body;

  try {
    const transcriptFile = path.join(pDir, 'transcript.txt');
    fs.writeFileSync(transcriptFile, transcript || '', 'utf-8');
    res.json({ success: true, transcript });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 6.5 Save or Auto-Select Storyboard
app.post('/api/projects/:id/storyboard', (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });
  const { scenes, autoSelectCount } = req.body;

  try {
    const storyboardPath = path.join(pDir, 'storyboard.json');
    const manifestPath = path.join(pDir, 'frames_manifest.json');
    let finalScenes = [];

    if (autoSelectCount && fs.existsSync(manifestPath)) {
      let allFrames = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));
      // Ensure time_str
      allFrames = allFrames.map((f, idx) => {
        if (!f.time_str) {
          const ts = f.timestamp !== undefined ? f.timestamp : (idx * 3.5);
          const m = Math.floor(ts / 60);
          const s = Math.floor(ts % 60);
          f.timestamp = ts;
          f.time_str = `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        }
        return f;
      });

      const count = Math.min(parseInt(autoSelectCount), allFrames.length);
      if (allFrames.length <= count) {
        finalScenes = allFrames;
      } else if (count <= 1) {
        finalScenes = [allFrames[0]];
      } else {
        const step = (allFrames.length - 1) / (count - 1);
        let lastIdx = -1;
        for (let i = 0; i < count; i++) {
          let idx = Math.min(Math.round(i * step), allFrames.length - 1);
          if (idx <= lastIdx && idx + 1 < allFrames.length) {
            idx = lastIdx + 1;
          }
          lastIdx = idx;
          finalScenes.push(allFrames[idx]);
        }
      }
    } else if (Array.isArray(scenes)) {
      finalScenes = scenes;
    }

    fs.writeFileSync(storyboardPath, JSON.stringify(finalScenes, null, 2), 'utf-8');
    res.json({ success: true, count: finalScenes.length, storyboard: finalScenes });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Helper to recalculate project timeline using timing_engine.py
async function recalculateProjectTimeline(pDir) {
  try {
    const timingFile = path.join(CORE_DIR, 'timing_engine.py');
    await runPythonScript([timingFile, '--mode', 'recalculate', '--project', pDir]);
    const timelinePath = path.join(pDir, 'voice_timeline.json');
    if (fs.existsSync(timelinePath)) {
      return JSON.parse(fs.readFileSync(timelinePath, 'utf-8'));
    }
  } catch (e) {
    console.warn(`[Warn] Could not recalculate timeline: ${e.message}`);
  }
  return null;
}

// 7. Update Script (with Automatic Timeline Recalculation)
app.post('/api/projects/:id/script', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });
  const { script } = req.body;

  if (!script) {
    return res.status(400).json({ success: false, error: 'Missing script data' });
  }

  try {
    fs.writeFileSync(path.join(pDir, 'script.json'), JSON.stringify(script, null, 2), 'utf-8');
    const timeline = await recalculateProjectTimeline(pDir);
    res.json({ success: true, timeline });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 8. Delete specific script line (with Automatic Timeline Recalculation)
app.post('/api/projects/:id/script/delete-line', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });
  const { lineIndex } = req.body;

  try {
    const scriptPath = path.join(pDir, 'script.json');
    if (!fs.existsSync(scriptPath)) return res.status(404).json({ error: 'Script not found' });
    const script = JSON.parse(fs.readFileSync(scriptPath, 'utf-8'));
    
    if (script.items && script.items.length > lineIndex) {
      script.items.splice(lineIndex, 1);
      // Re-index
      script.items.forEach((item, idx) => { item.id = idx + 1; });
      fs.writeFileSync(scriptPath, JSON.stringify(script, null, 2), 'utf-8');
    }
    const timeline = await recalculateProjectTimeline(pDir);
    res.json({ success: true, script, timeline });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 9. Insert specific script line (with Automatic Timeline Recalculation)
app.post('/api/projects/:id/script/insert-line', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });
  const { afterIndex, text, action_type, image_id } = req.body;

  try {
    const scriptPath = path.join(pDir, 'script.json');
    if (!fs.existsSync(scriptPath)) return res.status(404).json({ error: 'Script not found' });
    const script = JSON.parse(fs.readFileSync(scriptPath, 'utf-8'));
    
    const newItem = {
      id: afterIndex + 2,
      image_id: image_id || 'frame_0001',
      text: text || 'พิมพ์ข้อความบรรยายฉากนี้...',
      action_type: action_type || 'normal'
    };

    if (!script.items) script.items = [];
    script.items.splice(afterIndex + 1, 0, newItem);
    // Re-index
    script.items.forEach((item, idx) => { item.id = idx + 1; });
    fs.writeFileSync(scriptPath, JSON.stringify(script, null, 2), 'utf-8');
    const timeline = await recalculateProjectTimeline(pDir);
    res.json({ success: true, script, timeline });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 9.5 Split Scene automatically
app.post('/api/projects/:id/split-scene', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });
  const { index } = req.body;

  try {
    const timingFile = path.join(CORE_DIR, 'timing_engine.py');
    const rawOut = await runPythonScript([
      timingFile,
      '--mode', 'split-scene',
      '--project', pDir,
      '--index', String(index || 0),
      '--json'
    ]);
    let result = {};
    try {
      result = JSON.parse(rawOut);
    } catch (_) {
      const m = rawOut.match(/\{[\s\S]*\}/);
      if (m) result = JSON.parse(m[0]);
    }
    res.json(result);
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 9.6 Compress Scene Narration automatically
app.post('/api/projects/:id/compress-scene', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });
  const { index } = req.body;

  try {
    const timingFile = path.join(CORE_DIR, 'timing_engine.py');
    const rawOut = await runPythonScript([
      timingFile,
      '--mode', 'compress-scene',
      '--project', pDir,
      '--index', String(index || 0),
      '--json'
    ]);
    let result = {};
    try {
      result = JSON.parse(rawOut);
    } catch (_) {
      const m = rawOut.match(/\{[\s\S]*\}/);
      if (m) result = JSON.parse(m[0]);
    }
    res.json(result);
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 9.7 Recalculate Timeline on Demand
app.post('/api/projects/:id/recalculate-timeline', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  try {
    const timeline = await recalculateProjectTimeline(pDir);
    res.json({ success: true, timeline });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 9.8 Validate Timeline QC
app.get('/api/projects/:id/validate-timeline', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  try {
    const timingFile = path.join(CORE_DIR, 'timing_engine.py');
    const rawOut = await runPythonScript([
      timingFile,
      '--mode', 'validate',
      '--project', pDir
    ]).catch(err => err.message);
    
    let result = {};
    try {
      result = JSON.parse(rawOut);
    } catch (_) {
      const m = rawOut.match(/\{[\s\S]*\}/);
      if (m) result = JSON.parse(m[0]);
      else result = { valid: false, errors: [rawOut] };
    }
    res.json(result);
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 10. Generate AI Script with custom scene count & transcript
app.post('/api/projects/:id/ai-script', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });
  const { title, synopsis, apiKey, count = 30, resample = true } = req.body;

  try {
    const scriptFile = path.join(CORE_DIR, 'script_generator.py');
    const outPath = path.join(pDir, 'script.json');
    const args = [
      scriptFile,
      '--title', title || pId,
      '--out', outPath,
      '--project', pDir,
      '--count', String(count)
    ];
    if (resample) args.push('--resample');
    if (apiKey) args.push('--key', apiKey);
    if (synopsis) args.push('--synopsis', synopsis);

    await runPythonScript(args);
    const updatedScript = JSON.parse(fs.readFileSync(outPath, 'utf-8'));
    
    // Automatically recalculate initial estimated timeline
    const timeline = await recalculateProjectTimeline(pDir);
    
    // Also read updated storyboard if exists
    const sbPath = path.join(pDir, 'storyboard.json');
    const updatedStoryboard = fs.existsSync(sbPath) ? JSON.parse(fs.readFileSync(sbPath, 'utf-8')) : [];
    
    res.json({ success: true, script: updatedScript, storyboard: updatedStoryboard, timeline });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 11. Generate TTS Voiceover & Subtitles
app.post('/api/projects/:id/tts', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });
  const { voice = 'th-TH-NiwatNeural', rate = '+5%' } = req.body;

  try {
    const scriptFile = path.join(CORE_DIR, 'tts_engine.py');
    const scriptJson = path.join(pDir, 'script.json');
    await runPythonScript([scriptFile, '--script', scriptJson, '--out', pDir, '--voice', voice, '--rate', rate]);

    const timeline = JSON.parse(fs.readFileSync(path.join(pDir, 'voice_timeline.json'), 'utf-8'));
    res.json({ success: true, timeline });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 12. Render Master Video (with Pre-Render Quality Control)
app.post('/api/projects/:id/render', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });
  recoverProjectTrimTransactions(pDir);
  const { aspect = '16:9', bgm = 'epic_anime.mp3', subtitles = true } = req.body;

  try {
    const timelineFile = path.join(pDir, 'voice_timeline.json');
    if (!fs.existsSync(timelineFile)) {
      return res.status(400).json({ success: false, error: 'Timeline file (voice_timeline.json) not found. Please run TTS first.' });
    }

    // Quality Control validation check before rendering (Phase 15 & Phase 2)
    const timingFile = path.join(CORE_DIR, 'timing_engine.py');
    const qcRaw = await runPythonScript([timingFile, '--mode', 'validate', '--project', pDir]).catch(() => null);
    if (qcRaw) {
      try {
        const qc = JSON.parse(qcRaw);
        if (!qc.valid && qc.errors && qc.errors.length > 0) {
          console.warn(`[QC Validation Warning before Render]: ${qc.errors.join(', ')}`);
          const criticalErrors = qc.errors.filter(e =>
            e.includes('Invalid duration') ||
            e.includes('discontinuity') ||
            e.includes('NaN') ||
            e.includes('Audio overflow') ||
            e.includes('no scenes')
          );
          if (criticalErrors.length > 0) {
            return res.status(422).json({
              success: false,
              error: `Timeline validation failed before render: ${criticalErrors.join('; ')}`
            });
          }
        }
      } catch (_) {}
    }

    const scriptFile = path.join(CORE_DIR, 'motion_compositor.py');
    const args = [scriptFile, '--project', pDir, '--aspect', aspect, '--bgm', bgm];
    if (subtitles) args.push('--subtitles');

    await runPythonScript(args);
    const filename = `recap_${aspect.replace(':', '_')}_master.mp4`;
    const finalVideoPath = path.join(pDir, filename);

    if (!fs.existsSync(finalVideoPath) || fs.statSync(finalVideoPath).size === 0) {
      return res.status(500).json({ success: false, error: 'Rendered video file is missing or empty' });
    }

    // Register export in provenance manifest (Phase 6)
    // If provenance registration encounters an issue, preserve valid video and flag provenancePending
    let provenancePending = false;
    try {
      await registerExportProvenance(pDir, pId, { aspect, bgm, subtitles, filename });
    } catch (provErr) {
      console.warn(`[Provenance Warning]: Could not register export provenance for ${filename}: ${provErr.message}`);
      provenancePending = true;
    }

    res.json({
      success: true,
      videoUrl: `/projects/${pId}/${filename}`,
      provenancePending
    });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// 12.1 Get Project Asset Provenance (Phase 6 Read-Only)
app.get('/api/projects/:id/provenance', (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  const manifest = loadProjectProvenance(pDir);
  return res.json({
    success: true,
    manifest: manifest || null,
    migrated: !!manifest
  });
});

// 12.2 Migrate or Refresh Project Asset Provenance (Phase 6 Idempotent)
app.post('/api/projects/:id/provenance/migrate', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  try {
    const manifest = await migrateProjectProvenance(pDir, pId);
    return res.json({ success: true, manifest });
  } catch (err) {
    console.error('[Provenance Migration Error]', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

// 12.3 Update Asset Rights & Provenance Metadata (Phase 6 Controlled Mutation)
app.put('/api/projects/:id/provenance/:assetId', (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  const assetId = req.params.assetId;
  if (!assetId || typeof assetId !== 'string') {
    return res.status(400).json({ success: false, error: 'Asset ID required' });
  }

  try {
    const updatedAsset = updateAssetProvenance(pDir, pId, assetId, req.body || {});
    return res.json({ success: true, asset: updatedAsset });
  } catch (err) {
    return res.status(400).json({ success: false, error: err.message });
  }
});

// 12.4 Get Publication-Readiness Review Report (Phase 6 Review Gate)
app.get('/api/projects/:id/publication-readiness', (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  try {
    const report = evaluatePublicationReadiness(pDir, pId);
    return res.json({ success: true, report });
  } catch (err) {
    console.error('[Publication Readiness Error]', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

// 12.5 Server-Side Asset Content Hash Verification (Phase 6)
app.post('/api/projects/:id/provenance/:assetId/verify-hash', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  const assetId = req.params.assetId;
  if (!assetId || typeof assetId !== 'string') {
    return res.status(400).json({ success: false, error: 'Asset ID required' });
  }

  try {
    const result = await recomputeAndStoreAssetHash(pDir, pId, assetId);
    return res.json({ success: true, ...result });
  } catch (err) {
    return res.status(400).json({ success: false, error: err.message });
  }
});

// ==========================================
// 12.6 Concat NLE Integration Endpoints (Step 2)
// ==========================================

// 12.6.1 Export Project to Concat Format
app.post('/api/projects/:id/export-concat', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  try {
    await runPythonScript(['-m', 'core.concat_exporter', pId], BASE_DIR);
    
    const concatJsonPath = path.join(pDir, 'concat_project', 'concat.json');
    if (!fs.existsSync(concatJsonPath)) {
      throw new Error('Export script finished but concat.json was not created.');
    }

    const concatDoc = JSON.parse(fs.readFileSync(concatJsonPath, 'utf-8'));
    return res.json({
      success: true,
      message: 'Project successfully exported to Concat format',
      concatProjectDir: path.join(pDir, 'concat_project'),
      concatJsonPath,
      timelines: concatDoc.timelines ? concatDoc.timelines.map(t => t.name) : [],
      scenesCount: concatDoc.clips ? concatDoc.clips.filter(c => c.trackId === 'T_VIDEO').length : 0
    });
  } catch (err) {
    console.error('[Concat Export Error]', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

// 12.6.2 Open in Concat Desktop Application (Smart Auto-Sync & Detached Process)
app.post('/api/projects/:id/open-in-concat', async (req, res) => {
  const pId = req.params.id;
  const pDir = req.projectDir || resolveValidatedProjectDir(pId);
  if (!pDir) return res.status(400).json({ success: false, error: 'Invalid project ID' });
  if (!fs.existsSync(pDir)) return res.status(404).json({ success: false, error: 'Project not found' });

  // 1. Resolve Concat Binary
  const concatBin = resolveConcatBinary();
  if (!concatBin) {
    return res.status(404).json({
      success: false,
      error: 'Concat executable (concat.exe) was not found on this system.',
      downloadUrl: 'https://concatenate.pages.dev/#download'
    });
  }

  const concatProjectDir = path.join(pDir, 'concat_project');
  const concatJsonPath = path.join(concatProjectDir, 'concat.json');

  // 2. Smart Auto-Sync: Check if export needed, outdated, or track order invalid
  let needsExport = !fs.existsSync(concatJsonPath);
  if (!needsExport) {
    try {
      const concatStat = fs.statSync(concatJsonPath);
      const voiceTlPath = path.join(pDir, 'voice_timeline.json');
      const scriptPath = path.join(pDir, 'script.json');
      const exporterPath = path.join(BASE_DIR, 'core', 'concat_exporter.py');

      if (fs.existsSync(exporterPath) && fs.statSync(exporterPath).mtimeMs > concatStat.mtimeMs) {
        needsExport = true;
      } else if (fs.existsSync(voiceTlPath) && fs.statSync(voiceTlPath).mtimeMs > concatStat.mtimeMs) {
        needsExport = true;
      } else if (fs.existsSync(scriptPath) && fs.statSync(scriptPath).mtimeMs > concatStat.mtimeMs) {
        needsExport = true;
      } else {
        // Deep validate track ordering in existing concat.json:
        // Concat's GPU compositor requires T_VIDEO (background) before T_SUB (overlay)
        const doc = JSON.parse(fs.readFileSync(concatJsonPath, 'utf-8'));
        const tracks = doc.tracks || [];
        const vIdx = tracks.findIndex(t => t.id === 'T_VIDEO');
        const sIdx = tracks.findIndex(t => t.id === 'T_SUB');
        if (vIdx === -1 || sIdx === -1 || vIdx >= sIdx) {
          needsExport = true;
        }
      }
    } catch (_) {
      needsExport = true;
    }
  }

  try {
    // Terminate any stale concat.exe instances before export/launch
    // to prevent background in-memory state from overwriting concat.json on disk
    if (process.platform === 'win32') {
      try {
        spawnSync('taskkill', ['/F', '/IM', 'concat.exe'], { windowsHide: true });
      } catch (_) {}
    }

    if (needsExport) {
      console.log(`[*] Auto-syncing Concat project for ${pId} before launch...`);
      await runPythonScript(['-m', 'core.concat_exporter', pId], BASE_DIR);
    }

    // 3. Launch Concat as Fully Detached Process
    console.log(`[*] Launching Concat: "${concatBin}" --project "${concatProjectDir}"`);
    const child = spawn(concatBin, ['--project', concatProjectDir], {
      detached: true,
      stdio: 'ignore',
      windowsHide: false
    });
    child.unref();

    return res.json({
      success: true,
      message: 'Concat application launched successfully',
      pid: child.pid,
      concatProjectDir,
      autoSynced: needsExport
    });
  } catch (err) {
    console.error('[Concat Launch Error]', err);
    return res.status(500).json({ success: false, error: err.message });
  }
});

// 13. Quick Preview TTS line (SEC-01 Safe Implementation)
app.post('/api/tts-preview', async (req, res) => {
  const {
    text,
    voice = 'th-TH-NiwatNeural',
    rate = '+5%',
    action_type,
    voice_direction,
    pitch
  } = req.body || {};

  // Server-side validation
  if (!text || typeof text !== 'string') {
    return res.status(400).json({ success: false, error: 'Text required and must be a string' });
  }
  const trimmedText = text.trim();
  if (trimmedText.length === 0 || trimmedText.length > MAX_PREVIEW_TEXT_LENGTH) {
    return res.status(400).json({
      success: false,
      error: `Text length must be between 1 and ${MAX_PREVIEW_TEXT_LENGTH} characters`
    });
  }

  if (!ALLOWED_TTS_VOICES.has(voice)) {
    return res.status(400).json({ success: false, error: 'Invalid voice selection' });
  }

  if (typeof rate !== 'string' || !TTS_RATE_REGEX.test(rate)) {
    return res.status(400).json({ success: false, error: 'Invalid speech rate format' });
  }
  const rateNum = parseInt(rate.replace('%', '').replace('+', ''), 10);
  if (isNaN(rateNum) || rateNum < -50 || rateNum > 100) {
    return res.status(400).json({ success: false, error: 'Speech rate must be between -50% and +100%' });
  }

  if (action_type !== undefined && action_type !== null) {
    if (typeof action_type !== 'string' || !ALLOWED_ACTION_TYPES.has(action_type)) {
      return res.status(400).json({ success: false, error: 'Invalid action_type parameter' });
    }
  }

  if (voice_direction !== undefined && voice_direction !== null) {
    if (typeof voice_direction !== 'string' || !ALLOWED_VOICE_DIRECTIONS.has(voice_direction)) {
      return res.status(400).json({ success: false, error: 'Invalid voice_direction parameter' });
    }
  }

  if (pitch !== undefined && pitch !== null) {
    if (typeof pitch !== 'string' || !TTS_PITCH_REGEX.test(pitch)) {
      return res.status(400).json({ success: false, error: 'Invalid pitch format' });
    }
  }

  // Safe internal temporary file generation
  const tempDir = path.resolve(PROJECTS_DIR, '_temp');
  const tempId = crypto.randomBytes(16).toString('hex');
  const tempFile = path.join(tempDir, `preview_${tempId}.mp3`);

  const safeCleanup = () => {
    try {
      if (fs.existsSync(tempFile)) {
        fs.unlinkSync(tempFile);
      }
    } catch (_) {}
  };

  let proc = null;
  let timer = null;

  try {
    fs.mkdirSync(tempDir, { recursive: true });

    const pyExe = resolvePythonInterpreter();
    const pyScript = path.join(CORE_DIR, 'tts_preview.py');

    await new Promise((resolve, reject) => {
      let isSettled = false;

      proc = spawn(pyExe, [pyScript], {
        env: {
          PATH: process.env.PATH,
          SYSTEMROOT: process.env.SYSTEMROOT,
          LOCALAPPDATA: process.env.LOCALAPPDATA,
          VIRTUAL_ENV: process.env.VIRTUAL_ENV,
          PYTHON_PATH: process.env.PYTHON_PATH,
          PYTHONIOENCODING: 'utf-8'
        }
      });

      timer = setTimeout(() => {
        if (!isSettled) {
          isSettled = true;
          try { proc.kill(); } catch (_) {}
          const err = new Error('TTS preview generation timed out');
          err.status = 504;
          reject(err);
        }
      }, TTS_TIMEOUT_MS);

      proc.on('error', (err) => {
        if (!isSettled) {
          isSettled = true;
          clearTimeout(timer);
          reject(new Error('Failed to start TTS process'));
        }
      });

      proc.on('close', (code) => {
        if (!isSettled) {
          isSettled = true;
          clearTimeout(timer);
          if (code === 0) {
            resolve();
          } else {
            reject(new Error('TTS generation failed'));
          }
        }
      });

      // Write structured JSON to stdin
      const payload = JSON.stringify({
        text: trimmedText,
        voice,
        rate,
        action_type: action_type || undefined,
        voice_direction: voice_direction || undefined,
        pitch: pitch || undefined,
        output_path: tempFile
      });
      proc.stdin.write(payload, 'utf-8', () => {
        proc.stdin.end();
      });
    });

    if (!fs.existsSync(tempFile)) {
      safeCleanup();
      return res.status(500).json({ success: false, error: 'TTS audio file not generated' });
    }

    const stat = fs.statSync(tempFile);
    if (stat.size === 0 || stat.size > MAX_AUDIO_BYTES) {
      safeCleanup();
      return res.status(500).json({ success: false, error: 'Generated audio invalid or exceeded size limit' });
    }

    res.sendFile(tempFile, () => {
      safeCleanup();
    });
  } catch (err) {
    safeCleanup();
    const status = err.status || 500;
    const errorMsg = status === 504 ? 'TTS preview generation timed out' : 'TTS preview generation failed';
    res.status(status).json({ success: false, error: errorMsg });
  }
});

// 14. File Upload endpoint (Hardened Phase 3)
app.post('/api/upload', (req, res) => {
  upload.single('videoFile')(req, res, async (err) => {
    if (err) {
      const status = err.statusCode || (err instanceof multer.MulterError ? 400 : 400);
      return res.status(status).json({ success: false, error: err.message });
    }
    if (!req.file) {
      return res.status(400).json({ success: false, error: 'กรุณาเลือกไฟล์วิดีโอ' });
    }

    const { uploadId, stagedPath, size } = req.file;

    try {
      const mediaInfo = await validateMediaWithFFprobe(stagedPath);

      const ext = path.extname(req.file.originalname).toLowerCase();
      const sanitizedBase = path.basename(req.file.originalname, ext).replace(/[^a-zA-Z0-9_\u0E00-\u0E7F-]/g, '_').substring(0, 50);
      const storedFilename = `${uploadId}-${sanitizedBase}${ext}`;
      const promotedPath = path.join(UPLOADS_DIR, storedFilename);

      fs.renameSync(stagedPath, promotedPath);

      const entry = {
        uploadId,
        filename: req.file.originalname,
        storedFilename,
        relPath: path.relative(BASE_DIR, promotedPath).replace(/\\/g, '/'),
        size,
        mimeType: req.file.mimetype,
        mediaInfo,
        status: 'ready',
        uploadedAt: new Date().toISOString(),
        legacy: false
      };

      await updateManifest((m) => {
        m.uploads[uploadId] = entry;
      });

      res.json({
        success: true,
        uploadId,
        filePath: promotedPath,
        filename: req.file.originalname,
        size
      });
    } catch (valErr) {
      try {
        if (fs.existsSync(stagedPath)) fs.unlinkSync(stagedPath);
      } catch (_) {}
      return res.status(400).json({
        success: false,
        error: `Media validation failed: ${valErr.message}`
      });
    }
  });
});

if (require.main === module) {
  app.listen(PORT, BIND_HOST, () => {
    console.log(`=======================================================`);
    console.log(`🚀 Anime Recap Studio (Production Ready) is running!`);
    console.log(`🌐 Bound Host: ${BIND_HOST} (Remote Access: ${ALLOW_REMOTE ? 'ENABLED' : 'DISABLED - Loopback Only'})`);
    console.log(`🌐 Local URL: http://${BIND_HOST === '0.0.0.0' ? 'localhost' : BIND_HOST}:${PORT}`);
    console.log(`📂 Workspace: ${BASE_DIR}`);
    console.log(`=======================================================`);
  });
}

module.exports = {
  app,
  validateProjectId,
  resolveValidatedProjectDir,
  getUploadEntry,
  resolveAuthorizedVideoPath,
  updateManifest,
  checkDiskSpaceAvailable,
  validateMediaWithFFprobe,
  UPLOADS_DIR,
  MANIFEST_PATH,
  BASELINE_PATH,
  BIND_HOST,
  ALLOW_REMOTE,
  isAllowedHost,
  isAllowedOrigin,
  parseHost,
  getAllowedRemoteHosts,
  resolvePythonInterpreter,
  resolveConcatBinary,
  PYTHON_PATH,
  executeTrimTransaction,
  recoverProjectTrimTransactions,
  runWithProjectLock,
  loadProjectProvenance,
  migrateProjectProvenance,
  registerExportProvenance,
  updateAssetProvenance,
  evaluatePublicationReadiness,
  recomputeAndStoreAssetHash,
  verifyAssetContentHash,
  computeFileSha256,
  safeWriteJson,
  RIGHTS_STATUS,
  ASSET_TYPE,
  READINESS_STATUS,
  READINESS_LABELS
};
