/**
 * Asset Provenance and Rights-Review Engine (Phase 6)
 * Anime Recap Pipeline — สปอย
 *
 * Implements an auditable, versioned asset provenance manifest (projects/<id>/asset_manifest.json)
 * and publication-readiness evaluation.
 *
 * Principles:
 * - Honest rights representation: explicit documented states (unknown, needs_review, authorized, restricted, expired).
 * - No automated fair-use or copyright-immunity claims.
 * - Decision-support only; never legal clearance.
 * - Exact parent-child lineage tracking from source video through frames and clips to final exports.
 * - Streaming SHA-256 computation to avoid memory spikes on large media files.
 * - Concurrency-safe atomic file writes.
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const BASE_DIR = path.resolve(__dirname, '..');
const PROJECTS_DIR = path.join(BASE_DIR, 'projects');
const ASSETS_DIR = path.join(BASE_DIR, 'assets');

const SCHEMA_VERSION = 1;

// Rights Statuses (Strictly documented, no ambiguous states)
const RIGHTS_STATUS = {
  UNKNOWN: 'unknown',
  NEEDS_REVIEW: 'needs_review',
  AUTHORIZED: 'authorized',
  RESTRICTED: 'restricted',
  EXPIRED: 'expired'
};

// Asset Types
const ASSET_TYPE = {
  SOURCE_VIDEO: 'source_video',
  EXTRACTED_FRAME: 'extracted_frame',
  VISUAL_CLIP: 'visual_clip',
  NARRATION: 'narration',
  SUBTITLES: 'subtitles',
  MUSIC: 'music',
  FONT: 'font',
  FINAL_EXPORT: 'final_export'
};

// Publication-Readiness Status
const READINESS_STATUS = {
  REVIEW_REQUIRED: 'review_required',
  RESTRICTED: 'restricted',
  CHECKLIST_COMPLETE: 'checklist_complete'
};

const READINESS_LABELS = {
  [READINESS_STATUS.REVIEW_REQUIRED]: 'Review Required',
  [READINESS_STATUS.RESTRICTED]: 'Restricted',
  [READINESS_STATUS.CHECKLIST_COMPLETE]: 'Checklist Complete — Not Legal Clearance'
};

const LEGAL_DISCLAIMER =
  'This report is a technical decision-support tool documenting recorded provenance and license metadata. ' +
  'It does not constitute legal advice, does not establish fair use or copyright immunity, and does not guarantee ' +
  'monetization eligibility or the absence of platform Content ID or copyright claims.';

// Mutable fields allowlist for PUT updates
const MUTABLE_PROVENANCE_FIELDS = new Set([
  'rightsStatus',
  'license',
  'licenseUrl',
  'creator',
  'sourceUrl',
  'evidence',
  'attribution',
  'commercialUse',
  'derivativeWorks',
  'restrictions',
  'reviewerNotes',
  'reviewedBy'
]);

const IMMUTABLE_PROVENANCE_FIELDS = new Set([
  'assetId',
  'assetType',
  'path',
  'managedPath',
  'sha256',
  'hash',
  'filename',
  'parentAssetIds',
  'createdAt',
  'size',
  'uploadId',
  'originalSourceRef',
  'sourceRef'
]);

/**
 * Compute SHA-256 hash using streaming read to avoid loading large files into memory.
 */
function computeFileSha256(filePath) {
  return new Promise((resolve, reject) => {
    if (!fs.existsSync(filePath)) {
      return reject(new Error(`File not found for hash calculation: ${filePath}`));
    }
    const hash = crypto.createHash('sha256');
    const stream = fs.createReadStream(filePath);
    stream.on('data', chunk => hash.update(chunk));
    stream.on('end', () => resolve(hash.digest('hex')));
    stream.on('error', err => reject(err));
  });
}

/**
 * Verify asset content against expected SHA-256 hash.
 */
async function verifyAssetContentHash(filePath, expectedSha256) {
  if (!fs.existsSync(filePath)) {
    return { verified: false, error: 'File does not exist on disk' };
  }
  const actualHash = await computeFileSha256(filePath);
  return {
    verified: actualHash.toLowerCase() === expectedSha256.toLowerCase(),
    actualHash,
    expectedSha256
  };
}

/**
 * Atomically write JSON data to file.
 */
