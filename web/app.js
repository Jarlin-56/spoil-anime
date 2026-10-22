// Anime Recap Pipeline - Production Ready App Logic

let currentProjectId = 'demo_project';
let currentProjectData = null;

// DOM Elements
const projectSelect = document.getElementById('projectSelect');
const btnNewProject = document.getElementById('btnNewProject');
const btnDeleteProject = document.getElementById('btnDeleteProject');
const framesGrid = document.getElementById('framesGrid');
const scriptEditorContainer = document.getElementById('scriptEditorContainer');
const statFramesCount = document.getElementById('statFramesCount');
const statLinesCount = document.getElementById('statLinesCount');
const statTranscriptStatus = document.getElementById('statTranscriptStatus');
const statDuration = document.getElementById('statDuration');
const audioMasterStatusBadge = document.getElementById('audioMasterStatusBadge');
const videoPreviewPlayer = document.getElementById('videoPreviewPlayer');
const btnDownload169 = document.getElementById('btnDownload169');
const btnDownload916 = document.getElementById('btnDownload916');

// Modals
const modalNewProject = document.getElementById('modalNewProject');
const modalExtract = document.getElementById('modalExtract');
const modalAiScript = document.getElementById('modalAiScript');
const modalTranscript = document.getElementById('modalTranscript');

// Tab Switching
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

    btn.classList.add('active');
    const targetTab = document.getElementById(btn.dataset.tab);
    if (targetTab) targetTab.classList.add('active');
  });
});

// Load Projects List
async function loadProjects() {
  try {
    const res = await fetch('/api/projects');
    const data = await res.json();
    if (data.success) {
      projectSelect.innerHTML = '';
      data.projects.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = `${p.title} (${p.id})`;
        if (p.id === currentProjectId) opt.selected = true;
        projectSelect.appendChild(opt);
      });

      if (!data.projects.find(p => p.id === currentProjectId) && data.projects.length > 0) {
        currentProjectId = data.projects[0].id;
      }
      if (data.projects.length > 0) {
        await loadProjectDetails(currentProjectId);
      } else {
        currentProjectData = null;
        renderEmptyState();
      }
    }
  } catch (err) {
    console.error('Failed to load projects:', err);
  }
}

function renderEmptyState() {
  framesGrid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 3rem; color: #8b9bb4;"><p>ไม่มีโปรเจกต์ในระบบ กรุณากดสร้างโปรเจกต์ใหม่</p></div>';
  scriptEditorContainer.innerHTML = '<div style="text-align: center; padding: 2rem; color: #8b9bb4;"><p>ไม่มีข้อมูลบทพูด</p></div>';
  statFramesCount.textContent = '0 ภาพ';
  statLinesCount.textContent = '0 ประโยค';
  statTranscriptStatus.textContent = 'ไม่มีข้อมูล';
  statDuration.textContent = '0.0 วินาที';
}

// Storyboard State
let currentStoryboard = [];

function updateStoryboardBadges() {
  const lblTotal = document.getElementById('lblTotalFrames');
  const lblSelected = document.getElementById('lblSelectedStoryboard');
  const countInput = document.getElementById('inputAiCount');
  
  const total = (currentProjectData && currentProjectData.frames) ? currentProjectData.frames.length : 0;
  if (lblTotal) lblTotal.textContent = total;
  if (lblSelected) lblSelected.textContent = currentStoryboard.length;
  if (countInput && currentStoryboard.length > 0) {
    countInput.value = currentStoryboard.length;
  }
}

async function saveStoryboard(scenes) {
  try {
    const res = await fetch(`/api/projects/${currentProjectId}/storyboard`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenes })
    });
    const data = await res.json();
    if (data.success) {
      currentStoryboard = data.storyboard;
      if (currentProjectData) currentProjectData.storyboard = currentStoryboard;
      renderFrames(currentProjectData.frames);
      updateStoryboardBadges();
    }
  } catch (err) {
    alert('เกิดข้อผิดพลาดในการบันทึก Storyboard: ' + err.message);
  }
}

async function autoPickStoryboard(count) {
  if (!currentProjectId) return;
  try {
    const res = await fetch(`/api/projects/${currentProjectId}/storyboard`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ autoSelectCount: count })
    });
    const data = await res.json();
    if (data.success) {
      currentStoryboard = data.storyboard;
      if (currentProjectData) currentProjectData.storyboard = currentStoryboard;
      renderFrames(currentProjectData.frames);
      updateStoryboardBadges();
    }
  } catch (err) {
    alert('เกิดข้อผิดพลาดในการเลือก Storyboard อัตโนมัติ: ' + err.message);
  }
}

async function toggleFrameStoryboard(frame) {
  const existingIdx = currentStoryboard.findIndex(f => f.id === frame.id);
  if (existingIdx >= 0) {
    currentStoryboard.splice(existingIdx, 1);
  } else {
    currentStoryboard.push(frame);
  }
  currentStoryboard.sort((a, b) => (a.index || 0) - (b.index || 0));
  await saveStoryboard(currentStoryboard);
}

// Load Details of Selected Project
async function loadProjectDetails(pId) {
  currentProjectId = pId;
  try {
    const res = await fetch(`/api/projects/${pId}`);
    const data = await res.json();
    if (!data.success) return;

    currentProjectData = data.project;
    currentStoryboard = currentProjectData.storyboard || [];
    
    renderMetrics(currentProjectData);
    renderFrames(currentProjectData.frames);
    renderScript(currentProjectData.script, currentProjectData.frames);
    renderVideoPlayer(currentProjectData);
    updateStoryboardBadges();
    updateProjectReadinessBadge(pId);
  } catch (err) {
    console.error('Error fetching project:', err);
  }
}

function renderMetrics(p) {
  const framesCount = p.frames ? p.frames.length : 0;
  const scriptItems = p.script && p.script.items ? p.script.items : [];
  const linesCount = scriptItems.length;
  const totalDur = p.timeline ? p.timeline.total_duration : (linesCount * 3.5);

  statFramesCount.textContent = `${framesCount} ภาพ`;
  statLinesCount.textContent = `${linesCount} ประโยค`;
  statDuration.textContent = `${totalDur.toFixed(1)} วินาที`;

  const isErr = !!p.transcriptError || (p.transcript && (p.transcript.startsWith('เกิดข้อผิดพลาด') || p.transcript.includes('503 Service Unavailable')));
  if (p.transcript && p.transcript.length > 10 && !isErr) {
    statTranscriptStatus.textContent = 'ถอดเสียงแล้ว ✅';
    statTranscriptStatus.style.color = '#00ff88';
  } else if (isErr) {
    statTranscriptStatus.textContent = 'ถอดเสียงไม่สำเร็จ ⚠️';
    statTranscriptStatus.style.color = '#ff9800';
  } else {
    statTranscriptStatus.textContent = 'ยังไม่มีข้อมูล';
    statTranscriptStatus.style.color = '#8b9bb4';
  }

  if (p.audioMaster) {
    audioMasterStatusBadge.textContent = 'พร้อมใช้งาน (Master Ready)';
    audioMasterStatusBadge.className = 'badge badge-success';
  } else {
    audioMasterStatusBadge.textContent = 'ยังไม่ได้สร้าง';
    audioMasterStatusBadge.className = 'badge badge-warning';
  }

  // OP/ED Shield metric
  const statOpEdStatus = document.getElementById('statOpEdStatus');
  if (statOpEdStatus) {
    if (p.opEdDetection && p.opEdDetection.detected) {
      const opStr = p.opEdDetection.op ? `${p.opEdDetection.op.start_str}-${p.opEdDetection.op.end_str}` : 'ไม่มี';
      const edStr = p.opEdDetection.ed ? `${p.opEdDetection.ed.start_str}-${p.opEdDetection.ed.end_str}` : 'ไม่มี';
      statOpEdStatus.innerHTML = `<span style="color:#00ff88; font-size: 0.82rem;" title="วิธี: ${p.opEdDetection.method} (${p.opEdDetection.confidence})">ตัดแล้ว: OP ${opStr} | ED ${edStr}</span>`;
    } else {
      statOpEdStatus.textContent = 'ตรวจจับอัตโนมัติ ✅';
      statOpEdStatus.style.color = '#00ff88';
    }
  }
}

function renderFrames(frames) {
  framesGrid.innerHTML = '';
  if (!frames || frames.length === 0) {
    framesGrid.innerHTML = `
      <div style="grid-column: 1/-1; text-align: center; padding: 3rem; color: #8b9bb4;">
        <p style="font-size: 1.1rem; margin-bottom: 1rem;">ยังไม่มีภาพนิ่งในโปรเจกต์นี้</p>
        <button class="btn btn-secondary" onclick="document.getElementById('btnExtractModal').click()">⚡ สกัดภาพจากวิดีโออนิเมะ</button>
      </div>`;
    return;
  }

  frames.forEach((f, idx) => {
    const card = document.createElement('div');
    const sbIndex = currentStoryboard.findIndex(sb => sb.id === f.id);
    const isSelected = sbIndex >= 0;
    
    card.className = `frame-card ${isSelected ? 'is-storyboard' : ''}`;
    const imgSrc = `/${f.path}?t=${Date.now()}`;
    const timeStr = f.time_str || '00:00';
    
    card.innerHTML = `
      <div class="frame-thumb-wrapper">
        <img class="frame-thumb" src="${imgSrc}" alt="${f.id}" loading="lazy">
        <span class="frame-badge">#${idx + 1} (${f.id})</span>
        <span class="frame-time-badge">⏱️ ${timeStr}</span>
        ${isSelected ? `<span class="frame-storyboard-badge">🎬 ฉากที่ #${sbIndex + 1}</span>` : ''}
      </div>
      <div class="frame-card-footer" style="display: flex; flex-direction: column; gap: 6px;">
        <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
          <span style="font-size: 0.75rem; color: var(--text-dim);">${f.filename}</span>
          <button class="btn btn-outline" style="padding: 2px 6px; font-size: 0.75rem;" onclick="previewImage('${imgSrc}')">🔍 ดูภาพ</button>
        </div>
        <button class="btn btn-sm ${isSelected ? 'btn-danger' : 'btn-outline'} btn-toggle-sb" style="width: 100%;">
          ${isSelected ? '✕ นำออกจาก Storyboard' : '+ เพิ่มเข้า Storyboard'}
        </button>
      </div>
    `;

    const btnToggle = card.querySelector('.btn-toggle-sb');
    btnToggle.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleFrameStoryboard(f);
    });

    framesGrid.appendChild(card);
  });
}

// Visual Frame Picker State
let activePickerCard = null;

function openFramePicker(lineIndex, currentFrameId, card) {
  activePickerCard = card;
  const pickerGrid = document.getElementById('pickerFramesGrid');
  const modal = document.getElementById('modalFramePicker');
  if (!pickerGrid || !modal) return;
  
  pickerGrid.innerHTML = '';
  const frames = (currentProjectData && currentProjectData.frames) ? currentProjectData.frames : [];
  
  if (frames.length === 0) {
    pickerGrid.innerHTML = '<p style="color: #8b9bb4; grid-column: 1/-1;">ไม่มีภาพในโปรเจกต์นี้</p>';
  } else {
    frames.forEach(f => {
      const item = document.createElement('div');
      item.className = `picker-frame-item ${f.id === currentFrameId ? 'selected' : ''}`;
      item.innerHTML = `
        <img src="/${f.path}" alt="${f.id}" loading="lazy">
        <span class="picker-name">#${f.index} (${f.id})</span>
        <span class="picker-time">⏱️ ${f.time_str || '00:00'}</span>
      `;
      item.addEventListener('click', () => {
        const select = activePickerCard.querySelector('.select-frame-id');
        if (select) {
          select.value = f.id;
          select.dispatchEvent(new Event('change'));
        }
        modal.style.display = 'none';
      });
      pickerGrid.appendChild(item);
    });
  }
  
  modal.style.display = 'flex';
}

// --- Timing-Aware Speech & Word Heuristics ---
function countThaiWords(text) {
  if (!text) return 0;
  const clean = text.trim();
  const tokens = clean.split(/[\s,\.\!\?\"\:\;\(\)\[\]\-\—\…\.\.\.]+/).filter(Boolean);
  let total = 0;
  for (const tok of tokens) {
    const consonants = (tok.match(/[\u0E01-\u0E2E]/g) || []).length;
    if (consonants > 0) {
      total += Math.max(1, Math.round(consonants / 3.2));
    } else {
      total += 1;
    }
  }
  return Math.max(1, total);
}

function estimateThaiSpeechDuration(text, rate = 5.0) {
  if (!text || !text.trim()) return 0;
  const clean = text.trim();
  const charDur = clean.length / 13.0;
  const wordDur = countThaiWords(clean) / 2.6;
  const raw = (charDur * 0.6) + (wordDur * 0.4);
  const pause = ((clean.match(/[,،]/g) || []).length * 0.15) +
                ((clean.match(/(\.\.\.|…)/g) || []).length * 0.35) +
                ((clean.match(/[!\?]/g) || []).length * 0.20);
  const total = (raw + pause) / (1.0 + (rate / 100.0));
  return Math.max(0.8, Math.round(total * 10) / 10);
}

function renderScript(script, frames) {
  scriptEditorContainer.innerHTML = '';
  const items = (script && script.items) ? script.items : [];
  const timelineScenes = (currentProjectData && currentProjectData.timeline && currentProjectData.timeline.timeline) ? currentProjectData.timeline.timeline : [];

  if (items.length === 0) {
    scriptEditorContainer.innerHTML = `
      <div style="text-align: center; padding: 2rem; color: #8b9bb4; border: 1px dashed var(--border-color); border-radius: 12px;">
        <p>ยังไม่มีบทบรรยายในโปรเจกต์นี้</p>
        <button class="btn btn-secondary" style="margin-top: 0.5rem;" onclick="document.getElementById('btnAiScriptModal').click()">✨ ให้ AI ช่วยเขียนบท</button>
      </div>`;
    return;
  }

  items.forEach((item, idx) => {
    const card = document.createElement('div');
    card.className = 'script-card';
    card.dataset.index = idx;

    // Matching image thumbnail
    let currentImageId = item.image_id;
    let thumbUrl = '/assets/placeholder.jpg';
    let frameTime = item.timestamp || '';
    
    if (frames && frames.length > 0) {
      let match = frames.find(f => f.id === currentImageId);
      if (!match) {
        match = frames[idx % frames.length];
        currentImageId = match.id;
      }
      thumbUrl = `/${match.path}`;
      if (!frameTime) frameTime = match.time_str || '00:00';
    }

    // Build frame select options
    let frameOptionsHtml = '';
    if (frames && frames.length > 0) {
      frames.forEach(f => {
        const isSel = f.id === currentImageId ? 'selected' : '';
        const t = f.time_str ? ` [${f.time_str}]` : '';
        frameOptionsHtml += `<option value="${f.id}" ${isSel}>#${f.index} (${f.id})${t}</option>`;
      });
    } else {
      frameOptionsHtml = `<option value="${currentImageId}">${currentImageId}</option>`;
    }

    const contextHtml = item.scene_context ? `
      <div style="font-size: 0.75rem; color: #a0aec0; margin-bottom: 6px; background: rgba(255,255,255,0.04); padding: 4px 8px; border-radius: 6px; border-left: 3px solid var(--primary);">
        💡 <b>บริบทฉาก:</b> ${item.scene_context}
      </div>
    ` : '';

    // Timing metrics
    const textVal = item.text || '';
    const wordCount = countThaiWords(textVal);
    const tlScene = timelineScenes.find(t => t.id == item.id || t.order == (idx + 1) || t.index == (idx + 1));
    const storyBeat = (tlScene && tlScene.storyBeat) || item.storyBeat || (idx === 0 ? 'Hook' : idx === items.length - 1 ? 'Conclusion' : `Beat ${idx + 1}`);
    const actualDur = tlScene && tlScene.actualAudioDuration;
    const estDur = estimateThaiSpeechDuration(textVal);
    const sceneDur = tlScene ? tlScene.duration : Math.round((estDur + 0.45) * 10) / 10;
    const isWordy = wordCount > 18;

    card.innerHTML = `
      <div class="script-thumb-box" style="position: relative;">
        <div style="position: relative; border-radius: 8px; overflow: hidden;">
          <img class="card-thumb-img" src="${thumbUrl}" alt="Frame">
          <span class="frame-time-badge">⏱️ ${frameTime || '00:00'}</span>
        </div>
        <div style="margin-top: 6px;">
          <select class="styled-select select-frame-id" style="width: 100%; padding: 2px 4px; font-size: 0.75rem;">
            ${frameOptionsHtml}
          </select>
        </div>
        <button class="btn btn-outline btn-open-picker" style="width: 100%; margin-top: 4px; padding: 2px 4px; font-size: 0.72rem;">🖼️ สลับรูปภาพ</button>
      </div>
      <div>
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
          <div style="display: flex; gap: 6px; align-items: center;">
            <span style="font-size: 0.8rem; font-weight: 600; color: var(--primary);">ท่อนที่ #${idx + 1}</span>
            <span class="badge" style="background: rgba(0, 240, 255, 0.12); color: #00f0ff; border: 1px solid rgba(0,240,255,0.3); font-size: 0.72rem;">🎬 ${storyBeat}</span>
            <span class="badge badge-words ${isWordy ? 'badge-danger' : 'badge-normal'}" style="font-size: 0.72rem;">📝 ${wordCount} คำ ${isWordy ? '(ยาวเกิน! ควร 8-16 คำ)' : ''}</span>
            <span class="badge badge-duration" style="background: rgba(255,255,255,0.06); font-size: 0.72rem; color: #a0aec0;">⏱️ ${actualDur ? `เสียง ${actualDur.toFixed(2)}s (ฉาก ${sceneDur.toFixed(2)}s)` : `ประมาณ ${estDur}s (ฉาก ${sceneDur}s)`}</span>
          </div>
          <div style="display: flex; gap: 4px;">
            <button class="btn-icon-only btn-insert-line" title="แทรกท่อนใหม่ตรงนี้">➕ แทรก</button>
            <button class="btn-icon-only danger btn-delete-line" title="ลบท่อนนี้ทิ้ง">🗑️ ลบ</button>
          </div>
        </div>
        ${contextHtml}
        <textarea class="script-text-input" placeholder="พิมพ์ข้อความบรรยายภาษาไทย (เป้าหมาย 8-16 คำต่อฉาก)...">${textVal}</textarea>
        ${isWordy ? `
          <div class="timing-warning-box" style="display: flex; gap: 8px; align-items: center; margin-top: 4px; background: rgba(255,85,85,0.08); border: 1px solid rgba(255,85,85,0.25); border-radius: 6px; padding: 4px 8px;">
            <span style="font-size: 0.75rem; color: #ff5555; flex: 1;">⚠️ ข้อความนี้ยาวเกินความเหมาะสมของ 1 ฉาก:</span>
            <button class="btn btn-sm btn-outline danger btn-quick-split" style="padding: 2px 8px; font-size: 0.72rem;">✂️ แยกเป็น 2 ฉาก</button>
            <button class="btn btn-sm btn-outline btn-quick-compress" style="padding: 2px 8px; font-size: 0.72rem;">⚡ ย่อให้กระชับ</button>
          </div>
        ` : ''}
      </div>
      <div class="script-controls-col">
        <label style="font-size: 0.75rem; color: #8b9bb4;">อารมณ์ฉาก:</label>
        <select class="styled-select script-action-select" style="padding: 4px 8px; font-size: 0.8rem;">
          <option value="normal" ${item.action_type === 'normal' ? 'selected' : ''}>ปกติ (Normal)</option>
          <option value="battle" ${item.action_type === 'battle' ? 'selected' : ''}>ต่อสู้ (Battle)</option>
          <option value="shock" ${item.action_type === 'shock' ? 'selected' : ''}>ตกใจ/ตื่นเต้น (Shock)</option>
          <option value="drama" ${item.action_type === 'drama' ? 'selected' : ''}>ซึ้ง/ดราม่า (Drama)</option>
        </select>
      </div>
      <div>
        <button class="btn btn-outline btn-audition" style="padding: 6px 10px; font-size: 0.8rem;" title="กดฟังเสียงประโยคนี้">🔊 ฟังเสียง</button>
      </div>
    `;

    // Live typing event
    const textarea = card.querySelector('.script-text-input');
    textarea.addEventListener('input', (e) => {
      const val = e.target.value;
      const w = countThaiWords(val);
      const est = estimateThaiSpeechDuration(val);
      const wBadge = card.querySelector('.badge-words');
      const dBadge = card.querySelector('.badge-duration');
      if (wBadge) {
        wBadge.textContent = `📝 ${w} คำ ${w > 18 ? '(ยาวเกิน! ควร 8-16 คำ)' : ''}`;
        wBadge.className = `badge badge-words ${w > 18 ? 'badge-danger' : 'badge-normal'}`;
      }
      if (dBadge) {
        dBadge.textContent = `⏱️ ประมาณ ${est}s (ฉาก ${(est + 0.45).toFixed(2)}s)`;
      }
    });

    // Quick Split Event
    const btnSplit = card.querySelector('.btn-quick-split');
    btnSplit?.addEventListener('click', async () => {
      try {
        const resp = await fetch(`/api/projects/${currentProjectId}/split-scene`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ index: idx })
        });
        const d = await resp.json();
        if (d.success) {
          await loadProjectDetails(currentProjectId);
        } else {
          alert(d.error || 'ไม่สามารถแยกฉากได้');
        }
      } catch (err) {
        alert(err.message);
      }
    });

    // Quick Compress Event
    const btnCompress = card.querySelector('.btn-quick-compress');
    btnCompress?.addEventListener('click', async () => {
      try {
        const resp = await fetch(`/api/projects/${currentProjectId}/compress-scene`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ index: idx })
        });
        const d = await resp.json();
        if (d.success) {
          await loadProjectDetails(currentProjectId);
        } else {
          alert(d.error || 'ไม่สามารถย่อบทได้');
        }
      } catch (err) {
        alert(err.message);
      }
    });

    // Change mapped image event
    const selectFrame = card.querySelector('.select-frame-id');
    selectFrame.addEventListener('change', (e) => {
      const newFrameId = e.target.value;
      const match = frames.find(f => f.id === newFrameId);
      if (match) {
        card.querySelector('.card-thumb-img').src = `/${match.path}`;
        const timeBadge = card.querySelector('.frame-time-badge');
        if (timeBadge) timeBadge.textContent = `⏱️ ${match.time_str || '00:00'}`;
      }
    });

    // Open Visual Picker
    const btnPicker = card.querySelector('.btn-open-picker');
    btnPicker.addEventListener('click', () => {
      openFramePicker(idx, selectFrame.value, card);
    });

    // Delete Line event
    const btnDelete = card.querySelector('.btn-delete-line');
    btnDelete.addEventListener('click', async () => {
      if (!confirm(`คุณต้องการลบท่อนที่ #${idx + 1} หรือไม่?`)) return;
      try {
        const resp = await fetch(`/api/projects/${currentProjectId}/script/delete-line`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lineIndex: idx })
        });
        if (resp.ok) {
          await loadProjectDetails(currentProjectId);
        }
      } catch (e) {
        alert(e.message);
      }
    });

    // Insert Line event
    const btnInsert = card.querySelector('.btn-insert-line');
    btnInsert.addEventListener('click', async () => {
      const nextFrame = frames && frames.length > (idx + 1) ? frames[idx + 1].id : currentImageId;
      try {
        const resp = await fetch(`/api/projects/${currentProjectId}/script/insert-line`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            afterIndex: idx,
            text: 'พิมพ์ข้อความบรรยายฉากนี้...',
            action_type: 'normal',
            image_id: nextFrame
          })
        });
        if (resp.ok) {
          await loadProjectDetails(currentProjectId);
        }
      } catch (e) {
        alert(e.message);
      }
    });

    // Audition button event
    const btnAudition = card.querySelector('.btn-audition');
    btnAudition.addEventListener('click', async () => {
      const txt = card.querySelector('.script-text-input').value.trim();
      const voice = document.getElementById('voiceSelect').value;
      const rate = document.getElementById('rateSelect').value;
      const action_type = card.querySelector('.script-action-select')?.value || 'normal';
      const voice_direction = item.voice_direction;
      if (!txt) return;

      btnAudition.textContent = '⏳...';
      try {
        const resp = await fetch('/api/tts-preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: txt,
            voice,
            rate,
            action_type,
            voice_direction
          })
        });
        if (resp.ok) {
          const blob = await resp.blob();
          const audioUrl = URL.createObjectURL(blob);
          const audio = new Audio(audioUrl);
          audio.play();
        }
      } catch (e) {
        console.error('Audition error', e);
      } finally {
        btnAudition.textContent = '🔊 ฟังเสียง';
      }
    });

    scriptEditorContainer.appendChild(card);
  });
}