function safeWriteJson(targetPath, data) {
  const dir = path.dirname(targetPath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  const tmpFile = path.join(dir, `.tmp_${path.basename(targetPath)}_${Date.now()}_${crypto.randomBytes(4).toString('hex')}`);
  fs.writeFileSync(tmpFile, JSON.stringify(data, null, 2), 'utf-8');
  try {
    fs.renameSync(tmpFile, targetPath);
  } catch (_) {
    fs.writeFileSync(targetPath, JSON.stringify(data, null, 2), 'utf-8');
    try { fs.unlinkSync(tmpFile); } catch (_) {}
  }
}

function getAssetManifestPath(pDir) {
  return path.join(pDir, 'asset_manifest.json');
}

/**
 * Read-only load of asset manifest.
 * Preserves and reports malformed legacy data rather than silently discarding.
 */
function loadProjectProvenance(pDir) {
  const manifestPath = getAssetManifestPath(pDir);
  if (!fs.existsSync(manifestPath)) return null;
  try {
    const raw = fs.readFileSync(manifestPath, 'utf-8');
    return JSON.parse(raw);
  } catch (err) {
    console.warn(`[Provenance] Warning reading ${manifestPath}: ${err.message}`);
    // Preserve malformed legacy file rather than silently discarding
    try {
      const bakPath = path.join(pDir, `asset_manifest.malformed.${Date.now()}.bak`);
      fs.copyFileSync(manifestPath, bakPath);
      return {
        malformed: true,
        error: err.message,
        preservedPath: bakPath,
        rawPath: manifestPath
      };
    } catch (_) {
      return {
        malformed: true,
        error: err.message,
        rawPath: manifestPath
      };
    }
  }
}

/**
 * Recompute SHA-256 server-side from disk asset and update provenance manifest.
 */
async function recomputeAndStoreAssetHash(pDir, pId, assetId) {
  const manifest = loadProjectProvenance(pDir);
  if (!manifest || manifest.malformed) {
    throw new Error(`Valid asset manifest not found for project ${pId}`);
  }
  const asset = manifest.assets[assetId];
  if (!asset) {
    throw new Error(`Asset ID "${assetId}" not found in project provenance manifest.`);
  }
  const fullPath = path.resolve(BASE_DIR, asset.path);
  if (!fs.existsSync(fullPath)) {
    throw new Error(`Physical asset file does not exist on disk: ${asset.path}`);
  }
  const hash = await computeFileSha256(fullPath);
  asset.sha256 = hash;
  asset.hashVerifiedAt = new Date().toISOString();
  manifest.updatedAt = new Date().toISOString();
  const manifestPath = getAssetManifestPath(pDir);
  safeWriteJson(manifestPath, manifest);
  return { assetId, sha256: hash, verifiedAt: asset.hashVerifiedAt };
}

/**
 * Idempotently migrate an existing project to the asset provenance manifest.
 * Preserves all existing verified fields and operational files.
 */
async function migrateProjectProvenance(pDir, pId, options = {}) {
  const manifestPath = getAssetManifestPath(pDir);
  let manifest = {
    version: SCHEMA_VERSION,
    projectId: pId,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    assets: {}
  };

  if (fs.existsSync(manifestPath)) {
    try {
      const existing = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));
      if (existing && typeof existing === 'object' && existing.assets) {
        manifest = existing;
      }
    } catch (_) {}
  }

  const existingAssets = manifest.assets || {};

  // Helper to preserve or register asset
  const registerAsset = (assetId, assetData) => {
    if (existingAssets[assetId]) {
      // Preserve verified user-supplied fields
      const prev = existingAssets[assetId];
      existingAssets[assetId] = {
        ...assetData,
        rightsStatus: prev.rightsStatus || assetData.rightsStatus,
        evidence: prev.evidence !== undefined ? prev.evidence : assetData.evidence,
        license: prev.license || assetData.license,
        licenseUrl: prev.licenseUrl || assetData.licenseUrl,
        creator: prev.creator || assetData.creator,
        sourceUrl: prev.sourceUrl || assetData.sourceUrl,
        attribution: prev.attribution || assetData.attribution,
        commercialUse: prev.commercialUse || assetData.commercialUse,
        derivativeWorks: prev.derivativeWorks || assetData.derivativeWorks,
        restrictions: prev.restrictions || assetData.restrictions,
        reviewerNotes: prev.reviewerNotes || assetData.reviewerNotes,
        reviewedBy: prev.reviewedBy || assetData.reviewedBy,
        reviewedAt: prev.reviewedAt || assetData.reviewedAt,
        createdAt: prev.createdAt || assetData.createdAt
      };
    } else {
      existingAssets[assetId] = assetData;
    }
  };

  // 1. Source Video
  let sourceVideoAssetId = null;
  const videoMetaPath = path.join(pDir, 'video_meta.json');
  if (fs.existsSync(videoMetaPath)) {
    try {
      const vMeta = JSON.parse(fs.readFileSync(videoMetaPath, 'utf-8'));
      const videoPath = vMeta.videoPath;
      if (videoPath && fs.existsSync(videoPath)) {
        sourceVideoAssetId = `src_video_${vMeta.uploadId || crypto.createHash('md5').update(videoPath).digest('hex').substring(0, 8)}`;
        const stat = fs.statSync(videoPath);
        registerAsset(sourceVideoAssetId, {
          assetId: sourceVideoAssetId,
          assetType: ASSET_TYPE.SOURCE_VIDEO,
          filename: path.basename(videoPath),
          path: path.relative(BASE_DIR, videoPath).replace(/\\/g, '/'),
          uploadId: vMeta.uploadId || null,
          size: stat.size,
          rightsStatus: RIGHTS_STATUS.NEEDS_REVIEW,
          evidence: null,
          license: 'Unknown / Third-Party Anime Source',
          licenseUrl: null,
          creator: 'Unknown',
          sourceUrl: null,
          attribution: null,
          commercialUse: 'unknown',
          derivativeWorks: 'unknown',
          restrictions: 'Source video contains third-party anime footage requiring copyright review.',
          parentAssetIds: [],
          createdAt: vMeta.updatedAt || new Date().toISOString(),
          reviewerNotes: 'Uploaded anime source video. Requires human rights verification.'
        });
      }
    } catch (_) {}
  }

  // 2. Extracted Frames
  const framesManifestPath = path.join(pDir, 'frames_manifest.json');
  if (fs.existsSync(framesManifestPath)) {
    try {
      const frames = JSON.parse(fs.readFileSync(framesManifestPath, 'utf-8'));
      if (Array.isArray(frames)) {
        for (const f of frames) {
          const frameAssetId = `frame_${f.id || f.index}`;
          const frameRelPath = f.path || `projects/${pId}/frames/${f.filename || f.id + '.jpg'}`;
          const frameFullPath = path.resolve(BASE_DIR, frameRelPath);
          const fSize = fs.existsSync(frameFullPath) ? fs.statSync(frameFullPath).size : 0;
          registerAsset(frameAssetId, {
            assetId: frameAssetId,
            assetType: ASSET_TYPE.EXTRACTED_FRAME,
            filename: f.filename || `${f.id}.jpg`,
            path: frameRelPath.replace(/\\/g, '/'),
            size: fSize,
            timestamp: f.timestamp || 0,
            frameIndex: f.index || 0,
            rightsStatus: RIGHTS_STATUS.NEEDS_REVIEW,
            evidence: null,
            license: 'Derived from Source Video',
            licenseUrl: null,
            creator: 'Extracted via Pipeline',
            attribution: null,
            commercialUse: 'unknown',
            derivativeWorks: 'unknown',
            restrictions: 'Extracted keyframe inheriting third-party source footage restrictions.',
            parentAssetIds: sourceVideoAssetId ? [sourceVideoAssetId] : [],
            createdAt: manifest.createdAt,
            reviewerNotes: null
          });
        }
      }
    } catch (_) {}
  }

  // 3. Visual Clips (16:9 and 9:16)
  for (const aspect of ['16_9', '9_16']) {
    const clipsDir = path.join(pDir, `temp_clips_${aspect}`);
    const clipsManifestPath = path.join(clipsDir, 'clips_manifest.json');
    if (fs.existsSync(clipsManifestPath)) {
      try {
        const cMeta = JSON.parse(fs.readFileSync(clipsManifestPath, 'utf-8'));
        const clips = cMeta.clips || {};
        for (const [clipName, clipInfo] of Object.entries(clips)) {
          const clipAssetId = `clip_${aspect}_${clipName.replace('.mp4', '')}`;
          const clipPath = path.join(clipsDir, clipName);
          const cSize = fs.existsSync(clipPath) ? fs.statSync(clipPath).size : 0;
          const parentFrameName = clipInfo.image_name;
          const parentFrameId = parentFrameName ? `frame_${parentFrameName.replace(/\.[^.]+$/, '')}` : null;
          registerAsset(clipAssetId, {
            assetId: clipAssetId,
            assetType: ASSET_TYPE.VISUAL_CLIP,
            filename: clipName,
            aspectRatio: aspect.replace('_', ':'),
            path: path.relative(BASE_DIR, clipPath).replace(/\\/g, '/'),
            size: cSize,
            motion: clipInfo.motion || 'unknown',
            duration: clipInfo.duration || 0,
            fingerprint: clipInfo.fingerprint || null,
            rightsStatus: RIGHTS_STATUS.NEEDS_REVIEW,
            evidence: null,
            license: 'Derived Ken Burns Motion Clip',
            parentAssetIds: parentFrameId && existingAssets[parentFrameId] ? [parentFrameId] : (sourceVideoAssetId ? [sourceVideoAssetId] : []),
            createdAt: manifest.createdAt,
            reviewerNotes: null
          });
        }
      } catch (_) {}
    }
  }

  // 4. Bundled Fonts (Kanit OFL Fonts)
  const fontRegularPath = path.join(ASSETS_DIR, 'fonts', 'Kanit-Regular.ttf');
  const fontBoldPath = path.join(ASSETS_DIR, 'fonts', 'Kanit-Bold.ttf');
  const oflLicensePath = path.join(ASSETS_DIR, 'fonts', 'OFL.txt');
  if (fs.existsSync(fontRegularPath)) {
    registerAsset('font_kanit_regular', {
      assetId: 'font_kanit_regular',
      assetType: ASSET_TYPE.FONT,
      filename: 'Kanit-Regular.ttf',
      path: path.relative(BASE_DIR, fontRegularPath).replace(/\\/g, '/'),
      size: fs.statSync(fontRegularPath).size,
      rightsStatus: RIGHTS_STATUS.AUTHORIZED,
      evidence: 'SIL Open Font License 1.1 (bundled in assets/fonts/OFL.txt)',
      license: 'OFL-1.1',
      licenseUrl: 'https://scripts.sil.org/OFL',
      creator: 'Cadson Demak',
      sourceUrl: 'https://fonts.google.com/specimen/Kanit',
      attribution: 'Font: Kanit by Cadson Demak, licensed under SIL Open Font License 1.1',
      commercialUse: 'permitted',
      derivativeWorks: 'permitted',
      restrictions: 'Font software cannot be sold by itself (OFL clause 1).',
      parentAssetIds: [],
      createdAt: manifest.createdAt,
      reviewedBy: 'System Asset Catalog',
      reviewedAt: manifest.createdAt,
      reviewerNotes: 'Verified SIL Open Font License 1.1'
    });
  }
  if (fs.existsSync(fontBoldPath)) {
    registerAsset('font_kanit_bold', {
      assetId: 'font_kanit_bold',
      assetType: ASSET_TYPE.FONT,
      filename: 'Kanit-Bold.ttf',
      path: path.relative(BASE_DIR, fontBoldPath).replace(/\\/g, '/'),
      size: fs.statSync(fontBoldPath).size,
      rightsStatus: RIGHTS_STATUS.AUTHORIZED,
      evidence: 'SIL Open Font License 1.1 (bundled in assets/fonts/OFL.txt)',
      license: 'OFL-1.1',
      licenseUrl: 'https://scripts.sil.org/OFL',
      creator: 'Cadson Demak',
      sourceUrl: 'https://fonts.google.com/specimen/Kanit',
      attribution: 'Font: Kanit by Cadson Demak, licensed under SIL Open Font License 1.1',
      commercialUse: 'permitted',
      derivativeWorks: 'permitted',
      restrictions: 'Font software cannot be sold by itself (OFL clause 1).',
      parentAssetIds: [],
      createdAt: manifest.createdAt,
      reviewedBy: 'System Asset Catalog',
      reviewedAt: manifest.createdAt,
      reviewerNotes: 'Verified SIL Open Font License 1.1'
    });
  }

  // 5. Narration Audio
  for (const masterAudioName of ['voiceover_master.wav', 'voiceover_master.mp3']) {
    const aPath = path.join(pDir, masterAudioName);
    if (fs.existsSync(aPath)) {
      const aId = `narration_${masterAudioName.replace('.', '_')}`;
      registerAsset(aId, {
        assetId: aId,
        assetType: ASSET_TYPE.NARRATION,
        filename: masterAudioName,
        path: path.relative(BASE_DIR, aPath).replace(/\\/g, '/'),
        size: fs.statSync(aPath).size,
        rightsStatus: RIGHTS_STATUS.NEEDS_REVIEW,
        evidence: null,
        license: 'AI Synthesized Narration (Edge-TTS)',
        licenseUrl: null,
        creator: 'Recap Studio Narration Engine',
        attribution: null,
        commercialUse: 'unknown',
        derivativeWorks: 'permitted',
        restrictions: 'Review Microsoft Edge-TTS service terms for intended deployment platform.',
        parentAssetIds: [],
        createdAt: manifest.createdAt,
        reviewerNotes: 'Synthesized voiceover track. Verify script originality and TTS service terms.'
      });
      break;
    }
  }

  // 6. Subtitles
  for (const subName of ['subtitles.ass', 'subtitles.srt']) {
    const sPath = path.join(pDir, subName);
    if (fs.existsSync(sPath)) {
      const sId = `sub_${subName.replace('.', '_')}`;
      registerAsset(sId, {
        assetId: sId,
        assetType: ASSET_TYPE.SUBTITLES,
        filename: subName,
        path: path.relative(BASE_DIR, sPath).replace(/\\/g, '/'),
        size: fs.statSync(sPath).size,
        rightsStatus: RIGHTS_STATUS.NEEDS_REVIEW,
        evidence: null,
        license: 'Original Editorial Commentary Subtitles',
        licenseUrl: null,
        creator: 'Recap Script Author',
        attribution: null,
        commercialUse: 'unknown',
        derivativeWorks: 'permitted',
        restrictions: null,
        parentAssetIds: [],
        createdAt: manifest.createdAt,
        reviewerNotes: 'Subtitles generated from editorial recap script.'
      });
    }
  }

  // 7. Final Video Exports (16:9 and 9:16)
  for (const aspect of ['16_9', '9_16']) {
    const exportName = `recap_${aspect}_master.mp4`;
    const exportPath = path.join(pDir, exportName);
    if (fs.existsSync(exportPath)) {
      const expAssetId = `export_${aspect}`;
      const stat = fs.statSync(exportPath);

      // Collect exact parent asset IDs
      const parentIds = [];
      let lineageStatus = 'complete';
      // Visual clips of this aspect ratio
      const clipPrefix = `clip_${aspect}_`;
      for (const k of Object.keys(existingAssets)) {
        if (k.startsWith(clipPrefix)) parentIds.push(k);
      }
      // If no clips registered, link to frames or source video and mark lineage incomplete
      if (parentIds.length === 0) {
        if (sourceVideoAssetId) {
          parentIds.push(sourceVideoAssetId);
          lineageStatus = 'incomplete';
        } else {
          lineageStatus = 'unknown';
        }
      }
      // Voiceover
      for (const k of Object.keys(existingAssets)) {
        if (k.startsWith('narration_')) { parentIds.push(k); break; }
      }
      // Subtitles & Fonts: only link fonts if subtitles were actually used/present!
      let hasSubtitles = false;
      for (const k of Object.keys(existingAssets)) {
        if (k.startsWith('sub_')) {
          parentIds.push(k);
          hasSubtitles = true;
          break;
        }
      }
      if (hasSubtitles) {
        if (existingAssets['font_kanit_regular']) parentIds.push('font_kanit_regular');
        if (existingAssets['font_kanit_bold']) parentIds.push('font_kanit_bold');
      }

      registerAsset(expAssetId, {
        assetId: expAssetId,
        assetType: ASSET_TYPE.FINAL_EXPORT,
        filename: exportName,
        aspectRatio: aspect.replace('_', ':'),
        path: path.relative(BASE_DIR, exportPath).replace(/\\/g, '/'),
        size: stat.size,
        rightsStatus: RIGHTS_STATUS.NEEDS_REVIEW,
        evidence: null,
        license: 'Composite Video Work',
        licenseUrl: null,
        creator: 'Anime Recap Pipeline',
        attribution: null,
        commercialUse: 'unknown',
        derivativeWorks: 'unknown',
        restrictions: 'Composite export depends on third-party source footage and BGM. Requires publication-readiness review.',
        parentAssetIds: parentIds,
        lineageStatus,
        createdAt: manifest.createdAt,
        reviewerNotes: 'Final rendered video master. Review dependencies before external publication.'
      });
    }
  }

  manifest.assets = existingAssets;
  manifest.updatedAt = new Date().toISOString();

  // Atomically persist manifest
  safeWriteJson(manifestPath, manifest);
  return manifest;
}