function renderVideoPlayer(p) {
  const vSrc = p.video16_9 || p.video9_16;
  if (vSrc) {
    videoPreviewPlayer.src = `${vSrc}?t=${Date.now()}`;
    btnDownload169.href = p.video16_9 || '#';
    btnDownload916.href = p.video9_16 || '#';
    btnDownload169.style.display = p.video16_9 ? 'inline-flex' : 'none';
    btnDownload916.style.display = p.video9_16 ? 'inline-flex' : 'none';
  } else {
    videoPreviewPlayer.src = '';
    btnDownload169.style.display = 'none';
    btnDownload916.style.display = 'none';
  }
}

// Project Select change
projectSelect.addEventListener('change', (e) => {
  loadProjectDetails(e.target.value);
});

// Create Project
btnNewProject.addEventListener('click', () => modalNewProject.style.display = 'flex');
document.getElementById('btnCloseNewProjectModal').addEventListener('click', () => modalNewProject.style.display = 'none');
document.getElementById('btnCancelNewProject').addEventListener('click', () => modalNewProject.style.display = 'none');
document.getElementById('btnSubmitNewProject').addEventListener('click', async () => {
  const id = document.getElementById('inputProjectId').value.trim();
  const title = document.getElementById('inputProjectTitle').value.trim();
  if (!id) return alert('กรุณากรอกรหัสโปรเจกต์');

  const res = await fetch('/api/projects/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, title })
  });
  const data = await res.json();
  if (data.success) {
    modalNewProject.style.display = 'none';
    currentProjectId = data.id;
    await loadProjects();
  } else {
    alert(data.error);
  }
});

// Delete Project
btnDeleteProject.addEventListener('click', async () => {
  if (!currentProjectId) return;
  if (!confirm(`⚠️ คุณแน่ใจหรือไม่ว่าต้องการลบโปรเจกต์ "${currentProjectId}" และไฟล์ทั้งหมด?\nการกระทำนี้ไม่สามารถย้อนกลับได้`)) return;

  try {
    const res = await fetch(`/api/projects/${currentProjectId}`, {
      method: 'DELETE'
    });
    const data = await res.json();
    if (data.success) {
      alert(`ลบโปรเจกต์ "${currentProjectId}" เรียบร้อยแล้ว`);
      await loadProjects();
    } else {
      alert(data.error);
    }
  } catch (err) {
    alert(err.message);
  }
});

// Extract Modal
document.getElementById('btnExtractModal').addEventListener('click', () => modalExtract.style.display = 'flex');
document.getElementById('btnCloseExtractModal').addEventListener('click', () => modalExtract.style.display = 'none');
document.getElementById('btnCancelExtract').addEventListener('click', () => modalExtract.style.display = 'none');
document.getElementById('btnSubmitExtract').addEventListener('click', async () => {
  const fileInput = document.getElementById('inputVideoFile');
  let videoPath = document.getElementById('inputVideoPath').value.trim();
  const threshold = parseFloat(document.getElementById('inputThreshold').value);
  const maxFrames = parseInt(document.getElementById('inputMaxFrames').value);
  const doTranscribe = document.getElementById('checkDoTranscribe').checked;
  const apiKey = document.getElementById('inputExtractApiKey').value.trim();

  // OP/ED cut params from extract modal
  const autoDetectOpEd = document.getElementById('checkAutoDetectOpEd')?.checked ?? true;
  const skipIntro = document.getElementById('checkSkipIntro')?.checked || false;
  const skipOutro = document.getElementById('checkSkipOutro')?.checked || false;
  const introRange = document.getElementById('inputIntroRange')?.value.trim() || '';
  const outroRange = document.getElementById('inputOutroRange')?.value.trim() || '';
  const introSec   = document.getElementById('inputIntroSec')?.value.trim() || '';
  const outroSec   = document.getElementById('inputOutroSec')?.value.trim() || '';
  // Prefer explicit range if given, else use seconds
  const introParam  = introRange || introSec || null;
  const outroParam  = outroRange || outroSec || null;

  const btn = document.getElementById('btnSubmitExtract');
  let uploadId = null;

  // Handle uploaded file if present
  if (fileInput && fileInput.files && fileInput.files.length > 0) {
    btn.textContent = '⏳ กำลังอัปโหลดไฟล์วิดีโอเข้าสู่ระบบ...';
    btn.disabled = true;
    const formData = new FormData();
    formData.append('videoFile', fileInput.files[0]);

    try {
      const upRes = await fetch('/api/upload', {
        method: 'POST',
        body: formData
      });
      const upData = await upRes.json();
      if (!upData.success) throw new Error(upData.error || 'Upload failed');
      videoPath = upData.filePath;
      uploadId = upData.uploadId;
    } catch (e) {
      alert(`อัปโหลดไฟล์ไม่สำเร็จ: ${e.message}`);
      btn.textContent = 'เริ่มสกัดภาพ & ประมวลผล';
      btn.disabled = false;
      return;
    }
  }

  if (!videoPath && !uploadId) {
    return alert('กรุณาเลือกไฟล์วิดีโอ หรือระบุที่อยู่ไฟล์วิดีโอบนเครื่อง');
  }

  btn.textContent = autoDetectOpEd
    ? '⏳ กำลังตรวจจับ OP/ED, สกัดภาพ และถอดเสียง (ประมาณ 1-2 นาที)...'
    : '⏳ กำลังสกัดภาพและถอดเสียง (อาจใช้เวลา 1-3 นาที)...';
  btn.disabled = true;

  try {
    const res = await fetch(`/api/projects/${currentProjectId}/extract`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        uploadId: uploadId || undefined,
        videoPath, threshold, maxFrames, doTranscribe, apiKey,
        autoDetectOpEd,
        skipIntro: skipIntro ? (introParam || true) : false,
        skipOutro: skipOutro ? (outroParam || true) : false,
        introRange: autoDetectOpEd ? (introRange || '') : (introParam || ''),
        outroRange: autoDetectOpEd ? (outroRange || '') : (outroParam || '')
      })
    });
    const data = await res.json();
    if (data.success) {
      let msg = `สกัดภาพสำเร็จเรียบร้อยแล้ว ${data.count} ภาพ!`;
      if (data.detectedRanges && (data.detectedRanges.op || data.detectedRanges.ed)) {
        msg += `\n🛡️ ตรวจจับและตัด OP/ED สำเร็จ:`;
        if (data.detectedRanges.op) msg += `\n  - เพลงเปิด (OP): ${data.detectedRanges.op}`;
        if (data.detectedRanges.ed) msg += `\n  - เพลงปิด (ED): ${data.detectedRanges.ed}`;
      }
      if (data.transcript) {
        msg += `\n🎙️ ถอดเสียง/ซับไตเติลสำเร็จเรียบร้อยแล้ว`;
      }
      alert(msg);
      modalExtract.style.display = 'none';
      await loadProjectDetails(currentProjectId);
    } else {
      alert(`เกิดข้อผิดพลาด: ${data.error}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.textContent = 'เริ่มสกัดภาพ & ประมวลผล';
    btn.disabled = false;
  }
});

// Toggle Manual OP/ED settings in Extract Modal
document.getElementById('btnToggleManualOpEd')?.addEventListener('click', () => {
  const w = document.getElementById('manualOpEdWrapper');
  if (w) {
    w.style.display = (w.style.display === 'none' || w.style.display === '') ? 'block' : 'none';
  }
});

// AI Script Modal
function updateAiScriptSamplingInfo() {
  const countInput = document.getElementById('inputAiCount');
  const infoEl = document.getElementById('aiScriptSamplingInfo');
  if (!infoEl || !countInput) return;

  const count = parseInt(countInput.value) || 30;
  const frames = (currentProjectData && currentProjectData.frames) ? currentProjectData.frames : [];
  const total = frames.length;

  if (total === 0) {
    infoEl.innerHTML = '⚠️ ยังไม่มีภาพนิ่งในโปรเจกต์นี้ กรุณาสกัดภาพก่อน';
    infoEl.style.color = '#ff9800';
    return;
  }

  const effectiveCount = Math.min(count, total);
  const firstTime = frames[0]?.time_str || '00:00';
  const lastTime = frames[frames.length - 1]?.time_str || '00:00';

  infoEl.innerHTML = `🎯 <b>ระบบจะคัดเลือกภาพอย่างชาญฉลาด:</b> ได้รับ <b>${effectiveCount} ภาพ</b> จากทั้งหมด ${total} ภาพ กระจายสม่ำเสมอตั้งแต่ <b>${firstTime}</b> ถึง <b>${lastTime}</b> (ครอบคลุมต้นเรื่องจนถึงจบเรื่อง ไม่กระจุกตัว)`;
  infoEl.style.color = '#00ff88';
}

document.getElementById('inputAiCount')?.addEventListener('input', updateAiScriptSamplingInfo);

document.querySelectorAll('.btn-preset-count').forEach(btn => {
  btn.addEventListener('click', (e) => {
    e.preventDefault();
    const c = btn.dataset.count;
    const input = document.getElementById('inputAiCount');
    if (input) {
      input.value = c;
      updateAiScriptSamplingInfo();
    }
  });
});

// Transcript Modal Handlers
const textareaFullTranscript = document.getElementById('textareaFullTranscript');
const lblTranscriptCharCount = document.getElementById('lblTranscriptCharCount');

function updateTranscriptCharCount() {
  if (lblTranscriptCharCount && textareaFullTranscript) {
    const len = textareaFullTranscript.value.length;
    lblTranscriptCharCount.textContent = `${len.toLocaleString()} ตัวอักษร`;
  }
}

textareaFullTranscript?.addEventListener('input', updateTranscriptCharCount);

document.getElementById('btnOpenTranscriptModal')?.addEventListener('click', () => {
  if (currentProjectData && textareaFullTranscript) {
    textareaFullTranscript.value = currentProjectData.transcript || '';
    updateTranscriptCharCount();
  }
  if (modalTranscript) modalTranscript.style.display = 'flex';
});

document.getElementById('btnCloseTranscriptModal')?.addEventListener('click', () => {
  if (modalTranscript) modalTranscript.style.display = 'none';
});

document.getElementById('btnCancelTranscript')?.addEventListener('click', () => {
  if (modalTranscript) modalTranscript.style.display = 'none';
});

document.getElementById('btnSaveTranscript')?.addEventListener('click', async () => {
  if (!currentProjectId) return alert('กรุณาเลือกโปรเจกต์ก่อน');
  const text = textareaFullTranscript.value;
  const btn = document.getElementById('btnSaveTranscript');
  btn.textContent = '⏳ กำลังบันทึก...';
  btn.disabled = true;

  try {
    const res = await fetch(`/api/projects/${currentProjectId}/transcript`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transcript: text })
    });
    const data = await res.json();
    if (data.success) {
      if (currentProjectData) currentProjectData.transcript = text;
      renderMetrics(currentProjectData);
      alert('บันทึกสคริปต์ถอดเสียงเรียบร้อยแล้ว!');
    } else {
      alert(`บันทึกไม่สำเร็จ: ${data.error}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.textContent = '💾 บันทึกสคริปต์ถอดเสียง';
    btn.disabled = false;
  }
});

document.getElementById('btnProceedToAiScript')?.addEventListener('click', () => {
  if (modalTranscript) modalTranscript.style.display = 'none';
  document.getElementById('btnAiScriptModal')?.click();
});

document.getElementById('btnAiScriptModal').addEventListener('click', () => {
  // Pre-fill transcript if available and valid
  const synopsisEl = document.getElementById('inputSynopsis');
  const badgeEl = document.getElementById('badgeTranscriptFound');
  const retrySection = document.getElementById('retryTranscriptionSection');
  const retryErrorMsg = document.getElementById('retryErrorMessage');
  const retryApiKeyInput = document.getElementById('inputRetryApiKey');
  
  const rawTranscript = currentProjectData?.transcript || '';
  const transcriptError = currentProjectData?.transcriptError || '';
  
  // Check for error: either API returned error, or legacy error in transcript text
  const isErr = !!transcriptError || 
    rawTranscript.startsWith('เกิดข้อผิดพลาด') || 
    rawTranscript.includes('503 Service Unavailable');
  const hasRealTranscript = rawTranscript.trim() && !isErr;

  if (hasRealTranscript) {
    synopsisEl.value = rawTranscript;
    badgeEl.textContent = 'พบข้อความถอดเสียง';
    badgeEl.className = 'badge badge-success';
    badgeEl.style.display = 'inline-block';
    retrySection.style.display = 'none';
  } else if (isErr || transcriptError) {
    synopsisEl.value = '';
    synopsisEl.placeholder = '⚠️ การถอดเสียงไม่สำเร็จ\n👉 กดปุ่ม "🔄 ลองถอดเสียงใหม่" ด้านบน หรือวางเนื้อเรื่องย่อเอง';
    badgeEl.textContent = '⚠️ ถอดเสียงไม่สำเร็จ';
    badgeEl.className = 'badge badge-danger';
    badgeEl.style.display = 'inline-block';
    
    // Show retry section with error details
    retrySection.style.display = 'block';
    const errorDetail = transcriptError || 'เซิร์ฟเวอร์ Gemini โหลดสูงชั่วคราว (503 Overloaded)';
    retryErrorMsg.textContent = `ข้อผิดพลาด: ${errorDetail.substring(0, 150)}`;
    
    // Pre-fill retry API key from main key input if available
    const mainKeyEl = document.getElementById('inputGeminiKey');
    if (mainKeyEl && mainKeyEl.value && retryApiKeyInput) {
      retryApiKeyInput.value = mainKeyEl.value;
    }
  } else {
    synopsisEl.value = '';
    badgeEl.style.display = 'none';
    retrySection.style.display = 'none';
  }
  
  // Set default count to current storyboard count or 30
  const countInput = document.getElementById('inputAiCount');
  if (countInput) {
    if (currentStoryboard && currentStoryboard.length > 0) {
      countInput.value = currentStoryboard.length;
    } else {
      countInput.value = 30;
    }
  }
  
  updateAiScriptSamplingInfo();
  modalAiScript.style.display = 'flex';
});
document.getElementById('btnCloseAiScriptModal').addEventListener('click', () => modalAiScript.style.display = 'none');
document.getElementById('btnCancelAiScript').addEventListener('click', () => modalAiScript.style.display = 'none');