/**
 * Register a newly completed final export asset into the provenance manifest.
 * Links to exact visual clips, voiceover, BGM, subtitles, and fonts actually used.
 */
async function registerExportProvenance(pDir, pId, { aspect, bgm, subtitles, filename }) {
  const manifestPath = getAssetManifestPath(pDir);
  let manifest = loadProjectProvenance(pDir);
  if (!manifest || manifest.malformed) {
    manifest = await migrateProjectProvenance(pDir, pId);
  }

  const exportPath = path.join(pDir, filename);
  if (!fs.existsSync(exportPath)) {
    throw new Error(`Exported video file not found on disk: ${exportPath}`);
  }

  const stat = fs.statSync(exportPath);
  const aspectSafe = aspect.replace(':', '_');
  const exportAssetId = `export_${aspectSafe}`;

  // Compute streaming SHA-256 for the new export
  let exportHash = null;
  try {
    exportHash = await computeFileSha256(exportPath);
  } catch (hErr) {
    console.warn(`[Provenance] Warning computing export hash: ${hErr.message}`);
  }

  // Exact parent lineage collection
  const parentAssetIds = [];
  let lineageStatus = 'complete';

  // 1. Exact visual clips for this aspect ratio
  const clipPrefix = `clip_${aspectSafe}_`;
  for (const [k, a] of Object.entries(manifest.assets)) {
    if (k.startsWith(clipPrefix) && a.assetType === ASSET_TYPE.VISUAL_CLIP) {
      parentAssetIds.push(k);
    }
  }
  // Fallback: If clips weren't individually registered, link to frames and mark incomplete
  if (parentAssetIds.length === 0) {
    for (const [k, a] of Object.entries(manifest.assets)) {
      if (a.assetType === ASSET_TYPE.EXTRACTED_FRAME) {
        parentAssetIds.push(k);
      }
    }
    if (parentAssetIds.length > 0) {
      lineageStatus = 'incomplete';
    } else {
      lineageStatus = 'unknown';
    }
  }

  // 2. Exact Narration Master
  for (const [k, a] of Object.entries(manifest.assets)) {
    if (a.assetType === ASSET_TYPE.NARRATION) {
      parentAssetIds.push(k);
      break;
    }
  }

  // 3. Exact BGM asset used
  if (bgm && bgm !== 'none') {
    const bgmAssetId = `music_${path.basename(bgm, path.extname(bgm))}`;
    const bgmPath = path.join(ASSETS_DIR, 'bgm', bgm);
    if (!manifest.assets[bgmAssetId] && fs.existsSync(bgmPath)) {
      const bgmStat = fs.statSync(bgmPath);
      manifest.assets[bgmAssetId] = {
        assetId: bgmAssetId,
        assetType: ASSET_TYPE.MUSIC,
        filename: bgm,
        path: path.relative(BASE_DIR, bgmPath).replace(/\\/g, '/'),
        size: bgmStat.size,
        rightsStatus: RIGHTS_STATUS.NEEDS_REVIEW,
        evidence: null,
        license: 'Royalty-Free BGM (bundled)',
        licenseUrl: null,
        creator: 'Bundled Asset Library',
        attribution: null,
        commercialUse: 'unknown',
        derivativeWorks: 'permitted',
        restrictions: 'Review music library license terms for commercial monetization.',
        parentAssetIds: [],
        createdAt: new Date().toISOString(),
        reviewerNotes: 'Bundled background music. Verify commercial rights.'
      };
    }
    if (manifest.assets[bgmAssetId]) {
      parentAssetIds.push(bgmAssetId);
    }
  }

  // 4. Exact Subtitles & Fonts used (if subtitles were burned)
  if (subtitles) {
    for (const [k, a] of Object.entries(manifest.assets)) {
      if (a.assetType === ASSET_TYPE.SUBTITLES) {
        parentAssetIds.push(k);
        break;
      }
    }
    // Kanit OFL fonts
    if (manifest.assets['font_kanit_regular']) parentAssetIds.push('font_kanit_regular');
    if (manifest.assets['font_kanit_bold']) parentAssetIds.push('font_kanit_bold');
  }

  // Determine conservative rightsStatus for export:
  // If ANY parent is unknown, needs_review, restricted, or expired -> export cannot be authorized!
  let exportRightsStatus = RIGHTS_STATUS.NEEDS_REVIEW;
  let hasRestrictedParent = false;
  let hasExpiredParent = false;

  for (const pid of parentAssetIds) {
    const parent = manifest.assets[pid];
    if (parent) {
      if (parent.rightsStatus === RIGHTS_STATUS.RESTRICTED) hasRestrictedParent = true;
      if (parent.rightsStatus === RIGHTS_STATUS.EXPIRED) hasExpiredParent = true;
    }
  }

  if (hasExpiredParent) exportRightsStatus = RIGHTS_STATUS.EXPIRED;
  else if (hasRestrictedParent) exportRightsStatus = RIGHTS_STATUS.RESTRICTED;

  // Preserve existing reviewer notes or human attestation if export was previously audited
  const prevExport = manifest.assets[exportAssetId] || {};

  manifest.assets[exportAssetId] = {
    assetId: exportAssetId,
    assetType: ASSET_TYPE.FINAL_EXPORT,
    filename,
    aspectRatio: aspect,
    path: path.relative(BASE_DIR, exportPath).replace(/\\/g, '/'),
    size: stat.size,
    sha256: exportHash,
    rightsStatus: prevExport.rightsStatus === RIGHTS_STATUS.AUTHORIZED ? RIGHTS_STATUS.NEEDS_REVIEW : exportRightsStatus,
    evidence: prevExport.evidence || null,
    license: 'Composite Video Work',
    licenseUrl: null,
    creator: 'Anime Recap Pipeline',
    attribution: prevExport.attribution || null,
    commercialUse: prevExport.commercialUse || 'unknown',
    derivativeWorks: prevExport.derivativeWorks || 'unknown',
    restrictions: 'Composite export inheriting third-party parent media restrictions.',
    parentAssetIds,
    lineageStatus,
    createdAt: prevExport.createdAt || new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    reviewerNotes: prevExport.reviewerNotes || 'Freshly rendered master video. Review parent dependencies before publication.'
  };

  manifest.updatedAt = new Date().toISOString();
  safeWriteJson(manifestPath, manifest);
  return manifest.assets[exportAssetId];
}