// Retry Transcribe button in AI Script Modal
document.getElementById('btnRetryTranscribe')?.addEventListener('click', async () => {
  const retryBtn = document.getElementById('btnRetryTranscribe');
  const apiKeyInput = document.getElementById('inputRetryApiKey');
  const mainKeyInput = document.getElementById('inputGeminiKey');
  const statusMsg = document.getElementById('retryStatusMessage');
  const synopsisEl = document.getElementById('inputSynopsis');
  const badgeEl = document.getElementById('badgeTranscriptFound');
  const retrySection = document.getElementById('retryTranscriptionSection');
  
  const apiKey = (apiKeyInput?.value || mainKeyInput?.value || '').trim();
  if (!apiKey) {
    alert('กรุณากรอก Gemini API Key เพื่อถอดเสียง');
    apiKeyInput?.focus();
    return;
  }
  
  // Also sync to main key input if empty
  if (mainKeyInput && !mainKeyInput.value) {
    mainKeyInput.value = apiKey;
  }

  retryBtn.disabled = true;
  retryBtn.textContent = '⏳ กำลังถอดเสียง... (อาจใช้เวลา 30-90 วินาที)';
  statusMsg.style.display = 'block';
  statusMsg.style.color = '#8b9bb4';
  statusMsg.textContent = 'กำลังเชื่อมต่อ Gemini Audio API และประมวลผลพร้อมระบบ Auto-Retry...';

  try {
    const res = await fetch(`/api/projects/${currentProjectId}/retry-transcribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ apiKey })
    });
    const data = await res.json();
    
    if (data.success && data.transcript) {
      if (currentProjectData) {
        currentProjectData.transcript = data.transcript;
        currentProjectData.transcriptError = '';
      }
      synopsisEl.value = data.transcript;
      badgeEl.textContent = 'พบข้อความถอดเสียง';
      badgeEl.className = 'badge badge-success';
      badgeEl.style.display = 'inline-block';
      retrySection.style.display = 'none';
      renderMetrics(currentProjectData);
      alert('ถอดเสียงสำเร็จเรียบร้อยแล้ว!');
    } else {
      const errMsg = data.transcriptError || data.error || 'การถอดเสียงล้มเหลว กรุณาลองใหม่อีกครั้ง';
      statusMsg.style.color = '#ff5555';
      statusMsg.textContent = `❌ ${errMsg}`;
      const retryErrorMsg = document.getElementById('retryErrorMessage');
      if (retryErrorMsg) retryErrorMsg.textContent = `ข้อผิดพลาด: ${errMsg.substring(0, 150)}`;
    }
  } catch (err) {
    statusMsg.style.color = '#ff5555';
    statusMsg.textContent = `❌ เกิดข้อผิดพลาดในการเชื่อมต่อ: ${err.message}`;
  } finally {
    retryBtn.disabled = false;
    retryBtn.textContent = '🔄 ลองถอดเสียงใหม่';
  }
});
document.getElementById('btnSubmitAiScript').addEventListener('click', async () => {
  const apiKey = document.getElementById('inputGeminiKey').value.trim();
  const synopsis = document.getElementById('inputSynopsis').value.trim();
  const count = parseInt(document.getElementById('inputAiCount').value) || 30;

  const btn = document.getElementById('btnSubmitAiScript');
  btn.textContent = '⏳ กำลังประมวลผลวิเคราะห์ภาพ & เขียนบท...';
  btn.disabled = true;

  try {
    const res = await fetch(`/api/projects/${currentProjectId}/ai-script`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        apiKey,
        synopsis,
        count,
        title: (currentProjectData && currentProjectData.script) ? currentProjectData.script.title : currentProjectId,
        resample: true
      })
    });
    const data = await res.json();
    if (data.success) {
      modalAiScript.style.display = 'none';
      await loadProjectDetails(currentProjectId);
      document.getElementById('tabBtnScript').click();
    } else {
      alert(`Error: ${data.error}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.textContent = '🚀 สร้างสคริปต์ & ซิงก์ภาพทันที';
    btn.disabled = false;
  }
});

// Recalculate Timeline button
document.getElementById('btnRecalculateTimeline')?.addEventListener('click', async () => {
  if (!currentProjectId) return;
  const btn = document.getElementById('btnRecalculateTimeline');
  btn.textContent = '⏳ กำลังซิงก์...';
  btn.disabled = true;
  try {
    const res = await fetch(`/api/projects/${currentProjectId}/recalculate-timeline`, {
      method: 'POST'
    });
    const d = await res.json();
    if (d.success) {
      await loadProjectDetails(currentProjectId);
      alert(`✅ ซิงก์ไทม์ไลน์สำเร็จ! ความยาวรวม ${d.timeline.total_duration}s (${d.timeline.scenes_count} ฉาก)`);
    }
  } catch (err) {
    alert(err.message);
  } finally {
    btn.textContent = '⏱️ ซิงก์ไทม์ไลน์ (Sync Timing)';
    btn.disabled = false;
  }
});

// Save Script
document.getElementById('btnSaveScript').addEventListener('click', async () => {
  const cards = document.querySelectorAll('.script-card');
  const items = [];

  cards.forEach((c, idx) => {
    const text = c.querySelector('.script-text-input').value.trim();
    const action_type = c.querySelector('.script-action-select').value;
    const selectedFrameId = c.querySelector('.select-frame-id')?.value;
    const numStr = String(idx + 1).padStart(4, '0');
    const img_id = selectedFrameId || currentProjectData.frames[idx % (currentProjectData.frames.length || 1)]?.id || `frame_${numStr}`;

    const matchFrame = currentProjectData?.frames?.find(f => f.id === img_id);
    const timeStr = matchFrame?.time_str || '';

    const origItem = currentProjectData?.script?.items?.[idx];
    const itemObj = {
      id: idx + 1,
      image_id: img_id,
      timestamp: timeStr,
      text,
      action_type
    };
    if (origItem && origItem.voice_direction) {
      itemObj.voice_direction = origItem.voice_direction;
    }
    items.push(itemObj);
  });

  const updatedScript = {
    ...currentProjectData.script,
    items
  };

  const res = await fetch(`/api/projects/${currentProjectId}/script`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ script: updatedScript })
  });
  if (res.ok) {
    alert('บันทึกบทบรรยายเรียบร้อยแล้ว!');
    await loadProjectDetails(currentProjectId);
  }
});

// Add Line at end
document.getElementById('btnAddScriptLine').addEventListener('click', () => {
  const currentItems = currentProjectData.script.items || [];
  const nextId = currentItems.length + 1;
  const nextNumStr = String(nextId).padStart(4, '0');
  const frameId = currentProjectData.frames[nextId - 1]?.id || `frame_${nextNumStr}`;
  currentItems.push({
    id: nextId,
    image_id: frameId,
    text: 'พิมพ์ข้อความบรรยายฉากนี้...',
    action_type: 'normal'
  });
  renderScript(currentProjectData.script, currentProjectData.frames);
});