/**
 * Update mutable provenance metadata for a specific asset.
 * Rejects attempts to modify immutable fields.
 */
function updateAssetProvenance(pDir, pId, assetId, updates) {
  const manifestPath = getAssetManifestPath(pDir);
  const manifest = loadProjectProvenance(pDir);
  if (!manifest || manifest.malformed) {
    throw new Error(`Asset manifest not found for project ${pId}. Please initialize project provenance first.`);
  }

  const asset = manifest.assets[assetId];
  if (!asset) {
    throw new Error(`Asset ID "${assetId}" not found in project provenance manifest.`);
  }

  // Reject immutable field modification attempts
  for (const key of Object.keys(updates)) {
    if (IMMUTABLE_PROVENANCE_FIELDS.has(key)) {
      throw new Error(`Field "${key}" is immutable and cannot be modified.`);
    }
    if (!MUTABLE_PROVENANCE_FIELDS.has(key)) {
      throw new Error(`Unrecognized or disallowed provenance field: "${key}"`);
    }
  }

  // Validate rights status transitions
  if (updates.rightsStatus) {
    const validStatuses = Object.values(RIGHTS_STATUS);
    if (!validStatuses.includes(updates.rightsStatus)) {
      throw new Error(`Invalid rightsStatus: "${updates.rightsStatus}". Allowed: ${validStatuses.join(', ')}`);
    }

    // When asserting AUTHORIZED status: require human attestation and evidence
    if (updates.rightsStatus === RIGHTS_STATUS.AUTHORIZED) {
      const reviewedBy = updates.reviewedBy || asset.reviewedBy;
      const evidence = updates.evidence || asset.evidence;
      if (!reviewedBy || typeof reviewedBy !== 'string' || reviewedBy.trim().length === 0) {
        throw new Error('Marking an asset as "authorized" requires a non-empty "reviewedBy" human identifier.');
      }
      if (!evidence || typeof evidence !== 'string' || evidence.trim().length === 0) {
        throw new Error('Marking an asset as "authorized" requires documented "evidence" (e.g. license agreement, permission reference).');
      }
      updates.reviewedAt = new Date().toISOString();
    }
  }

  // Apply allowed updates
  for (const [k, v] of Object.entries(updates)) {
    asset[k] = v;
  }

  asset.updatedAt = new Date().toISOString();
  manifest.updatedAt = new Date().toISOString();

  safeWriteJson(manifestPath, manifest);
  return asset;
}