// Generate TTS & Subtitles
document.getElementById('btnGenerateTTS').addEventListener('click', async () => {
  const voice = document.getElementById('voiceSelect').value;
  const rate = document.getElementById('rateSelect').value;

  const btn = document.getElementById('btnGenerateTTS');
  btn.textContent = '⏳ กำลังพากย์เสียง & สร้างซับ...';
  btn.disabled = true;

  try {
    const res = await fetch(`/api/projects/${currentProjectId}/tts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice, rate })
    });
    const data = await res.json();
    if (data.success) {
      alert(`สร้างเสียงพากย์เสร็จสิ้น! ความยาวรวม ${data.timeline.total_duration} วินาที (${data.timeline.lines_count} ท่อน)`);
      await loadProjectDetails(currentProjectId);
      document.getElementById('tabBtnRender').click();
    } else {
      alert(`Error: ${data.error}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.textContent = '🎙️ สร้างเสียงพากย์ & ซับไตเติล';
    btn.disabled = false;
  }
});

// Render Video
document.getElementById('btnStartRender').addEventListener('click', async () => {
  const aspect = document.querySelector('input[name="aspectRatio"]:checked').value;
  const bgm = document.getElementById('bgmSelect').value;
  const subtitles = document.getElementById('checkBurnSubtitles').checked;

  const btn = document.getElementById('btnStartRender');
  const progressBox = document.getElementById('renderProgressBox');
  btn.disabled = true;
  progressBox.style.display = 'flex';

  try {
    const res = await fetch(`/api/projects/${currentProjectId}/render`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ aspect, bgm, subtitles })
    });
    const data = await res.json();
    if (data.success) {
      alert(`เรนเดอร์วิดีโอ (${aspect}) สำเร็จเรียบร้อยแล้ว!`);
      await loadProjectDetails(currentProjectId);
    } else {
      alert(`Render Error: ${data.error}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.disabled = false;
    progressBox.style.display = 'none';
  }
});

// Storyboard Toolbar Listeners
document.getElementById('btnAutoPick15')?.addEventListener('click', () => autoPickStoryboard(15));
document.getElementById('btnAutoPick30')?.addEventListener('click', () => autoPickStoryboard(30));
document.getElementById('btnAutoPick45')?.addEventListener('click', () => autoPickStoryboard(45));
document.getElementById('btnSelectAllFrames')?.addEventListener('click', () => {
  if (currentProjectData && currentProjectData.frames) {
    saveStoryboard([...currentProjectData.frames]);
  }
});
document.getElementById('btnClearStoryboard')?.addEventListener('click', () => {
  if (confirm('คุณต้องการล้าง Storyboard ที่เลือกไว้ทั้งหมดหรือไม่?')) {
    saveStoryboard([]);
  }
});

document.getElementById('btnGoToScriptWithStoryboard')?.addEventListener('click', () => {
  document.getElementById('tabBtnScript').click();
  const items = currentProjectData?.script?.items || [];
  if (items.length === 0) {
    document.getElementById('btnAiScriptModal').click();
  }
});

// Visual Frame Picker Close Buttons
document.getElementById('btnCloseFramePickerModal')?.addEventListener('click', () => {
  document.getElementById('modalFramePicker').style.display = 'none';
});
document.getElementById('btnCancelFramePicker')?.addEventListener('click', () => {
  document.getElementById('modalFramePicker').style.display = 'none';
});

// BGM Slider label
document.getElementById('bgmVolume')?.addEventListener('input', (e) => {
  const val = Math.round(e.target.value * 100);
  document.getElementById('bgmVolumeLabel').textContent = `${val}% (หรี่ลงเมื่อมีเสียงพากย์)`;
});

function previewImage(url) {
  window.open(url, '_blank');
}

// Initial Boot
window.addEventListener('DOMContentLoaded', loadProjects);

// ================================================
// Trim OP/ED Modal Logic
// ================================================

/** Parse time string like "01:30" or "90" into seconds (number) */
function parseTimeToSeconds(str) {
  str = str.trim();
  if (!str) return null;
  if (str.includes(':')) {
    const parts = str.split(':').map(Number);
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  }
  const n = parseFloat(str);
  return isNaN(n) ? null : n;
}

/** Parse range string like "01:00-02:30" or "60-150" into [startSec, endSec] */
function parseRangeStr(str) {
  if (!str || !str.includes('-')) return null;
  // Handle negative numbers / trailing dash guard
  const dashIdx = str.lastIndexOf('-');
  if (dashIdx === 0) return null;
  const left  = str.substring(0, dashIdx);
  const right = str.substring(dashIdx + 1);
  const s = parseTimeToSeconds(left);
  const e = parseTimeToSeconds(right);
  if (s === null || e === null) return null;
  return [Math.min(s, e), Math.max(s, e)];
}

// Open modal
document.getElementById('btnTrimOpEd')?.addEventListener('click', () => {
  if (!currentProjectData || !currentProjectData.frames || currentProjectData.frames.length === 0) {
    return alert('ไม่มีภาพนิ่งในโปรเจกต์นี้ กรุณาสกัดภาพก่อน');
  }
  document.getElementById('modalTrimOpEd').style.display = 'flex';
  updateTrimPreview();
});

// Close modal buttons
document.getElementById('btnCloseTrimOpEd')?.addEventListener('click', () => {
  document.getElementById('modalTrimOpEd').style.display = 'none';
});
document.getElementById('btnCancelTrimOpEd')?.addEventListener('click', () => {
  document.getElementById('modalTrimOpEd').style.display = 'none';
});

// Toggle intro section enabled/disabled
document.getElementById('trimCheckIntro')?.addEventListener('change', (e) => {
  document.getElementById('trimIntroBody').classList.toggle('disabled', !e.target.checked);
  updateTrimPreview();
});
document.getElementById('trimCheckOutro')?.addEventListener('change', (e) => {
  document.getElementById('trimOutroBody').classList.toggle('disabled', !e.target.checked);
  updateTrimPreview();
});

// Radio mode switching — Intro
document.querySelectorAll('input[name="trimIntroMode"]').forEach(radio => {
  radio.addEventListener('change', () => {
    const isRange = document.getElementById('trimIntroModeRange').checked;
    document.getElementById('trimIntroSecondsInput').style.display = isRange ? 'none' : 'block';
    document.getElementById('trimIntroRangeInput').style.display  = isRange ? 'block' : 'none';
    updateTrimPreview();
  });
});

// Radio mode switching — Outro
document.querySelectorAll('input[name="trimOutroMode"]').forEach(radio => {
  radio.addEventListener('change', () => {
    const isRange = document.getElementById('trimOutroModeRange').checked;
    document.getElementById('trimOutroSecondsInput').style.display = isRange ? 'none' : 'block';
    document.getElementById('trimOutroRangeInput').style.display  = isRange ? 'block' : 'none';
    updateTrimPreview();
  });
});

// Live preview of how many frames will be cut
document.getElementById('trimIntroSeconds')?.addEventListener('input', updateTrimPreview);
document.getElementById('trimIntroRange')?.addEventListener('input', updateTrimPreview);
document.getElementById('trimOutroSeconds')?.addEventListener('input', updateTrimPreview);
document.getElementById('trimOutroRange')?.addEventListener('input', updateTrimPreview);

function getTrimRanges() {
  const frames = currentProjectData?.frames || [];
  const ranges = [];

  // --- Intro ---
  if (document.getElementById('trimCheckIntro')?.checked) {
    const isRange = document.getElementById('trimIntroModeRange')?.checked;
    if (isRange) {
      const r = parseRangeStr(document.getElementById('trimIntroRange')?.value);
      if (r) ranges.push(r);
    } else {
      const sec = parseTimeToSeconds(document.getElementById('trimIntroSeconds')?.value);
      if (sec !== null && sec > 0) ranges.push([0, sec]);
    }
  }

  // --- Outro ---
  if (document.getElementById('trimCheckOutro')?.checked) {
    const isRange = document.getElementById('trimOutroModeRange')?.checked;
    if (isRange) {
      const r = parseRangeStr(document.getElementById('trimOutroRange')?.value);
      if (r) ranges.push(r);
    } else {
      const sec = parseTimeToSeconds(document.getElementById('trimOutroSeconds')?.value);
      if (sec !== null && sec > 0) {
        // Find video duration from last frame timestamp
        const lastFrame = frames[frames.length - 1];
        const lastSec = parseTimeToSeconds(lastFrame?.time_str) || 0;
        const endSec = lastSec + 30; // buffer
        const startSec = Math.max(0, endSec - sec);
        ranges.push([startSec, endSec]);
      }
    }
  }

  return ranges;
}

function isFrameInRanges(frame, ranges) {
  const frameSec = parseTimeToSeconds(frame.time_str);
  if (frameSec === null) return false;
  return ranges.some(([s, e]) => frameSec >= s && frameSec <= e);
}

function updateTrimPreview() {
  const frames = currentProjectData?.frames || [];
  if (frames.length === 0) return;

  const ranges = getTrimRanges();
  const previewEl = document.getElementById('trimPreviewInfo');
  const previewText = document.getElementById('trimPreviewText');

  if (ranges.length === 0) {
    previewEl.style.display = 'none';
    return;
  }

  const cutFrames = frames.filter(f => isFrameInRanges(f, ranges));
  const remaining = frames.length - cutFrames.length;
  const cutSb = currentStoryboard.filter(f => isFrameInRanges(f, ranges));

  const rangesText = ranges.map(([s,e]) => {
    const fmt = sec => `${Math.floor(sec/60).toString().padStart(2,'0')}:${Math.floor(sec%60).toString().padStart(2,'0')}`;
    return `${fmt(s)}–${fmt(e)}`;
  }).join(', ');

  previewEl.style.display = 'block';
  previewText.innerHTML =
    `🎯 ช่วงที่จะตัด: <b>${rangesText}</b><br>` +
    `✂️ ภาพที่จะถูกลบออก: <b>${cutFrames.length} ภาพ</b> (เหลือ ${remaining} ภาพ)<br>` +
    (cutSb.length > 0 ? `⚠️ รวม <b>${cutSb.length} ภาพ</b> ใน Storyboard ปัจจุบันที่จะถูกลบด้วย` : '✅ ไม่มีภาพใน Storyboard ที่ได้รับผลกระทบ');
}

// Confirm trim
document.getElementById('btnConfirmTrimOpEd')?.addEventListener('click', async () => {
  const frames = currentProjectData?.frames || [];
  if (frames.length === 0) return;

  const ranges = getTrimRanges();
  if (ranges.length === 0) {
    return alert('กรุณาระบุช่วงเวลาที่ต้องการตัดอย่างน้อย 1 ช่วง');
  }

  const cutFrames = frames.filter(f => isFrameInRanges(f, ranges));
  if (cutFrames.length === 0) {
    return alert('ไม่พบภาพในช่วงเวลาที่ระบุ ตรวจสอบว่าช่วงเวลาถูกต้องหรือไม่');
  }

  const remaining = frames.length - cutFrames.length;
  if (!confirm(`⚠️ จะลบภาพนิ่ง ${cutFrames.length} ภาพออกจากคลัง (เหลือ ${remaining} ภาพ)\nการกระทำนี้ไม่สามารถยกเลิกได้ ต้องการดำเนินการต่อหรือไม่?`)) return;

  const btn = document.getElementById('btnConfirmTrimOpEd');
  btn.textContent = '⏳ กำลังตัดภาพ...';
  btn.disabled = true;

  // Build list of frame IDs to remove
  const frameIdsToRemove = cutFrames.map(f => f.id);

  try {
    const res = await fetch(`/api/projects/${currentProjectId}/trim-frames`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frameIds: frameIdsToRemove })
    });
    const data = await res.json();

    if (data.success) {
      // Also remove from local storyboard
      const newSb = currentStoryboard.filter(f => !frameIdsToRemove.includes(f.id));
      await saveStoryboard(newSb);

      document.getElementById('modalTrimOpEd').style.display = 'none';
      alert(`✅ ตัดภาพ OP/ED สำเร็จ! ลบ ${data.removed} ภาพออกจากคลัง`);
      await loadProjectDetails(currentProjectId);
    } else {
      alert(`เกิดข้อผิดพลาด: ${data.error}`);
    }
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.textContent = '✂️ ตัดภาพ OP/ED ออกทันที';
    btn.disabled = false;
  }
});

// Auto-Scan Video OP/ED button in Trim Modal
document.getElementById('btnAutoScanVideoOpEd')?.addEventListener('click', async () => {
  const btn = document.getElementById('btnAutoScanVideoOpEd');
  const statusLbl = document.getElementById('trimDetectStatusLbl');
  const dashboard = document.getElementById('trimDetectionDashboard');
  btn.textContent = '⏳ กำลังสแกนคลิป...';
  btn.disabled = true;

  // Hide dashboard while scanning
  if (dashboard) dashboard.style.display = 'none';

  try {
    const res = await fetch(`/api/projects/${currentProjectId}/detect-op-ed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });
    const data = await res.json();

    if (data.success && data.detection && data.detection.detected) {
      const det = data.detection;

      // --- Helper: render confidence badge ---
      function renderConfBadge(conf) {
        let pct = 0;
        if (typeof conf === 'number') {
          pct = Math.round(conf <= 1 ? conf * 100 : conf);
        } else if (typeof conf === 'string') {
          if (conf.toLowerCase() === 'high') pct = 95;
          else if (conf.toLowerCase() === 'medium') pct = 70;
          else if (conf.toLowerCase() === 'low') pct = 40;
          else pct = Math.round(parseFloat(conf) <= 1 ? parseFloat(conf) * 100 : parseFloat(conf)) || 0;
        }
        let color, bg, label;
        if (pct >= 80) { color = '#00ff88'; bg = 'rgba(0,255,136,0.15)'; label = 'HIGH'; }
        else if (pct >= 50) { color = '#ffaa00'; bg = 'rgba(255,170,0,0.15)'; label = 'MED'; }
        else { color = '#ff5555'; bg = 'rgba(255,85,85,0.15)'; label = 'LOW'; }
        return { html: `<span style="color:${color};background:${bg};padding:2px 8px;border-radius:4px;font-size:0.78rem;font-weight:700;">${label} ${pct}%</span>`, color };
      }

      // --- Helper: render signal tags ---
      function renderSignals(signals) {
        if (!signals) return '';
        let list = [];
        if (Array.isArray(signals)) {
          list = signals;
        } else if (typeof signals === 'object') {
          list = Object.keys(signals);
        }
        if (list.length === 0) return '';
        const tagColors = {
          'silence_boundary': '#00f0ff',
          'scene_density': '#a855f7',
          'credit_text': '#ffaa00',
          'duration_typical': '#00ff88',
          'structure_position': '#ff6b9d',
          'audio_energy': '#38bdf8',
          'visual': '#a855f7',
          'audio': '#00f0ff',
          'credit': '#ffaa00',
          'structure': '#00ff88'
        };
        return list.map(s => {
          const c = tagColors[s] || '#8b9bb4';
          return `<span style="background:rgba(255,255,255,0.06);border:1px solid ${c}33;color:${c};padding:1px 6px;border-radius:3px;font-size:0.7rem;">${s.replace(/_/g,' ')}</span>`;
        }).join('');
      }

      // --- Populate OP ---
      if (det.op) {
        const checkIntro = document.getElementById('trimCheckIntro');
        const introRadioRange = document.getElementById('trimIntroModeRange');
        if (checkIntro) checkIntro.checked = true;
        if (introRadioRange) introRadioRange.checked = true;
        document.getElementById('trimIntroSecondsInput').style.display = 'none';
        document.getElementById('trimIntroRangeInput').style.display = 'block';
        document.getElementById('trimIntroRange').value = `${det.op.start_str}-${det.op.end_str}`;
        document.getElementById('trimIntroBody')?.classList.remove('disabled');

        // Show OP confidence card
        const opCard = document.getElementById('trimOpConfCard');
        if (opCard) {
          opCard.style.display = 'block';
          const badge = renderConfBadge(det.op.confidence);
          document.getElementById('trimOpConfBadge').innerHTML = badge.html;
          document.getElementById('trimOpSignals').innerHTML = renderSignals(det.op.signals);
        }
      }

      // --- Populate ED ---
      if (det.ed) {
        const checkOutro = document.getElementById('trimCheckOutro');
        const outroRadioRange = document.getElementById('trimOutroModeRange');
        if (checkOutro) checkOutro.checked = true;
        if (outroRadioRange) outroRadioRange.checked = true;
        document.getElementById('trimOutroSecondsInput').style.display = 'none';
        document.getElementById('trimOutroRangeInput').style.display = 'block';
        document.getElementById('trimOutroRange').value = `${det.ed.start_str}-${det.ed.end_str}`;
        document.getElementById('trimOutroBody')?.classList.remove('disabled');

        // Show ED confidence card
        const edCard = document.getElementById('trimEdConfCard');
        if (edCard) {
          edCard.style.display = 'block';
          const badge = renderConfBadge(det.ed.confidence);
          document.getElementById('trimEdConfBadge').innerHTML = badge.html;
          document.getElementById('trimEdSignals').innerHTML = renderSignals(det.ed.signals);
        }
      }

      // --- Post-credit protection ---
      const postCreditCard = document.getElementById('trimPostCreditCard');
      if (det.post_credit && det.post_credit.detected && postCreditCard) {
        postCreditCard.style.display = 'block';
        const pcInfo = document.getElementById('trimPostCreditInfo');
        if (pcInfo) {
          const pcStart = det.post_credit.start_str || formatTime(det.post_credit.start || 0);
          pcInfo.textContent = `เริ่มที่ ${pcStart} — ระบบจะเก็บเนื้อหาหลังเครดิตไว้ (${Math.round(det.post_credit.duration || 0)}วินาที)`;
        }
      } else if (postCreditCard) {
        postCreditCard.style.display = 'none';
      }

      // Show dashboard
      if (dashboard) dashboard.style.display = 'block';

      if (statusLbl) {
        const opTxt = det.op ? `${det.op.start_str}-${det.op.end_str}` : 'ไม่พบ';
        const edTxt = det.ed ? `${det.ed.start_str}-${det.ed.end_str}` : 'ไม่พบ';
        statusLbl.innerHTML = `✅ <b>ตรวจพบอัตโนมัติ:</b> OP (<b>${opTxt}</b>) | ED (<b>${edTxt}</b>) [ความแม่นยำ: ${det.confidence}]`;
        statusLbl.style.color = '#00ff88';
      }

      updateTrimPreview();
    } else {
      alert(`ไม่สามารถตรวจจับได้: ${data.error || 'ไม่พบช่วง OP/ED ชัดเจน'}`);
    }
  } catch (err) {
    alert(`ข้อผิดพลาดขณะสแกนคลิป: ${err.message}`);
  } finally {
    btn.textContent = '🔍 สแกนตรวจจับทันที';
    btn.disabled = false;
  }
});

// =========================================================
// Phase 6: Provenance & Publication-Readiness UI Handlers
// =========================================================
const rightsStatusPill = document.getElementById('rightsStatusPill');
const rightsStatusDot = document.getElementById('rightsStatusDot');
const rightsStatusValue = document.getElementById('rightsStatusValue');
const modalProvenance = document.getElementById('modalProvenance');
const btnCloseProvenanceModal = document.getElementById('btnCloseProvenanceModal');
const btnCloseProvenanceModalFooter = document.getElementById('btnCloseProvenanceModalFooter');
const btnRefreshReadiness = document.getElementById('btnRefreshReadiness');
const provenanceTableBody = document.getElementById('provenanceTableBody');
const readinessFindingsList = document.getElementById('readinessFindingsList');
const assetEditFormBox = document.getElementById('assetEditFormBox');
const btnCancelEditAsset = document.getElementById('btnCancelEditAsset');
const btnSaveAssetProvenance = document.getElementById('btnSaveAssetProvenance');

let currentProvenanceManifest = null;
let currentReadinessReport = null;

async function updateProjectReadinessBadge(pId) {
  if (!pId) return;
  try {
    const res = await fetch(`/api/projects/${pId}/publication-readiness`);
    const data = await res.json();
    if (!data.success || !data.report) return;

    const rep = data.report;
    currentReadinessReport = rep;

    if (rightsStatusValue) {
      rightsStatusValue.textContent = rep.statusLabel || 'Review Required';
      if (rep.overallStatus === 'checklist_complete') {
        rightsStatusValue.style.color = '#00ff88';
        if (rightsStatusDot) rightsStatusDot.style.background = '#00ff88';
      } else if (rep.overallStatus === 'restricted') {
        rightsStatusValue.style.color = '#ff4444';
        if (rightsStatusDot) rightsStatusDot.style.background = '#ff4444';
      } else {
        rightsStatusValue.style.color = '#ffaa00';
        if (rightsStatusDot) rightsStatusDot.style.background = '#ffaa00';
      }
    }
  } catch (err) {
    console.warn('[Provenance Badge] Error:', err);
  }
}

async function openProvenanceModal(pId) {
  if (!pId) {
    alert('กรุณาเลือกโปรเจกต์ก่อนตรวจสอบสิทธิ์');
    return;
  }
  modalProvenance.style.display = 'flex';
  assetEditFormBox.style.display = 'none';
  await refreshProvenanceData(pId);
}

async function refreshProvenanceData(pId) {
  try {
    // 1. Fetch provenance manifest
    let provRes = await fetch(`/api/projects/${pId}/provenance`);
    let provData = await provRes.json();

    // If project hasn't been migrated, run idempotent migration
    if (!provData.manifest) {
      const migRes = await fetch(`/api/projects/${pId}/provenance/migrate`, { method: 'POST' });
      provData = await migRes.json();
    }
    currentProvenanceManifest = provData.manifest;

    // 2. Fetch publication readiness report
    const readRes = await fetch(`/api/projects/${pId}/publication-readiness`);
    const readData = await readRes.json();
    currentReadinessReport = readData.report;

    renderReadinessReportUI(currentReadinessReport);
    renderProvenanceTableUI(currentProvenanceManifest);
    updateProjectReadinessBadge(pId);
  } catch (err) {
    alert(`ไม่สามารถโหลดข้อมูลสิทธิ์ได้: ${err.message}`);
  }
}

function renderReadinessReportUI(report) {
  if (!report) return;

  const banner = document.getElementById('readinessStatusBanner');
  const bannerTitle = document.getElementById('readinessBannerTitle');
  if (bannerTitle) bannerTitle.textContent = report.statusLabel || 'Review Required';

  if (banner) {
    if (report.overallStatus === 'checklist_complete') {
      banner.style.background = 'rgba(0, 255, 136, 0.1)';
      banner.style.borderColor = 'rgba(0, 255, 136, 0.4)';
      if (bannerTitle) bannerTitle.style.color = '#00ff88';
    } else if (report.overallStatus === 'restricted') {
      banner.style.background = 'rgba(255, 68, 68, 0.1)';
      banner.style.borderColor = 'rgba(255, 68, 68, 0.4)';
      if (bannerTitle) bannerTitle.style.color = '#ff4444';
    } else {
      banner.style.background = 'rgba(255, 170, 0, 0.1)';
      banner.style.borderColor = 'rgba(255, 170, 0, 0.4)';
      if (bannerTitle) bannerTitle.style.color = '#ffaa00';
    }
  }

  // Summary counts
  const sum = report.summary || {};
  document.getElementById('statTotalAssets').textContent = sum.totalAssets || 0;
  document.getElementById('statAuthorized').textContent = sum.authorized || 0;
  document.getElementById('statNeedsReview').textContent = sum.needsReview || 0;
  document.getElementById('statRestricted').textContent = (sum.restricted || 0) + (sum.expired || 0);

  // Findings list
  if (readinessFindingsList) {
    readinessFindingsList.innerHTML = '';
    const findings = report.findings || [];
    if (findings.length === 0) {
      readinessFindingsList.innerHTML = '<div style="color: #00ff88; font-size: 0.85rem; padding: 6px 0;">✅ ไม่พบข้อติดขัดทางเทคนิคที่ต้องแก้ไข</div>';
    } else {
      for (const f of findings) {
        const item = document.createElement('div');
        const isBlock = f.severity === 'BLOCKING';
        item.style.padding = '8px 12px';
        item.style.borderRadius = '6px';
        item.style.fontSize = '0.8rem';
        item.style.background = isBlock ? 'rgba(255,68,68,0.1)' : 'rgba(255,170,0,0.08)';
        item.style.border = `1px solid ${isBlock ? 'rgba(255,68,68,0.3)' : 'rgba(255,170,0,0.2)'}`;
        item.innerHTML = `
          <div style="font-weight: bold; color: ${isBlock ? '#ff6666' : '#ffbb33'};">[${f.severity}] ${f.message}</div>
          <div style="color: #ccc; margin-top: 2px;">💡 ข้อแนะนำ: ${f.remediation}</div>
        `;
        readinessFindingsList.appendChild(item);
      }
    }
  }
}