/**
 * Evaluate publication readiness for the project.
 * Produces structured findings, honest status labels, and legal disclaimers.
 */
function evaluatePublicationReadiness(pDir, pId, options = {}) {
  const manifest = loadProjectProvenance(pDir);

  if (manifest && manifest.malformed) {
    return {
      projectId: pId,
      evaluatedAt: new Date().toISOString(),
      overallStatus: READINESS_STATUS.REVIEW_REQUIRED,
      statusLabel: READINESS_LABELS[READINESS_STATUS.REVIEW_REQUIRED],
      readyForDistribution: false,
      summary: { totalAssets: 0, authorized: 0, needsReview: 0, restricted: 0, expired: 0, unknown: 0 },
      unresolvedAssets: [],
      findings: [
        {
          severity: 'BLOCKING',
          category: 'MALFORMED_PROVENANCE_DATA',
          message: `Asset provenance manifest is corrupted: ${manifest.error}. A preserved copy was retained.`,
          remediation: 'Rebuild project provenance or restore from preserved backup.'
        }
      ],
      disclaimer: LEGAL_DISCLAIMER
    };
  }

  if (!manifest || !manifest.assets || Object.keys(manifest.assets).length === 0) {
    return {
      projectId: pId,
      evaluatedAt: new Date().toISOString(),
      overallStatus: READINESS_STATUS.REVIEW_REQUIRED,
      statusLabel: READINESS_LABELS[READINESS_STATUS.REVIEW_REQUIRED],
      readyForDistribution: false,
      summary: {
        totalAssets: 0,
        authorized: 0,
        needsReview: 0,
        restricted: 0,
        expired: 0,
        unknown: 0
      },
      unresolvedAssets: [],
      findings: [
        {
          severity: 'BLOCKING',
          category: 'PROVENANCE_MISSING',
          message: 'No asset provenance manifest found for this project. Provenance tracking must be initialized.',
          remediation: 'Run provenance migration or project build to catalog project media assets.'
        }
      ],
      disclaimer: LEGAL_DISCLAIMER
    };
  }

  const assets = Object.values(manifest.assets);
  const summary = {
    totalAssets: assets.length,
    authorized: 0,
    needsReview: 0,
    restricted: 0,
    expired: 0,
    unknown: 0
  };

  const unresolvedAssets = [];
  const findings = [];

  for (const a of assets) {
    const status = a.rightsStatus || RIGHTS_STATUS.UNKNOWN;
    if (status === RIGHTS_STATUS.AUTHORIZED) summary.authorized++;
    else if (status === RIGHTS_STATUS.NEEDS_REVIEW) summary.needsReview++;
    else if (status === RIGHTS_STATUS.RESTRICTED) summary.restricted++;
    else if (status === RIGHTS_STATUS.EXPIRED) summary.expired++;
    else summary.unknown++;

    // Unresolved assets blocking external distribution
    if (status !== RIGHTS_STATUS.AUTHORIZED) {
      unresolvedAssets.push({
        assetId: a.assetId,
        assetType: a.assetType,
        filename: a.filename,
        rightsStatus: status,
        license: a.license,
        restrictions: a.restrictions,
        parentAssetIds: a.parentAssetIds || []
      });

      if (status === RIGHTS_STATUS.EXPIRED) {
        findings.push({
          severity: 'BLOCKING',
          category: 'LICENSE_EXPIRED',
          assetId: a.assetId,
          filename: a.filename,
          message: `Asset "${a.filename}" has expired permissions.`,
          remediation: 'Renew license or replace asset before publishing.'
        });
      } else if (status === RIGHTS_STATUS.RESTRICTED) {
        findings.push({
          severity: 'BLOCKING',
          category: 'USAGE_RESTRICTED',
          assetId: a.assetId,
          filename: a.filename,
          message: `Asset "${a.filename}" has documented usage restrictions: ${a.restrictions || 'Review terms'}`,
          remediation: 'Verify whether external distribution on target platform complies with restrictions.'
        });
      } else if (status === RIGHTS_STATUS.NEEDS_REVIEW || status === RIGHTS_STATUS.UNKNOWN) {
        findings.push({
          severity: 'ACTION_REQUIRED',
          category: 'RIGHTS_UNVERIFIED',
          assetId: a.assetId,
          filename: a.filename,
          message: `Asset "${a.filename}" (${a.assetType}) rights status is "${status}".`,
          remediation: 'Conduct human rights review and record license evidence or authorization reference.'
        });
      }
    } else {
      // Asserted AUTHORIZED: verify human attestation and evidence presence
      if (!a.reviewedBy || typeof a.reviewedBy !== 'string' || a.reviewedBy.trim().length === 0 ||
          !a.evidence || typeof a.evidence !== 'string' || a.evidence.trim().length === 0) {
        findings.push({
          severity: 'BLOCKING',
          category: 'MISSING_HUMAN_ATTESTATION',
          assetId: a.assetId,
          filename: a.filename,
          message: `Asset "${a.filename}" is marked authorized without documented human attestation (reviewer ID and evidence reference).`,
          remediation: 'Record human reviewer ID and license documentation reference before publishing.'
        });
      }
    }

    // Attribution check: If attribution is required but missing
    if (a.attributionRequired && (!a.attribution || a.attribution.trim().length === 0)) {
      findings.push({
        severity: 'ACTION_REQUIRED',
        category: 'ATTRIBUTION_MISSING',
        assetId: a.assetId,
        filename: a.filename,
        message: `Asset "${a.filename}" requires credit/attribution, but attribution text is not documented.`,
        remediation: 'Add required attribution text to the video description or end credits.'
      });
    }

    // Lineage check for exports: Ensure final export has registered parent dependencies
    if (a.assetType === ASSET_TYPE.FINAL_EXPORT) {
      if (!a.parentAssetIds || a.parentAssetIds.length === 0 || a.lineageStatus === 'incomplete' || a.lineageStatus === 'unknown') {
        findings.push({
          severity: 'WARNING',
          category: 'INCOMPLETE_LINEAGE',
          assetId: a.assetId,
          filename: a.filename,
          message: `Final export "${a.filename}" has incomplete or unknown parent lineage.`,
          remediation: 'Re-render or register input dependencies to ensure full lineage traceability.'
        });
      }

      if (!a.sha256) {
        findings.push({
          severity: 'WARNING',
          category: 'UNVERIFIED_CONTENT_HASH',
          assetId: a.assetId,
          filename: a.filename,
          message: `Final export "${a.filename}" does not have a verified SHA-256 content hash.`,
          remediation: 'Compute server-side content hash for the final export artifact.'
        });
      }

      // Check unresolved parent dependencies
      if (Array.isArray(a.parentAssetIds)) {
        for (const pId of a.parentAssetIds) {
          const parent = manifest.assets[pId];
          if (parent && parent.rightsStatus !== RIGHTS_STATUS.AUTHORIZED) {
            findings.push({
              severity: 'ACTION_REQUIRED',
              category: 'UNRESOLVED_PARENT_DEPENDENCY',
              assetId: a.assetId,
              filename: a.filename,
              parentAssetId: pId,
              message: `Final export "${a.filename}" depends on parent asset "${parent.filename || pId}" which has rights status "${parent.rightsStatus}".`,
              remediation: `Complete rights review for parent dependency "${parent.filename || pId}".`
            });
          }
        }
      }
    }
  }

  // Detect uncataloged exports on disk
  for (const expName of ['recap_16_9_master.mp4', 'recap_9_16_master.mp4']) {
    const expFullPath = path.join(pDir, expName);
    if (fs.existsSync(expFullPath)) {
      const isRegistered = Object.values(manifest.assets).some(
        a => a.assetType === ASSET_TYPE.FINAL_EXPORT && a.filename === expName
      );
      if (!isRegistered) {
        findings.push({
          severity: 'BLOCKING',
          category: 'UNREGISTERED_EXPORT',
          filename: expName,
          message: `Master video "${expName}" exists on disk but is not cataloged in the asset provenance manifest.`,
          remediation: 'Run provenance migration or reconciliation before publishing.'
        });
      }
    }
  }

  // Determine overall status
  let overallStatus = READINESS_STATUS.CHECKLIST_COMPLETE;
  if (summary.restricted > 0 || summary.expired > 0) {
    overallStatus = READINESS_STATUS.RESTRICTED;
  } else if (summary.needsReview > 0 || summary.unknown > 0 || unresolvedAssets.length > 0 || findings.some(f => f.severity === 'BLOCKING')) {
    overallStatus = READINESS_STATUS.REVIEW_REQUIRED;
  }

  const readyForDistribution = overallStatus === READINESS_STATUS.CHECKLIST_COMPLETE && findings.filter(f => f.severity === 'BLOCKING').length === 0;

  return {
    projectId: pId,
    evaluatedAt: new Date().toISOString(),
    overallStatus,
    statusLabel: READINESS_LABELS[overallStatus],
    readyForDistribution,
    summary,
    unresolvedAssets,
    findings,
    disclaimer: LEGAL_DISCLAIMER
  };
}

module.exports = {
  SCHEMA_VERSION,
  RIGHTS_STATUS,
  ASSET_TYPE,
  READINESS_STATUS,
  READINESS_LABELS,
  LEGAL_DISCLAIMER,
  MUTABLE_PROVENANCE_FIELDS,
  IMMUTABLE_PROVENANCE_FIELDS,
  computeFileSha256,
  verifyAssetContentHash,
  recomputeAndStoreAssetHash,
  safeWriteJson,
  getAssetManifestPath,
  loadProjectProvenance,
  migrateProjectProvenance,
  registerExportProvenance,
  updateAssetProvenance,
  evaluatePublicationReadiness
};