function renderProvenanceTableUI(manifest) {
  if (!provenanceTableBody) return;
  provenanceTableBody.innerHTML = '';

  const assets = manifest && manifest.assets ? Object.values(manifest.assets) : [];
  if (assets.length === 0) {
    provenanceTableBody.innerHTML = '<tr><td colspan="6" style="padding: 16px; text-align: center; color: #888;">ยังไม่มีการบันทึกสินทรัพย์</td></tr>';
    return;
  }

  for (const a of assets) {
    const tr = document.createElement('tr');
    tr.style.borderBottom = '1px solid rgba(255,255,255,0.05)';

    let statusBadgeColor = '#ffaa00';
    if (a.rightsStatus === 'authorized') statusBadgeColor = '#00ff88';
    else if (a.rightsStatus === 'restricted' || a.rightsStatus === 'expired') statusBadgeColor = '#ff4444';

    const parentLineageText = a.parentAssetIds && a.parentAssetIds.length > 0
      ? a.parentAssetIds.map(p => `<code style="background: rgba(255,255,255,0.06); padding: 1px 4px; border-radius: 3px;">${p}</code>`).join(', ')
      : '<span style="color: #666;">ต้นกำเนิด</span>';

    tr.innerHTML = `
      <td style="padding: 10px 12px; font-weight: bold; color: #fff;">
        <div>${a.filename || a.assetId}</div>
        <small style="color: #666; font-family: monospace;">${a.assetId}</small>
      </td>
      <td style="padding: 10px 12px; color: #aaa;">${a.assetType}</td>
      <td style="padding: 10px 12px;">
        <span style="color: ${statusBadgeColor}; font-weight: bold; background: rgba(255,255,255,0.04); padding: 3px 8px; border-radius: 4px; border: 1px solid ${statusBadgeColor}44;">
          ${a.rightsStatus}
        </span>
      </td>
      <td style="padding: 10px 12px; color: #ddd;">${a.license || '—'}</td>
      <td style="padding: 10px 12px; max-width: 180px; overflow-wrap: break-word;">${parentLineageText}</td>
      <td style="padding: 10px 12px;">
        <button class="btn btn-outline" style="font-size: 0.75rem; padding: 4px 8px;" onclick="openAssetEditForm('${a.assetId}')">✏️ แก้ไขสิทธิ์</button>
      </td>
    `;
    provenanceTableBody.appendChild(tr);
  }
}

window.openAssetEditForm = function(assetId) {
  if (!currentProvenanceManifest || !currentProvenanceManifest.assets[assetId]) return;
  const a = currentProvenanceManifest.assets[assetId];

  document.getElementById('editAssetId').value = assetId;
  document.getElementById('editAssetTitle').textContent = `${a.filename || assetId} (${a.assetType})`;
  document.getElementById('editRightsStatus').value = a.rightsStatus || 'needs_review';
  document.getElementById('editLicense').value = a.license || '';
  document.getElementById('editEvidence').value = a.evidence || '';
  document.getElementById('editReviewedBy').value = a.reviewedBy || '';
  document.getElementById('editAttribution').value = a.attribution || '';
  document.getElementById('editReviewerNotes').value = a.reviewerNotes || '';

  assetEditFormBox.style.display = 'block';
  assetEditFormBox.scrollIntoView({ behavior: 'smooth' });
};

if (btnCancelEditAsset) {
  btnCancelEditAsset.addEventListener('click', () => {
    assetEditFormBox.style.display = 'none';
  });
}

if (btnSaveAssetProvenance) {
  btnSaveAssetProvenance.addEventListener('click', async () => {
    const assetId = document.getElementById('editAssetId').value;
    if (!assetId || !currentProjectId) return;

    const payload = {
      rightsStatus: document.getElementById('editRightsStatus').value,
      license: document.getElementById('editLicense').value.trim() || null,
      evidence: document.getElementById('editEvidence').value.trim() || null,
      reviewedBy: document.getElementById('editReviewedBy').value.trim() || null,
      attribution: document.getElementById('editAttribution').value.trim() || null,
      reviewerNotes: document.getElementById('editReviewerNotes').value.trim() || null
    };

    try {
      const res = await fetch(`/api/projects/${currentProjectId}/provenance/${assetId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!data.success) {
        alert(`ไม่สามารถบันทึกข้อมูลได้: ${data.error}`);
        return;
      }

      assetEditFormBox.style.display = 'none';
      await refreshProvenanceData(currentProjectId);
    } catch (err) {
      alert(`ข้อผิดพลาดขณะบันทึก: ${err.message}`);
    }
  });
}

if (rightsStatusPill) {
  rightsStatusPill.addEventListener('click', () => {
    openProvenanceModal(currentProjectId);
  });
}

if (btnCloseProvenanceModal) {
  btnCloseProvenanceModal.addEventListener('click', () => {
    modalProvenance.style.display = 'none';
  });
}

if (btnCloseProvenanceModalFooter) {
  btnCloseProvenanceModalFooter.addEventListener('click', () => {
    modalProvenance.style.display = 'none';
  });
}

if (btnRefreshReadiness) {
  btnRefreshReadiness.addEventListener('click', () => {
    refreshProvenanceData(currentProjectId);
  });
}

// ================================================
// Cyber Toast Notification System
// ================================================
function showToast(message, type = 'info', durationMs = 4000) {
  let container = document.getElementById('toastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toastContainer';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }

  const icons = {
    success: '✅',
    info: '🎬',
    error: '❌',
    warning: '⚠️'
  };

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <span style="font-size: 1.1rem;">${icons[type] || '🔔'}</span>
    <span style="flex: 1;">${message}</span>
  `;

  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('fade-out');
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 300);
  }, durationMs);
}
window.showToast = showToast;

// ================================================
// Concat NLE Studio Integration
// ================================================
const btnOpenConcat = document.getElementById('btnOpenConcat');
const btnExportConcat = document.getElementById('btnExportConcat');
const concatSyncStatus = document.getElementById('concatSyncStatus');
const concatSyncStatusText = document.getElementById('concatSyncStatusText');
const modalConcatNotFound = document.getElementById('modalConcatNotFound');
const btnCloseConcatModal = document.getElementById('btnCloseConcatModal');
const btnDismissConcatModal = document.getElementById('btnDismissConcatModal');

function setConcatLoading(isLoading, text = '') {
  if (btnOpenConcat) btnOpenConcat.disabled = isLoading;
  if (btnExportConcat) btnExportConcat.disabled = isLoading;
  if (concatSyncStatus) {
    concatSyncStatus.style.display = isLoading ? 'flex' : 'none';
    if (concatSyncStatusText && text) {
      concatSyncStatusText.textContent = text;
    }
  }
}

// 1. Open in Concat NLE
if (btnOpenConcat) {
  btnOpenConcat.addEventListener('click', async () => {
    if (!currentProjectId) {
      showToast('กรุณาเลือกหรือสร้างโปรเจกต์ก่อน', 'warning');
      return;
    }

    setConcatLoading(true, 'กำลังซิงค์และเริ่มโปรแกรม Concat NLE...');

    try {
      const res = await fetch(`/api/projects/${currentProjectId}/open-in-concat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      const data = await res.json();

      if (data.success) {
        showToast(`เปิด Concat NLE สำเร็จ! (PID: ${data.pid || 'Active'})`, 'success', 5000);
      } else {
        if (data.binaryMissing || res.status === 404 || (data.error && data.error.includes('concat.exe'))) {
          if (modalConcatNotFound) {
            modalConcatNotFound.style.display = 'flex';
          } else {
            showToast('ไม่พบโปรแกรม Concat บนเครื่อง แต่ส่งออก concat.json สำเร็จแล้ว', 'warning', 6000);
          }
        } else {
          showToast(`เปิด Concat ล้มเหลว: ${data.error}`, 'error', 5000);
        }
      }
    } catch (err) {
      showToast(`เกิดข้อผิดพลาดในการเชื่อมต่อ: ${err.message}`, 'error', 5000);
    } finally {
      setConcatLoading(false);
    }
  });
}

// 2. Export Concat Only (Sync Only)
if (btnExportConcat) {
  btnExportConcat.addEventListener('click', async () => {
    if (!currentProjectId) {
      showToast('กรุณาเลือกหรือสร้างโปรเจกต์ก่อน', 'warning');
      return;
    }

    setConcatLoading(true, 'กำลังส่งออกโปรเจกต์ไปยัง Concat...');

    try {
      const res = await fetch(`/api/projects/${currentProjectId}/export-concat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      const data = await res.json();

      if (data.success) {
        const scCount = data.summary?.scenesCount || 0;
        showToast(`ส่งออก Concat สำเร็จ (${scCount} ฉาก, 2 ไทม์ไลน์ 16:9 & 9:16)`, 'success', 4500);
      } else {
        showToast(`ส่งออกล้มเหลว: ${data.error}`, 'error', 5000);
      }
    } catch (err) {
      showToast(`เกิดข้อผิดพลาด: ${err.message}`, 'error', 5000);
    } finally {
      setConcatLoading(false);
    }
  });
}

// Close Modal Concat Not Found
if (btnCloseConcatModal) {
  btnCloseConcatModal.addEventListener('click', () => {
    if (modalConcatNotFound) modalConcatNotFound.style.display = 'none';
  });
}
if (btnDismissConcatModal) {
  btnDismissConcatModal.addEventListener('click', () => {
    if (modalConcatNotFound) modalConcatNotFound.style.display = 'none';
  });
}


