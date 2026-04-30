// ==========================================================================
// ROSETTA MAGAZINE RESEARCHER - MAIN APPLICATION LOGIC
// ==========================================================================

// Tell the Markdown parser to respect single line breaks natively
marked.use({ breaks: true });

// ==========================================
// --- 1. GLOBALS & STATE MANAGEMENT ---
// ==========================================
const magSelect = document.getElementById('mag-select');
const pageInput = document.getElementById('page-num');
const img = document.getElementById('page-img');
const container = document.getElementById('img-container');

// Application State
let isEditing = false;
let isMarkdown = true; 
let currentRawData = null;
let bookmarksData = {};
let metadataCache = {};
let localFiles = [];
let catalogData =[];
let maxPage = 1;
const lensZoomFactor = 4;
let labelToPath = {};
let pathToLabel = {};
let itemsWithUpdates =[];
let completedDownloads = new Set();

// Editor State
let currentModalItemId = null;
let contentEditor = null;
let activeContentEditorKey = null;
let contentEditorMode = 'wysiwyg';
let pendingConfirmAction = null;

const CONTENT_EDITOR_FIELDS = {
    jp: { textareaId: 'jp-edit', title: 'Transcription', subtitle: 'Visual editing for page transcription. Markdown is saved under the hood.' },
    en: { textareaId: 'en-edit', title: 'Translation', subtitle: 'Visual editing for page translation. Markdown is saved under the hood.' },
    sum: { textareaId: 'sum-edit', title: 'Page Summary', subtitle: 'Visual editing for page summary text. Markdown is saved under the hood.' },
};

const METADATA_FIELDS =[
    { key: 'name', label: 'Magazine Name' }, { key: 'publisher', label: 'Publisher' },
    { key: 'date', label: 'Date' }, { key: 'issue_name', label: 'Issue Name' },
    { key: 'region', label: 'Region' }, { key: 'translation', label: 'Translation' },
    { key: 'version', label: 'Version' }, { key: 'tags', label: 'Tags' },
    { key: 'scanner', label: 'Scanner' }, { key: 'scanner_url', label: 'Scanner URL' },
    { key: 'editor', label: 'Editor' }, { key: 'editor_url', label: 'Editor URL' },
    { key: 'notes', label: 'Notes' },
];

const METADATA_KEY_MAP = Object.fromEntries(
    METADATA_FIELDS.map(field => [field.label.toLowerCase(), field.key])
);

// ==========================================
// --- 2. INITIALIZATION & CORE REFRESH ---
// ==========================================

/**
 * Main boot sequence. Fetches local magazine list and handles first-run logic.
 */
async function init(forceUpdate = false) {
    const res = await fetch('/api/list');
    const data = await res.json();
    metadataCache = data.metadata || {};
    localFiles = data.files || [];
    
    // Clear and rebuild dropdowns
    const oldVal = magSelect.value;
    magSelect.innerHTML = '';
    const dl = document.getElementById('mag-select-list');
    dl.innerHTML = '';
    
    labelToPath = {};
    pathToLabel = {};
    
    if (localFiles.length > 0) {
        // --- POPULATE EXISTING MAGAZINES ---
        localFiles.forEach(m => {
            const meta = metadataCache[m] || {};
            let label = meta.name ? meta.name : m.split('/').pop().replace('.pdf','');
            if (meta.date) label += ` (${meta.date})`;
            if (meta.issue_name) label += ` - ${meta.issue_name}`;
            
            labelToPath[label] = m;
            pathToLabel[m] = label;
            
            let opt = document.createElement('option');
            opt.value = m;
            magSelect.appendChild(opt);
            
            let dlOpt = document.createElement('option');
            dlOpt.value = label;
            dl.appendChild(dlOpt);
        });

        // Restore selection or pick the first one
        if (localFiles.includes(oldVal)) {
            magSelect.value = oldVal;
        } else {
            magSelect.value = localFiles[0];
        }
        
        document.getElementById('mag-input').value = pathToLabel[magSelect.value] || "";
        if (forceUpdate) update();

    } else {
        // --- FIRST RUN / EMPTY STATE LOGIC ---
        document.getElementById('page-title').innerText = "Welcome! Open the Library to download your first magazine.";
        document.getElementById('mag-input').value = "";
        
        // Automatically open the library if no magazines exist
        if (forceUpdate) {
            setTimeout(() => toggleLibrary(true), 500); 
        }
    }

    // Sync Search tab datalists
    const magSet = new Set();
    Object.values(metadataCache).forEach(meta => { if (meta.name) magSet.add(meta.name); });
    const dlSearch = document.getElementById('mag-datalist');
    dlSearch.innerHTML = '';
    Array.from(magSet).sort().forEach(m => dlSearch.innerHTML += `<option value="${m}">`);
    
    fetchBookmarks();
    fetchCatalog();
}

/**
 * Loads the currently selected magazine and page from the backend API.
 */
async function update(targetPage = null, searchTerms = null) {
    if (searchTerms) window.activeSearchTerms = searchTerms;
    else window.activeSearchTerms = null;

    const mag = magSelect.value; 
    if (!mag) return;

    if (targetPage) pageInput.value = targetPage;
    const page = pageInput.value;

    document.getElementById('mag-input').value = pathToLabel[mag] || mag.split('/').pop();
    img.src = ""; // Clear image while loading
    document.getElementById('page-title').innerText = "Loading Issue...";
    
    // Request Image
    img.src = `/api/render?mag=${encodeURIComponent(mag)}&page=${page-1}&zoom=1.5&t=${Date.now()}`;
    adjustImgZoom(0);

    // Request Text & Metadata
    try {
        const res = await fetch(`/api/text?mag=${encodeURIComponent(mag)}&page=${page}&t=${Date.now()}`);
        if (!res.ok) throw new Error("Server error fetching text");
        currentRawData = await res.json();
    } catch (err) {
        console.warn("Could not fetch page text, showing empty state:", err);
        currentRawData = {
            jp: "No transcription found for this page.", en: "", sum: "",
            total_pages: 1, metadata: metadataCache[mag] || {}, raw_meta: ""
        };
    }

    maxPage = currentRawData.total_pages || 1;
    drawCoordinateBoxes(currentRawData.coordinates ||[]);
    renderContent();
    renderMetadata(currentRawData.metadata || {}, page, mag);
    
    // Reset any open editors
    closeContentEditor();
    closeMetadataEditor();
    closeBookmarkModal();
    if (isEditing) { 
        isEditing = false; 
        toggleEdit(); 
    }
}

function changePage(d) {
    let next = parseInt(pageInput.value) + d;
    if (next >= 1 && next <= maxPage) { pageInput.value = next; update(); }
}


// ==========================================
// --- 3. VIEWER ZOOM & PANNING LOGIC ---
// ==========================================
let currentImgZoom = 100;

function adjustImgZoom(delta) {
    setImgZoom(currentImgZoom + delta);
}

function setImgZoom(val) {
    let num = parseInt(String(val).replace('%', ''));
    if (isNaN(num)) num = 100;
    
    currentImgZoom = Math.max(30, Math.min(num, 500));
    
    const img = document.getElementById('page-img');
    const leftPanel = document.getElementById('left');
    const safeHeight = leftPanel.clientHeight - 40; 
    
    img.style.height = `${safeHeight * (currentImgZoom / 100)}px`;
    document.getElementById('zoom-input').value = `${currentImgZoom}%`;
}

window.addEventListener('resize', () => {
    adjustImgZoom(0);
    syncContentEditorHeight();
});

document.getElementById('left').addEventListener('wheel', (e) => {
    if (e.ctrlKey || e.metaKey) {
        e.preventDefault(); 
        adjustImgZoom(e.deltaY < 0 ? 10 : -10);
    }
}, { passive: false });


// ==========================================
// --- 4. CONTENT RENDERING & EDITING ---
// ==========================================

function toggleMarkdown() {
    isMarkdown = !isMarkdown;
    document.getElementById('md-toggle').classList.toggle('active', isMarkdown);
    renderContent();
}

function renderContent() {
    if (!currentRawData) return;
    const boxes = {
        'jp-box': currentRawData.jp, 
        'en-box': currentRawData.en, 
        'synopsis-box': currentRawData.sum
    };

    for (const [id, text] of Object.entries(boxes)) {
        const el = document.getElementById(id);
        if (!el) continue;

        const content = text || "";

        if (isMarkdown) {
            let safeText = content
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/&lt;br\s*\/?&gt;/gi, "<br>");
            
            if (id === 'jp-box' || id === 'en-box') {
                safeText = safeText.replace(/^\s*・/gm, '- '); 
                safeText = safeText.replace(/^\s*([0-9]+\.)([^\s])/gm, '$1 $2'); 
            }

            el.innerHTML = marked.parse(safeText); 
            el.classList.add('markdown-mode'); 
        } else {
            el.innerText = content; 
            el.classList.remove('markdown-mode'); 
        }
    }
    
    if (isMarkdown && currentRawData.coordinates && currentRawData.coordinates.length > 0) {
        attachTextHoverMagic();
        if (window.activeSearchTerms) {
            setTimeout(() => triggerSearchHighlight(window.activeSearchTerms), 100);
        }
    }
}

function toggleEdit() {
    isEditing = !isEditing;
    const viewBoxes =["jp-box", "en-box", "synopsis-box", "meta-display"];
    const editBoxes =["jp-edit", "en-edit", "sum-edit", "meta-edit-container"];
    
    document.getElementById('edit-btn').classList.toggle('active', isEditing);
    document.getElementById('save-btn').style.display = isEditing ? 'inline-block' : 'none';
    document.body.classList.toggle('editing-active', isEditing);
    
    isZoneEditMode = isEditing;
    document.getElementById('img-container').classList.toggle('zone-edit-mode', isZoneEditMode);
    document.getElementById('zone-editor-panel').style.display = isZoneEditMode ? 'block' : 'none';
    
    if(isEditing) {
        document.getElementById('jp-edit').value = currentRawData.jp;
        document.getElementById('en-edit').value = currentRawData.en;
        document.getElementById('sum-edit').value = currentRawData.sum;
        document.getElementById('meta-edit').value = currentRawData.raw_meta || "";
        updateMetadataSummary();
    } else { 
        closeContentEditor();
        closeMetadataEditor();
        drawCoordinateBoxes(currentRawData.coordinates ||[]);
        renderContent();
    }
    
    viewBoxes.forEach(id => document.getElementById(id).style.display = isEditing ? 'none' : 'block');
    editBoxes.forEach(id => document.getElementById(id).style.display = isEditing ? 'block' : 'none');

    if (!isEditing && isMarkdown) {
        setTimeout(() => attachTextHoverMagic(), 50);
    }
}

async function saveCorrections() {
    // Collect Spatial Boxes
    const coordsGroup = {};
    let maxIndex = -1;
    const padding = 5; 
    
    document.querySelectorAll('.spatial-box').forEach(box => {
        if (box.dataset.index) {
            let parsed = parseInt(box.dataset.index, 10);
            if (!isNaN(parsed)) maxIndex = Math.max(maxIndex, parsed);
        }
    });

    document.querySelectorAll('.spatial-box').forEach(box => {
        let idx = box.dataset.index;
        if (!idx) {
            maxIndex++;
            idx = maxIndex.toString();
            box.dataset.index = idx; 
        }
        
        const text = (box.dataset.jpText || "").trim();
        const t = parseFloat(box.style.top);
        const l = parseFloat(box.style.left);
        const h = parseFloat(box.style.height);
        const w = parseFloat(box.style.width);
        
        const ymin = Math.max(0, Math.round(t * 10) + padding);
        const xmin = Math.max(0, Math.round(l * 10) + padding);
        const ymax = Math.min(1000, Math.round((t + h) * 10) - padding);
        const xmax = Math.min(1000, Math.round((l + w) * 10) - padding);
        
        if (!coordsGroup[idx]) coordsGroup[idx] = { text: text, boxes: [] };
        coordsGroup[idx].boxes.push([ymin, xmin, ymax, xmax]);
        if (text) coordsGroup[idx].text = text; 
    });

    const newCoords = Object.values(coordsGroup);

    // Prepare API Payload
    const payload = {
        mag: magSelect.value, 
        page: parseInt(pageInput.value, 10),
        jp: document.getElementById('jp-edit').value,
        en: document.getElementById('en-edit').value,
        sum: document.getElementById('sum-edit').value,
        meta: document.getElementById('meta-edit').value,
        coords: newCoords 
    };
    const btn = document.getElementById('save-btn');
    btn.innerText = "Saving...";
    
    try {
        const res = await fetch('/api/save', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
        if(res.ok) { 
            btn.innerText = "✅ Saved!"; 
            setTimeout(() => btn.innerText = "💾 Save", 2000);
            
            if (currentRawData) {
                currentRawData.jp = payload.jp;
                currentRawData.en = payload.en;
                currentRawData.sum = payload.sum;
                currentRawData.raw_meta = payload.meta;
                currentRawData.coordinates = payload.coords; 
                currentRawData.metadata = parseMetadataText(payload.meta).fields;
                renderMetadata(currentRawData.metadata, payload.page, payload.mag);
            }
        } else {
            btn.innerText = "❌ Error";
            setTimeout(() => btn.innerText = "💾 Save", 2000);
        }
    } catch (e) {
        btn.innerText = "❌ Error";
        setTimeout(() => btn.innerText = "💾 Save", 2000);
    }
}


// ==========================================
// --- 5. ADVANCED TOAST-UI EDITORS ---
// ==========================================

function ensureContentEditor() {
    if (contentEditor) return;
    contentEditor = new toastui.Editor({
        el: document.getElementById('editor-modal-host'),
        height: '400px',
        initialEditType: 'wysiwyg',
        hideModeSwitch: true,
        usageStatistics: false,
        customHTMLRenderer: { softbreak() { return { type: 'text', content: ' ' }; } },
        previewStyle: 'vertical',
        toolbarItems: [
            ['heading', 'bold', 'italic', 'strike'],
            ['hr', 'quote'],
            ['ul', 'ol', 'task'],['table', 'link'],
        ],
    });
    syncContentEditorTheme();
}

function syncContentEditorHeight() {
    if (!contentEditor) return;
    const host = document.getElementById('editor-modal-host');
    if (!host) return;
    const nextHeight = Math.max(260, Math.floor(host.clientHeight || 0));
    if (typeof contentEditor.setHeight === 'function') contentEditor.setHeight(`${nextHeight}px`);
}

function setContentEditorMode(mode, options = {}) {
    contentEditorMode = mode;
    document.getElementById('editor-mode-rich').classList.toggle('active', mode === 'wysiwyg');
    document.getElementById('editor-mode-markdown').classList.toggle('active', mode === 'markdown');
    document.getElementById('editor-mode-split').classList.toggle('active', mode === 'split');

    if (!contentEditor) return;

    if (mode === 'wysiwyg') {
        contentEditor.changePreviewStyle('tab');
        contentEditor.changeMode('wysiwyg', true);
    } else if (mode === 'markdown') {
        contentEditor.changePreviewStyle('tab');
        contentEditor.changeMode('markdown', true);
    } else if (mode === 'split') {
        contentEditor.changePreviewStyle('vertical');
        contentEditor.changeMode('markdown', true);
    }
    setTimeout(() => syncContentEditorHeight(), 0);
}

function syncContentEditorTheme() {
    const editorRoot = document.querySelector('#editor-modal-host .toastui-editor-defaultUI');
    if (!editorRoot) return;
    editorRoot.classList.toggle('toastui-editor-dark', !document.body.classList.contains('light-mode'));
}

function openContentEditor(fieldKey) {
    if (!isEditing || !CONTENT_EDITOR_FIELDS[fieldKey]) return;

    activeContentEditorKey = fieldKey;
    document.getElementById('editor-modal-overlay').style.display = 'flex';
    ensureContentEditor();

    const field = CONTENT_EDITOR_FIELDS[fieldKey];
    document.getElementById('editor-modal-title').innerText = field.title;
    document.getElementById('editor-modal-subtitle').innerText = field.subtitle;
    contentEditor.setMarkdown(document.getElementById(field.textareaId).value || "", false);
    
    syncContentEditorHeight();
    syncContentEditorTheme();
}

function closeContentEditor(e) {
    if (e && e.target.id !== 'editor-modal-overlay' && !e.target.classList.contains('close-modal')) return;
    document.getElementById('editor-modal-overlay').style.display = 'none';
    activeContentEditorKey = null;
}

function applyContentEditor() {
    if (!contentEditor || !activeContentEditorKey) return;
    const field = CONTENT_EDITOR_FIELDS[activeContentEditorKey];
    document.getElementById(field.textareaId).value = contentEditor.getMarkdown().trimEnd();
    closeContentEditor();
}

// METADATA EDITOR LOGIC
function createEmptyMetadataFields() { return Object.fromEntries(METADATA_FIELDS.map(field =>[field.key, ""])); }

function parseMetadataText(rawText = "") {
    const fields = createEmptyMetadataFields();
    const extraLines =[];
    rawText.replace(/\r\n/g, '\n').split('\n').forEach(line => {
        if (!line) { extraLines.push(line); return; }
        const separatorIndex = line.indexOf(':');
        if (separatorIndex === -1) { extraLines.push(line); return; }
        const rawKey = line.slice(0, separatorIndex).trim().toLowerCase();
        const mappedKey = METADATA_KEY_MAP[rawKey];
        if (!mappedKey) { extraLines.push(line); return; }
        fields[mappedKey] = line.slice(separatorIndex + 1).trim();
    });
    return { fields, extraLines: extraLines.join('\n').trimEnd() };
}

function serializeMetadataText(fields, extraLines = "") {
    const lines =[];
    METADATA_FIELDS.forEach(({ key, label }) => {
        const value = (fields[key] || "").trim();
        if (value) lines.push(`${label}: ${value}`);
    });
    const rawExtras = extraLines.replace(/\r\n/g, '\n').trimEnd();
    if (rawExtras) { if (lines.length) lines.push(''); lines.push(rawExtras); }
    return lines.join('\n');
}

function updateMetadataSummary() {
    const summaryEl = document.getElementById('meta-edit-summary');
    const previewEl = document.getElementById('meta-edit-preview');
    const rawText = document.getElementById('meta-edit').value || "";
    const { fields, extraLines } = parseMetadataText(rawText);
    
    const populatedCount = METADATA_FIELDS.filter(({ key }) => fields[key]).length;
    const extraCount = extraLines ? extraLines.split('\n').filter(line => line.trim()).length : 0;
    const headline =[fields.name, fields.date, fields.version ? `v${fields.version}` : ""].filter(Boolean).join(' • ');

    if (!headline && populatedCount === 0 && extraCount === 0) {
        summaryEl.innerText = "No metadata saved yet.";
        previewEl.className = 'meta-preview-empty';
        previewEl.innerText = "No metadata saved yet.";
        return;
    }

    const stats =[
        `${populatedCount} field${populatedCount === 1 ? '' : 's'} set`,
        extraCount ? `${extraCount} extra line${extraCount === 1 ? '' : 's'} preserved` : '',
    ].filter(Boolean).join(' • ');

    summaryEl.innerText =[headline, stats].filter(Boolean).join(' — ');

    // Preview Table
    const rows = METADATA_FIELDS.filter(({ key }) => fields[key]).map(({ label, key }) => `<tr><th scope="row">${escapeHtml(label)}</th><td>${escapeHtml(fields[key])}</td></tr>`);
    if (extraCount) rows.push(`<tr><th scope="row">Additional Raw Lines</th><td>${escapeHtml(extraLines)}</td></tr>`);
    previewEl.className = '';
    previewEl.innerHTML = `<table class="meta-preview-table"><tbody>${rows.join('')}</tbody></table>`;
}

function escapeHtml(value = "") {
    return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function renderMetadata(meta, page, filename) {
    const titleEl = document.getElementById('page-title');
    const metaEl = document.getElementById('meta-display');
    
    let displayTitle = meta.name || filename.split('/').pop().replace('.pdf','');
    if (meta.issue_name) displayTitle += ` — ${meta.issue_name}`;
    titleEl.innerText = displayTitle + " — Page " + page;

    let metaStr =[];
    if (meta.version) metaStr.push(`<b>Version</b> - <font color="FFFFFF">${meta.version}</font>`);
    if (meta.date) metaStr.push(`<b>Date</b> - <font color="FFFFFF">${meta.date}</font>`);
    if (meta.region) metaStr.push(`<b>Region</b> - <font color="FFFFFF">${meta.region}</font>`);
    if (meta.translation) metaStr.push(`<b>Translation</b> - <font color="FFFFFF">${meta.translation}</font>`);
    if (meta.publisher) metaStr.push(`<b>Publisher</b> - <font color="FFFFFF">${meta.publisher}</font>`);
    
    let credits =[];
    if (meta.scanner) {
        let s = meta.scanner_url ? `<a href="${meta.scanner_url}" target="_blank" class="scanner-link">${meta.scanner}</a>` : meta.scanner;
        credits.push(`<b>Scanned by</b> - ${s}`);
    }
    if (meta.editor) {
        let e = meta.editor_url ? `<a href="${meta.editor_url}" target="_blank" class="scanner-link">${meta.editor}</a>` : meta.editor;
        credits.push(`<b>Edited by</b> - ${e}`);
    }

    let finalHtml = metaStr.join(" • ");
    if (credits.length > 0) finalHtml += (finalHtml ? "<br>" : "") + credits.join(" | ");
    if (meta.tags) finalHtml += `<br><span style="color:#8ab4f8; font-size:12px; font-weight:bold;">Tags - ${meta.tags}</span>`;
    if (meta.notes) finalHtml += `<br><span style="color:#fde68a; font-size:11px;">Notes - ${meta.notes}</span>`;
    
    metaEl.innerHTML = finalHtml;
}

function openMetadataEditor() {
    if (!isEditing) return;
    const { fields, extraLines } = parseMetadataText(document.getElementById('meta-edit').value || "");
    METADATA_FIELDS.forEach(({ key }) => document.getElementById(`meta-field-${key}`).value = fields[key] || "");
    document.getElementById('meta-extra-lines').value = extraLines;
    document.getElementById('metadata-modal-overlay').style.display = 'flex';
}

function closeMetadataEditor(e) {
    if (e && e.target.id !== 'metadata-modal-overlay' && !e.target.classList.contains('close-modal')) return;
    document.getElementById('metadata-modal-overlay').style.display = 'none';
}

function applyMetadataEditor() {
    const fields = createEmptyMetadataFields();
    METADATA_FIELDS.forEach(({ key }) => fields[key] = document.getElementById(`meta-field-${key}`).value || "");
    document.getElementById('meta-edit').value = serializeMetadataText(fields, document.getElementById('meta-extra-lines').value || "");
    updateMetadataSummary();
    closeMetadataEditor();
}


// ==========================================
// --- 6. SEARCH & BOOKMARKS ---
// ==========================================

async function executeSearch() {
    const q = document.getElementById('search-in').value;
    const scope = document.querySelector('input[name="scope"]:checked').value;
    const incJp = document.getElementById('search-inc-jp').checked;
    const incEn = document.getElementById('search-inc-en').checked;
    const incSum = document.getElementById('search-inc-sum').checked;
    
    const magFilter = document.getElementById('search-mag').value;
    const dateStart = document.getElementById('search-date-start').value;
    const dateEnd = document.getElementById('search-date-end').value;
    const tagFilter = document.getElementById('search-tags').value;

    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&scope=${scope}&incJp=${incJp}&incEn=${incEn}&incSum=${incSum}&currentMag=${encodeURIComponent(magSelect.value)}&magFilter=${encodeURIComponent(magFilter)}&dateStart=${encodeURIComponent(dateStart)}&dateEnd=${encodeURIComponent(dateEnd)}&tagFilter=${encodeURIComponent(tagFilter)}`);
    const data = await res.json();
    const container = document.getElementById('search-results');
    container.innerHTML = '';
    
    if (data.results.length === 0) {
        container.innerHTML = '<div style="padding:20px; color:#888;">No matches.</div>';
    } else {
        const countLabel = document.createElement('div');
        countLabel.style.fontSize = "11px";
        countLabel.style.color = "var(--accent)";
        countLabel.style.marginBottom = "10px";
        countLabel.style.fontWeight = "bold";
        countLabel.innerText = `${data.results.length}${data.results.length >= 200 ? '+' : ''} results found`;
        container.appendChild(countLabel);
    }

    data.results.forEach(r => {
        const div = document.createElement('div'); div.className = 'result-item';
        let snip = r.snippet;
        data.terms_to_highlight.forEach(t => {
            const reHighlight = new RegExp(`(${t.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')})`, 'gi');
            snip = snip.replace(reHighlight, '<mark>$1</mark>');
        });
        const meta = metadataCache[r.mag] || {};
        let resultTitle = meta.name ? meta.name : r.mag.split('/').pop().replace('.pdf', '');
        if (meta.issue_name) resultTitle += ` - ${meta.date} - ${meta.issue_name}`;

        div.innerHTML = `<span style="color:var(--accent); font-weight:bold; font-size:11px;">${resultTitle} — P${r.page}</span><br><small>...${snip}...</small>`;
        div.onclick = () => { magSelect.value = r.mag; update(r.page, data.terms_to_highlight); };
        container.appendChild(div);
    });
}

function triggerSearchHighlight(termsArray) {
    if (!termsArray || termsArray.length === 0 || !window.currentBoxAssignments) return;
    
    let foundBox = null;
    let foundEl = null;
    const allBoxes = document.querySelectorAll('.spatial-box');

    allBoxes.forEach(box => {
        box.classList.remove('search-pulse');
        const idx = window.currentBoxAssignments.get(box);
        if (idx === undefined) return;

        const jpText = window.currentJpElements[idx]?.innerText.toLowerCase() || "";
        const enText = window.currentEnElements[idx]?.innerText.toLowerCase() || "";

        const matches = termsArray.some(term => {
            const cleanTerm = term.toLowerCase();
            return jpText.includes(cleanTerm) || enText.includes(cleanTerm);
        });

        if (matches) {
            box.classList.add('search-pulse'); 
            if (!foundBox) {
                foundBox = box;
                foundEl = window.currentEnElements[idx] || window.currentJpElements[idx];
            }
        }
    });

    if (foundBox) {
        foundBox.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
        if (foundEl) {
            foundEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            foundEl.style.transition = "background-color 0.3s";
            foundEl.style.backgroundColor = "rgba(255, 82, 82, 0.3)";
            setTimeout(() => foundEl.style.backgroundColor = "", 2000);
        }
    }
}

function formatSearchDate(input, type) {
    let val = input.value.trim().split('/').join('-').split('\\\\').join('-');
    if (!val) return;
    
    if (/^\d{8}$/.test(val)) { val = `${val.substring(0,4)}-${val.substring(4,6)}-${val.substring(6,8)}`; }
    else if (/^\d{6}$/.test(val)) { val = `${val.substring(0,4)}-${val.substring(4,6)}`; }
    else if (/^\d{1,2}-\d{1,2}-\d{4}$/.test(val)) { 
        let parts = val.split('-'); val = `${parts[2]}-${parts[0].padStart(2, '0')}-${parts[1].padStart(2, '0')}`;
    }
    else if (/^\d{1,2}-\d{1,2}-\d{2}$/.test(val)) { 
        let parts = val.split('-'); let yy = parseInt(parts[2]);
        let yyyy = yy < 50 ? 2000 + yy : 1900 + yy;
        val = `${yyyy}-${parts[0].padStart(2, '0')}-${parts[1].padStart(2, '0')}`;
    }
    else if (/^\d{1,2}-\d{4}$/.test(val)) { 
        let parts = val.split('-'); val = `${parts[1]}-${parts[0].padStart(2, '0')}`;
    }
    else if (/^\d{4}-\d{1,2}-\d{1,2}$/.test(val) || /^\d{4}-\d{1,2}$/.test(val)) { 
        let parts = val.split('-');
        val = `${parts[0]}` + (parts[1] ? `-${parts[1].padStart(2, '0')}` : '') + (parts[2] ? `-${parts[2].padStart(2, '0')}` : '');
    }

    if (/^\d{4}$/.test(val)) { val = type === 'start' ? `${val}-01-01` : `${val}-12-31`; } 
    else if (/^\d{4}-\d{2}$/.test(val)) {
        if (type === 'start') { val = `${val}-01`; } 
        else {
            let lastDay = new Date(parseInt(val.split('-')[0]), parseInt(val.split('-')[1]), 0).getDate();
            val = `${val}-${lastDay}`;
        }
    }
    input.value = val;
}

// Attach Search Listeners
['search-in', 'search-mag', 'search-tags', 'search-date-start', 'search-date-end'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
        el.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                if (id.startsWith('search-date')) {
                    formatSearchDate(e.target, id.includes('start') ? 'start' : 'end');
                }
                executeSearch();
            }
        });
    }
});

async function fetchBookmarks() {
    const res = await fetch('/api/bookmarks');
    bookmarksData = await res.json();
    renderBookmarks();
}

function renderBookmarks() {
    const list = document.getElementById('bookmark-list');
    const filter = document.getElementById('bk-filter').value.toLowerCase();
    list.innerHTML = "";
    Object.entries(bookmarksData).forEach(([key, b]) => {
        const prettyName = b.mag.split('/').pop().replace('.pdf','');
        if (filter && !b.tags.toLowerCase().includes(filter) && !prettyName.toLowerCase().includes(filter)) return;
        const div = document.createElement('div');
        div.className = 'result-item';
        div.innerHTML = `<b>${prettyName} - P${b.page}</b><br><small style="color:var(--accent)">${b.tags}</small><span class="del-bk" onclick="deleteBookmark('${key}', event)">🗑️</span>`;
        div.onclick = () => { magSelect.value = b.mag; update(b.page); };
        list.appendChild(div);
    });
}

async function deleteBookmark(key, e) { e.stopPropagation(); await fetch(`/api/bookmarks?key=${encodeURIComponent(key)}`, { method: 'DELETE' }); fetchBookmarks(); }

function getCurrentBookmarkKey() {
    if (!magSelect.value || !pageInput.value) return "";
    return `${magSelect.value}_${pageInput.value}`;
}

function openBookmarkModal() {
    if (!magSelect.value) return;
    const currentKey = getCurrentBookmarkKey();
    const existing = bookmarksData[currentKey];
    const prettyName = pathToLabel[magSelect.value] || magSelect.value.split('/').pop();

    document.getElementById('bookmark-modal-target').innerText = `${prettyName} • Page ${pageInput.value}`;
    document.getElementById('bookmark-tags-input').value = existing?.tags || "";
    document.getElementById('bookmark-modal-overlay').style.display = 'flex';
    setTimeout(() => { document.getElementById('bookmark-tags-input').focus(); }, 0);
}

function closeBookmarkModal(e) {
    if (e && e.target.id !== 'bookmark-modal-overlay' && !e.target.classList.contains('close-modal')) return;
    document.getElementById('bookmark-modal-overlay').style.display = 'none';
}

function handleBookmarkModalKeydown(event) {
    if (event.key !== 'Enter') return;
    event.preventDefault(); applyBookmarkModal();
}

async function applyBookmarkModal() {
    if (!magSelect.value) return;
    const tags = document.getElementById('bookmark-tags-input').value;
    await fetch('/api/bookmarks', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ mag: magSelect.value, page: pageInput.value, tags })
    });
    closeBookmarkModal();
    fetchBookmarks();
}

function toggleBookmark() { openBookmarkModal(); }


// ==========================================
// --- 7. COMMUNITY LIBRARY & MODALS ---
// ==========================================

async function fetchCatalog() {
    const res = await fetch('/api/catalog');
    catalogData = await res.json();
    populateLibraryFilters();
    renderLibrary();
}

function toggleLibrary(forceOpen = false) {
    const overlay = document.getElementById('library-overlay');
    if (forceOpen) overlay.style.display = 'flex';
    else overlay.style.display = overlay.style.display === 'flex' ? 'none' : 'flex';
}

function populateLibraryFilters() {
    let sets = {
        'lib-mag-datalist': new Set(), 'lib-pub-datalist': new Set(),
        'lib-orig-datalist': new Set(), 'lib-trans-datalist': new Set(),
        'lib-media-datalist': new Set(), 'lib-tags-datalist': new Set()
    };
    
    catalogData.forEach(item => {
        if (item.magazine_name) sets['lib-mag-datalist'].add(item.magazine_name);
        if (item.publisher) sets['lib-pub-datalist'].add(item.publisher);
        if (item.original_language) sets['lib-orig-datalist'].add(item.original_language);
        if (item.translated_language) sets['lib-trans-datalist'].add(item.translated_language);
        if (item.media_type) sets['lib-media-datalist'].add(item.media_type);
        if (item.tags) {
            let tList = Array.isArray(item.tags) ? item.tags : item.tags.split(',');
            tList.forEach(t => sets['lib-tags-datalist'].add(t.trim()));
        }
    });
    
    Object.entries(sets).forEach(([id, uniqueSet]) => {
        const dl = document.getElementById(id);
        dl.innerHTML = '';
        Array.from(uniqueSet).sort().forEach(val => { if(val) dl.innerHTML += `<option value="${val}">`; });
    });
}

function syncLibraryMagazineFilterUi() {
    const input = document.getElementById('lib-filter-mag');
    const clearBtn = document.getElementById('lib-filter-mag-clear');
    if (clearBtn) clearBtn.style.display = input.value ? 'block' : 'none';
}

function clearLibraryMagazineFilter() {
    const input = document.getElementById('lib-filter-mag');
    input.value = '';
    syncLibraryMagazineFilterUi();
    filterLibrary();
    input.focus();
}

function filterLibrary() { renderLibrary(); }

function renderLibrary() {
    const grid = document.getElementById('lib-grid');
    const magListContainer = document.getElementById('lib-mag-list');
    grid.innerHTML = ''; magListContainer.innerHTML = '';
    
    const filterMag = document.getElementById('lib-filter-mag').value.toLowerCase();
    const filterPub = document.getElementById('lib-filter-pub').value.toLowerCase();
    const filterOrig = document.getElementById('lib-filter-orig').value.toLowerCase();
    const filterTrans = document.getElementById('lib-filter-trans').value.toLowerCase();
    const filterMedia = document.getElementById('lib-filter-media').value.toLowerCase();
    const filterTags = document.getElementById('lib-filter-tags').value.toLowerCase();
    syncLibraryMagazineFilterUi();
    
    const dateStart = document.getElementById('lib-date-start').value;
    const dateEnd = document.getElementById('lib-date-end').value;
    const adultOnly = document.getElementById('lib-filter-adult').checked;
    const hideInstalled = document.getElementById('lib-filter-installed').checked;
    
    // Mode Toggle: Show list of names by default, show grid if a name is typed/clicked
    if (!filterMag) {
        grid.style.display = 'none';
        magListContainer.style.display = 'flex';
        
        let uniqueMags = new Set();
        catalogData.forEach(item => { if (item.magazine_name) uniqueMags.add(item.magazine_name); });
        
        Array.from(uniqueMags).sort().forEach(magName => {
            const pill = document.createElement('div');
            pill.className = 'mag-list-item';
            pill.innerText = magName;
            pill.onclick = () => {
                document.getElementById('lib-filter-mag').value = magName;
                syncLibraryMagazineFilterUi();
                filterLibrary(); // Instantly switch to grid view
            };
            magListContainer.appendChild(pill);
        });
        return; 
    }

    grid.style.display = 'grid';
    magListContainer.style.display = 'none';
    itemsWithUpdates =[]; 

    function normalizeCatDate(dStr) {
        if (!dStr) return "";
        let clean = dStr.split('/').join('-').replace(/[^\d\-]/g, '');
        let parts = clean.split('-');
        if (parts.length === 3) {
            if (parts[0].length === 4) return `${parts[0]}-${parts[1].padStart(2,'0')}-${parts[2].padStart(2,'0')}`;
            if (parts[2].length === 4) return `${parts[2]}-${parts[0].padStart(2,'0')}-${parts[1].padStart(2,'0')}`;
        } else if (parts.length === 2 && parts[0].length === 4) return `${parts[0]}-${parts[1].padStart(2,'0')}-01`;
        else if (parts.length === 1 && parts[0].length === 4) return `${parts[0]}-01-01`;
        return clean;
    }

    catalogData.forEach(item => {
        const localRelPath = localFiles.find(f => f.endsWith(item.pdf_filename));
        const isDownloaded = !!localRelPath;
        
        let updateAvailable = false;
        if (isDownloaded) {
            const localMeta = metadataCache[localRelPath] || {};
            const localVer = parseFloat(localMeta.version || 0);
            const catVer = parseFloat(item.version || 0);
            if (catVer > localVer) {
                updateAvailable = true;
                itemsWithUpdates.push(item.id);
            }
        }
        if (hideInstalled && isDownloaded) return;
        
        const isAdult = item.adult_content === true || String(item.adult_content).toLowerCase() === "true" || 
                        item.adult === true || String(item.adult).toLowerCase() === "true" ||
                        item.nsfw === true || String(item.nsfw).toLowerCase() === "true" ||
                        item.mature === true || String(item.mature).toLowerCase() === "true";
                        
        if (isAdult && !adultOnly) return;

        let prettyName = item.magazine_name || "Unknown Magazine";
        let pub = item.publisher || "";
        let orig = item.original_language || "";
        let trans = item.translated_language || "";
        let media = item.media_type || "";
        let itemTags = item.tags ? (Array.isArray(item.tags) ? item.tags.join(', ') : item.tags) : "";

        if (filterMag && !prettyName.toLowerCase().includes(filterMag)) return;
        if (filterPub && !pub.toLowerCase().includes(filterPub)) return;
        if (filterOrig && !orig.toLowerCase().includes(filterOrig)) return;
        if (filterTrans && !trans.toLowerCase().includes(filterTrans)) return;
        if (filterMedia && !media.toLowerCase().includes(filterMedia)) return;
        if (filterTags && !itemTags.toLowerCase().includes(filterTags)) return;

        if (dateStart || dateEnd) {
            const normCatDate = normalizeCatDate(item.date);
            if (!normCatDate) return; 
            if (dateStart && normCatDate < dateStart) return;
            if (dateEnd && normCatDate > dateEnd) return;
        }

        let issueLabel = "";
        if(item.date) issueLabel += `${item.date} `;
        if(item.issue_name) issueLabel += `- ${item.issue_name}`;

        let badgeHtml = "";
        if (updateAvailable) badgeHtml = `<div class="badge" style="background:#ff9800; color:#000;">🔄 Update Available</div>`;
        else if (isDownloaded) badgeHtml = `<div class="badge badge-installed">✅ Installed</div>`;
        else badgeHtml = `<div class="badge badge-cloud">☁️ Cloud</div>`;
        
        let origFlag = getFlagEmoji(item.original_language);
        let transFlag = getFlagEmoji(item.translated_language);
        let langDisplay = "";
        if (origFlag && transFlag) langDisplay = `<span class="flag-box">${origFlag} ➔ ${transFlag}</span>`;
        else if (origFlag) langDisplay = `<span class="flag-box">${origFlag}</span>`;

        const coverImg = item.cover_url ? `/api/cover/${encodeURIComponent(item.id)}?v=${encodeURIComponent(item.version || '1.0')}` : 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="%23222"/><text x="50%" y="50%" fill="%23666" font-family="sans-serif" font-size="14" text-anchor="middle">No Cover Art</text></svg>';

        const card = document.createElement('div');
        card.className = 'lib-card';
        card.onclick = () => openModal(item.id, isDownloaded);
        card.innerHTML = `
            ${badgeHtml}
            <img class="lib-cover" src="${coverImg}" loading="lazy">
            <div class="lib-info">
                <div class="lib-title"><span style="overflow:hidden; text-overflow:ellipsis;">${prettyName}</span> ${langDisplay}</div>
                <div class="lib-desc">${issueLabel || 'Unknown Issue'}</div>
            </div>
        `;
        grid.appendChild(card);
    });
    document.getElementById('lib-update-all-btn').style.display = itemsWithUpdates.length > 0 ? 'block' : 'none';
}

function openModal(id, isDownloaded) {
    currentModalItemId = id; 
    const item = catalogData.find(i => i.id === id);
    if(!item) return;

    document.getElementById('modal-cover').src = item.cover_url ? `/api/cover/${encodeURIComponent(item.id)}?v=${encodeURIComponent(item.version || '1.0')}` : 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="%23222"/></svg>';
    let prettyName = item.magazine_name || "Unknown Magazine";
    document.getElementById('modal-title').innerText = prettyName;
    
    // Build Modal Metadata
    let metaHtml = "";
    let origFlag = getFlagEmoji(item.original_language);
    let transFlag = getFlagEmoji(item.translated_language);
    
    if(item.issue_name) metaHtml += `<b>Issue:</b> ${item.issue_name}`;
    if(item.date) metaHtml += `<br><b>Date:</b> ${item.date} &nbsp;|&nbsp; `;
    if(item.version) metaHtml += `<b>Version:</b> ${item.version} &nbsp;|&nbsp; `;
    if (origFlag && transFlag) metaHtml += `<b>Language:</b> ${origFlag} ➔ ${transFlag} &nbsp;|&nbsp; `;
    else if (origFlag) metaHtml += `<b>Language:</b> ${origFlag} &nbsp;|&nbsp; `;
    
    let credits =[];
    if (item.scanner) {
        let s = item.scanner_url ? `<a href="${item.scanner_url}" target="_blank" style="color:var(--accent); text-decoration:none;">${item.scanner}</a>` : item.scanner;
        credits.push(`<b>Scanned by:</b> ${s}`);
    }
    if (item.editor) {
        let e = item.editor_url ? `<a href="${item.editor_url}" target="_blank" style="color:var(--accent); text-decoration:none;">${item.editor}</a>` : item.editor;
        credits.push(`<b>Edited by:</b> ${e}`);
    }
    if (credits.length > 0) metaHtml += `<br>` + credits.join(" &nbsp;|&nbsp; ");
    
    document.getElementById('modal-meta').innerHTML = metaHtml;
    
    let descHtml = item.description || "No description provided.";
    if (item.notes) descHtml += `<br><br><span style="color:#fde68a; font-size:13px;"><b>Notes:</b> ${item.notes}</span>`;

    if (item.adult_content === true) {
        descHtml = `<div style="color: #ff4d4d; font-weight: bold; margin-bottom: 12px; border: 1px solid #ff4d4d; padding: 6px 10px; border-radius: 4px; display: inline-block; background: rgba(255, 77, 77, 0.1);">⚠️ Adult Content 18+ ONLY!!</div><br>` + descHtml;
    }

    document.getElementById('modal-desc').innerHTML = descHtml;

    const actionArea = document.getElementById('modal-action-area');
    
    fetch('/api/downloads').then(r => r.json()).then(states => {
        const state = states[id];
        if (state && !state.done && !state.error) {
            actionArea.innerHTML = `
                <div style="font-size:12px; color:var(--accent); margin-bottom:5px;" id="dl-stat-mod">Downloading...</div>
                <div class="progress-container">
                    <div class="progress-bar" id="dl-bar-mod" style="width:${state.progress}%"></div>
                    <div class="progress-text" id="dl-txt-mod">${state.progress}%</div>
                </div>
            `;
        } else if (itemsWithUpdates.includes(item.id)) {
            actionArea.innerHTML = `
                <div style="display:flex; gap:10px;">
                    <button class="btn-read" style="flex:1;" onclick="readIssue('${item.pdf_filename}')">📖 Read Old</button>
                    <button class="btn-dl" style="background:#ff9800; color:#000; flex:1;" onclick="startDownload('${item.id}', this.parentElement)">🔄 Update Now</button>
                    <button class="btn-dl" style="background:#dc3545; flex:none; width:auto; padding:10px 15px;" onclick="uninstallIssue('${item.pdf_filename}')">🗑️ Uninstall</button>
                </div>
            `;
        } else if (isDownloaded) {
            actionArea.innerHTML = `
                <div style="display:flex; gap:10px;">
                    <button class="btn-read" style="flex:1;" onclick="readIssue('${item.pdf_filename}')">📖 Read Now</button>
                    <button class="btn-dl" style="background:#dc3545; flex:none; width:auto; padding:10px 15px;" onclick="uninstallIssue('${item.pdf_filename}')">🗑️ Uninstall</button>
                </div>
            `;
        } else {
            actionArea.innerHTML = `<button class="btn-dl" onclick="startDownload('${item.id}', this.parentElement)">☁️ Download to Library</button>`;
        }
        document.getElementById('modal-overlay').style.display = 'flex';
    });
}

function closeModal(e) {
    if (e && e.target.id !== 'modal-overlay' && !e.target.classList.contains('close-modal')) return;
    document.getElementById('modal-overlay').style.display = 'none';
}

function openConfirmModal({ title, message, confirmLabel = 'Confirm', tone = 'primary', onConfirm, showCancel = true }) {
    pendingConfirmAction = onConfirm || null;
    document.getElementById('confirm-modal-title').innerText = title || 'Confirm Action';
    document.getElementById('confirm-modal-message').innerText = message || '';

    const confirmBtn = document.getElementById('confirm-modal-confirm');
    confirmBtn.innerText = confirmLabel;
    confirmBtn.className = `modal-btn ${tone === 'danger' ? 'danger' : 'primary'}`;

    const cancelButton = document.querySelector('#confirm-modal-overlay .modal-btn.secondary');
    const xButton = document.querySelector('#confirm-modal-overlay .close-modal');
    
    if (showCancel) {
        cancelButton.style.display = 'inline-block';
        xButton.style.display = 'block';
        document.getElementById('confirm-modal-overlay').onclick = closeConfirmModal;
    } else {
        cancelButton.style.display = 'none';
        xButton.style.display = 'none';
        document.getElementById('confirm-modal-overlay').onclick = null;
    }
    document.getElementById('confirm-modal-overlay').style.display = 'flex';
    setTimeout(() => confirmBtn.focus(), 0);
}

function closeConfirmModal(e) {
    if (e && e.target.id !== 'confirm-modal-overlay' && !e.target.classList.contains('close-modal')) return;
    document.getElementById('confirm-modal-overlay').style.display = 'none';
    pendingConfirmAction = null;
}

async function applyConfirmModal() {
    const action = pendingConfirmAction;
    document.getElementById('confirm-modal-overlay').style.display = 'none';
    pendingConfirmAction = null;
    if (typeof action === 'function') await action();
}

async function uninstallIssue(pdf_filename) {
    openConfirmModal({
        title: 'Uninstall Issue',
        message: 'Are you sure you want to permanently delete this issue from your computer?',
        confirmLabel: 'Uninstall',
        tone: 'danger',
        onConfirm: async () => {
            document.getElementById('modal-action-area').innerHTML = `<div style="color:#ff4d4d; font-weight:bold; text-align:center;">🗑️ Uninstalling...</div>`;
            await fetch('/api/uninstall', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({pdf_filename: pdf_filename})
            });
            closeModal();
            init(); // Refresh grid and dropdowns
        }
    });
}

async function updateAllIssues() {
    openConfirmModal({
        title: 'Update All Issues',
        message: `Update ${itemsWithUpdates.length} issues? Depending on size, this may take a while.`,
        confirmLabel: 'Start Updates',
        onConfirm: async () => {
            document.getElementById('lib-update-all-btn').innerText = "Starting Updates...";
            document.getElementById('lib-update-all-btn').disabled = true;
            for (let id of itemsWithUpdates) {
                completedDownloads.delete(id);
                await fetch(`/api/download`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: id}) });
                await new Promise(r => setTimeout(r, 500)); // Stagger slightly
            }
        }
    });
}

async function startDownload(id, actionAreaElement) {
    completedDownloads.delete(id); 
    actionAreaElement.innerHTML = `
        <div style="font-size:12px; color:var(--accent); margin-bottom:5px;" id="dl-stat-mod">Connecting to Archive...</div>
        <div class="progress-container">
            <div class="progress-bar" id="dl-bar-mod"></div>
            <div class="progress-text" id="dl-txt-mod">0%</div>
        </div>
    `;
    await fetch(`/api/download`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: id})
    });
    renderLibrary(); 
}

// Global Background Progress Poller
setInterval(async () => {
    const overlay = document.getElementById('modal-overlay');
    const libOverlay = document.getElementById('library-overlay');
    if(overlay.style.display !== 'flex' && libOverlay.style.display !== 'flex') return;

    const res = await fetch('/api/downloads');
    const states = await res.json();
    let needsRefresh = false;

    for (const [id, state] of Object.entries(states)) {
        if (state.done && state.progress === 100) {
            if (!completedDownloads.has(id)) {
                completedDownloads.add(id);
                needsRefresh = true;
            }
        }

        if (id === currentModalItemId && overlay.style.display === 'flex') {
            const statEl = document.getElementById('dl-stat-mod');
            const barEl = document.getElementById('dl-bar-mod');
            const txtEl = document.getElementById('dl-txt-mod');
            
            if(statEl && barEl) {
                if (state.error) {
                    statEl.innerText = "Error: " + state.error;
                    statEl.style.color = "#ff4d4d";
                    barEl.style.background = "#ff4d4d";
                } else {
                    statEl.innerText = state.status;
                    barEl.style.width = state.progress + "%";
                    txtEl.innerText = state.progress + "%";
                    
                    if (state.done && state.progress === 100) {
                        const actionArea = document.getElementById('modal-action-area');
                        const item = catalogData.find(i => i.id === id);
                        if(actionArea && item && actionArea.innerHTML.includes('dl-bar-mod')) {
                            actionArea.innerHTML = `
                                <div style="display:flex; gap:10px;">
                                    <button class="btn-read" style="flex:1;" onclick="readIssue('${item.pdf_filename}')">📖 Download Complete - Read Now</button>
                                    <button class="btn-dl" style="background:#dc3545; flex:none; width:auto; padding:10px 15px;" onclick="uninstallIssue('${item.pdf_filename}')">🗑️ Uninstall</button>
                                </div>
                            `;
                        }
                    }
                }
            }
        }
    }
    
    if(needsRefresh) {
        await init(); 
        if (libOverlay.style.display === 'flex') filterLibrary();
    }
}, 1500);

async function readIssue(filename) {
    const actionArea = document.getElementById('modal-action-area');
    actionArea.innerHTML = `<div style="text-align:center; color:var(--accent); font-weight:bold;">🔄 Opening Issue...</div>`;
    await init(); 
    const match = localFiles.find(f => f.endsWith(filename));
    
    if (match) {
        magSelect.value = match;
        pageInput.value = 1;
        document.getElementById('mag-input').value = pathToLabel[match] || filename;
        update();
        closeModal();
        toggleLibrary(false);
        if (document.body.clientWidth < 1000) document.getElementById('sidebar').classList.add('collapsed');
    } else {
        actionArea.innerHTML = `<div style="color:#ff4d4d; font-weight:bold;">Error: Could not find file path. Try refreshing.</div>`;
    }
}


// ==========================================
// --- 8. SPATIAL MAPPING (ZONES & MANGA) ---
// ==========================================

let isMangaMode = false;
function toggleMangaMode() {
    isMangaMode = !isMangaMode;
    document.getElementById('img-container').classList.toggle('manga-mode', isMangaMode);
    document.getElementById('manga-btn').classList.toggle('active', isMangaMode);
}

function toggleZones() { 
    document.getElementById('img-container').classList.toggle('show-all-zones'); 
    document.getElementById('zones-btn').classList.toggle('active');
}

function drawCoordinateBoxes(coordinatesArray) {
    const container = document.getElementById('img-container');
    if (!container) return;

    container.querySelectorAll('.spatial-box').forEach(box => box.remove());

    if (!coordinatesArray || !Array.isArray(coordinatesArray) || coordinatesArray.length === 0) return;

    const padding = 5; 

    coordinatesArray.forEach((item, index) => {
        const boxesList = item.boxes || (item.box ? [item.box] :[]);

        boxesList.forEach((boxCoords, partIndex) => {
            if (!Array.isArray(boxCoords) || boxCoords.length < 4) return;
            if (boxCoords[0] === 0 && boxCoords[1] === 0 && boxCoords[2] === 0 && boxCoords[3] === 0) return;

            const ymin = Math.max(0, boxCoords[0] - padding) / 10;
            const xmin = Math.max(0, boxCoords[1] - padding) / 10;
            const ymax = Math.min(1000, boxCoords[2] + padding) / 10;
            const xmax = Math.min(1000, boxCoords[3] + padding) / 10;

            const boxDiv = document.createElement('div');
            boxDiv.className = 'spatial-box';
            
            const heightPct = ymax - ymin;
            const widthPct = xmax - xmin;
            boxDiv.style.zIndex = Math.floor(45 - ((heightPct * widthPct) / 280));
            
            addResizeHandles(boxDiv);
            
            boxDiv.style.top = `${ymin}%`;
            boxDiv.style.left = `${xmin}%`;
            boxDiv.style.height = `${ymax - ymin}%`;
            boxDiv.style.width = `${xmax - xmin}%`;
            
            boxDiv.dataset.jpText = (item.text || "").trim();
            boxDiv.dataset.index = index; 
            boxDiv.dataset.part = partIndex;

            container.appendChild(boxDiv);
        });
    });
}

function attachTextHoverMagic() {
    const allSpatialBoxes = Array.from(document.querySelectorAll('.spatial-box'));
    if (allSpatialBoxes.length === 0) return;

    const selectors = 'p, h1, h2, h3, h4, h5, h6, ul, ol, table';
    const jpElements = Array.from(document.getElementById('jp-box').querySelectorAll(selectors));
    const enElements = Array.from(document.getElementById('en-box').querySelectorAll(selectors));

    function cleanString(str) {
        if (!str) return "";
        return str.normalize("NFKC").toLowerCase()
                  .replace(/[^a-z0-9\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/g, '');
    }

    const jpTexts = jpElements.map(el => cleanString(el.textContent));

    function getSimilarity(s1, s2) {
        if (!s1 || !s2) return 0;
        if (s1 === s2) return 1.0;
        if (s1.includes(s2) || s2.includes(s1)) {
            const min = Math.min(s1.length, s2.length);
            const max = Math.max(s1.length, s2.length);
            return (min / max) * 0.6 + 0.4; 
        }
        if (s1.length < 2 || s2.length < 2) return 0;

        const getBigrams = (str) => {
            const bg =[];
            for (let i = 0; i < str.length - 1; i++) bg.push(str.substring(i, i + 2));
            return bg;
        };

        const bg1 = getBigrams(s1);
        const bg2 = getBigrams(s2);
        
        let intersection = 0;
        let tempBg2 = [...bg2]; 
        for (let b of bg1) {
            const idx = tempBg2.indexOf(b);
            if (idx > -1) { intersection++; tempBg2.splice(idx, 1); }
        }
        return (2.0 * intersection) / (bg1.length + bg2.length);
    }

    const elementToLogicalIdx = new Array(jpTexts.length).fill(null);
    const logicalIdxToElements = new Map(); 

    const logicalGroups = new Map();
    allSpatialBoxes.forEach(box => {
        const idx = box.dataset.index;
        if (!logicalGroups.has(idx)) logicalGroups.set(idx,[]);
        logicalGroups.get(idx).push(box);
    });

    jpTexts.forEach((jpText, elIdx) => {
        if (!jpText) return;

        let bestLogicalIdx = null, bestSim = 0, bestLenDiff = Infinity, bestIdxDiff = Infinity;

        logicalGroups.forEach((boxes, logicalIdx) => {
            const boxText = cleanString(boxes[0].dataset.jpText);
            if (!boxText) return;

            const sim = getSimilarity(jpText, boxText);
            const lenDiff = Math.abs(jpText.length - boxText.length);
            const idxDiff = Math.abs((elIdx / jpTexts.length) - (parseInt(logicalIdx) / logicalGroups.size));
            
            let isBetter = false;
            if (sim > bestSim) { isBetter = true; } 
            else if (sim === bestSim) {
                if (lenDiff < bestLenDiff) { isBetter = true; } 
                else if (lenDiff === bestLenDiff && idxDiff < bestIdxDiff) { isBetter = true; }
            }

            if (isBetter) { bestSim = sim; bestLenDiff = lenDiff; bestIdxDiff = idxDiff; bestLogicalIdx = logicalIdx; }
        });

        if (bestLogicalIdx !== null && bestSim > 0.15) { 
            elementToLogicalIdx[elIdx] = bestLogicalIdx;
            if (!logicalIdxToElements.has(bestLogicalIdx)) logicalIdxToElements.set(bestLogicalIdx,[]);
            logicalIdxToElements.get(bestLogicalIdx).push(elIdx);
        }
    });

    const toggleBoxHighlight = (logicalIdx, forceState) => {
        const boxes = logicalGroups.get(logicalIdx);
        if (boxes) boxes.forEach(b => forceState ? b.classList.add('active-highlight') : b.classList.remove('active-highlight'));
    };

    jpElements.forEach((el, index) => {
        el.addEventListener('mouseenter', () => toggleBoxHighlight(elementToLogicalIdx[index], true));
        el.addEventListener('mouseleave', () => toggleBoxHighlight(elementToLogicalIdx[index], false));
    });

    enElements.forEach((el, index) => {
        el.addEventListener('mouseenter', () => {
            if (jpElements[index]) jpElements[index].style.backgroundColor = "rgba(138, 180, 248, 0.15)";
            toggleBoxHighlight(elementToLogicalIdx[index], true);
        });
        el.addEventListener('mouseleave', () => {
            if (jpElements[index]) jpElements[index].style.backgroundColor = "";
            toggleBoxHighlight(elementToLogicalIdx[index], false);
        });
    });

    logicalGroups.forEach((boxes, logicalIdx) => {
        const mappedElements = logicalIdxToElements.get(logicalIdx) ||[];
        if (mappedElements.length === 0) return;

        boxes.forEach(box => {
            box.addEventListener('mouseenter', () => {
                toggleBoxHighlight(logicalIdx, true);
                mappedElements.forEach(elIdx => {
                    if (jpElements[elIdx]) jpElements[elIdx].style.backgroundColor = "rgba(0, 212, 255, 0.2)";
                    if (enElements[elIdx]) enElements[elIdx].style.backgroundColor = "rgba(0, 212, 255, 0.2)";
                });
            });

            box.addEventListener('mouseleave', () => {
                toggleBoxHighlight(logicalIdx, false);
                mappedElements.forEach(elIdx => {
                    if (jpElements[elIdx]) jpElements[elIdx].style.backgroundColor = "";
                    if (enElements[elIdx]) enElements[elIdx].style.backgroundColor = "";
                });
            });

            box.addEventListener('click', (e) => {
                e.preventDefault(); 
                if (mappedElements.length === 0) return;

                let nextLang = box.dataset.scrollTarget === 'en' ? 'jp' : 'en';
                boxes.forEach(b => b.dataset.scrollTarget = nextLang); 

                let targetEl = nextLang === 'en' ? enElements[mappedElements[0]] : jpElements[mappedElements[0]];
                if (!targetEl) targetEl = enElements[mappedElements[0]] || jpElements[mappedElements[0]];

                if (targetEl) {
                    targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    mappedElements.forEach(elIdx => {
                        if (jpElements[elIdx]) {
                            jpElements[elIdx].style.transition = "background-color 0.3s";
                            jpElements[elIdx].style.backgroundColor = nextLang === 'jp' ? "rgba(0, 212, 255, 0.6)" : "rgba(0, 212, 255, 0.2)";
                            setTimeout(() => jpElements[elIdx].style.backgroundColor = "", 1200);
                        }
                        if (enElements[elIdx]) {
                            enElements[elIdx].style.transition = "background-color 0.3s";
                            enElements[elIdx].style.backgroundColor = nextLang === 'en' ? "rgba(0, 212, 255, 0.6)" : "rgba(0, 212, 255, 0.2)";
                            setTimeout(() => enElements[elIdx].style.backgroundColor = "", 1200);
                        }
                    });
                }
            });
        });
    });

    window.currentBoxAssignments = new Map();
    allSpatialBoxes.forEach(box => {
        const mappedElements = logicalIdxToElements.get(box.dataset.index) ||[];
        if (mappedElements.length > 0) window.currentBoxAssignments.set(box, mappedElements[0]);
    });
    window.currentJpElements = jpElements;
    window.currentEnElements = enElements;

    // Inject text for Manga Mode
    logicalGroups.forEach((boxes, logicalIdx) => {
        const mappedElements = logicalIdxToElements.get(logicalIdx) ||[];
        if (mappedElements.length > 0) {
            const span = document.createElement('span');
            span.className = 'manga-text';
            span.innerText = mappedElements
                .map(idx => enElements[idx] ? enElements[idx].innerText : '')
                .filter(Boolean).join('\n\n');
                
            const primaryBox = boxes.find(b => b.dataset.part === "0") || boxes[0];
            primaryBox.appendChild(span);
        }
    });
}


// ==========================================
// --- 9. ZONE EDITOR ENGINE ---
// ==========================================

let isZoneEditMode = false;
let selectedZone = null;
let isResizing = false, isDragging = false;
let startX, startY, startW, startH, startT, startL;
let resizeDir = ''; 

function addResizeHandles(box) {
    ['nw', 'ne', 'sw', 'se'].forEach(dir => {
        const h = document.createElement('div');
        h.className = `resize-handle ${dir}`;
        h.dataset.dir = dir;
        box.appendChild(h);
    });
}

const zonePanel = document.getElementById('zone-editor-panel');
const zoneHandle = document.getElementById('zone-editor-handle');
let isDraggingPanel = false, panelStartX, panelStartY, panelStartLeft, panelStartTop;

function selectZone(box) {
    if (selectedZone) selectedZone.classList.remove('selected-zone');
    selectedZone = box;
    if (selectedZone) {
        selectedZone.classList.add('selected-zone');
        document.getElementById('zone-selected-tools').style.display = 'block';
        document.getElementById('zone-unselected').style.display = 'none';
        document.getElementById('zone-text-input').value = selectedZone.dataset.jpText || "";
    } else {
        document.getElementById('zone-selected-tools').style.display = 'none';
        document.getElementById('zone-unselected').style.display = 'block';
    }
}

function updateSelectedZoneText() {
    if (selectedZone) selectedZone.dataset.jpText = document.getElementById('zone-text-input').value;
}

function deleteSelectedZone() {
    if (selectedZone) { selectedZone.remove(); selectZone(null); }
}

function addNewZone() {
    const boxDiv = document.createElement('div');
    boxDiv.className = 'spatial-box selected-zone';
    boxDiv.style.top = '40%'; boxDiv.style.left = '40%';
    boxDiv.style.height = '10%'; boxDiv.style.width = '20%';
    boxDiv.dataset.jpText = "";
    addResizeHandles(boxDiv);
    document.getElementById('img-container').appendChild(boxDiv);
    selectZone(boxDiv);
}

function splitSelectedZone() {
    if (!selectedZone) return;
    const boxDiv = document.createElement('div');
    boxDiv.className = 'spatial-box selected-zone';
    const currentTop = parseFloat(selectedZone.style.top);
    const currentLeft = parseFloat(selectedZone.style.left);
    boxDiv.style.top = (currentTop + 5) + '%'; 
    boxDiv.style.left = (currentLeft + 5) + '%';
    boxDiv.style.height = selectedZone.style.height; 
    boxDiv.style.width = selectedZone.style.width;
    boxDiv.dataset.jpText = selectedZone.dataset.jpText;
    if (selectedZone.dataset.index) boxDiv.dataset.index = selectedZone.dataset.index;
    addResizeHandles(boxDiv);
    document.getElementById('img-container').appendChild(boxDiv);
    selectZone(boxDiv);
}

if (zoneHandle) {
    zoneHandle.addEventListener('mousedown', (e) => {
        isDraggingPanel = true;
        panelStartX = e.clientX; panelStartY = e.clientY;
        panelStartLeft = zonePanel.offsetLeft; panelStartTop = zonePanel.offsetTop;
        e.preventDefault();
    });
}

const imgCont = document.getElementById('img-container');
imgCont.addEventListener('mousedown', (e) => {
    if (!isZoneEditMode) return;
    if (e.target.closest('#zone-editor-panel')) return; 

    if (e.target.classList.contains('resize-handle')) {
        isResizing = true;
        resizeDir = e.target.dataset.dir; 
        selectZone(e.target.parentElement);
        startW = parseFloat(selectedZone.style.width);
        startH = parseFloat(selectedZone.style.height);
        startT = parseFloat(selectedZone.style.top);
        startL = parseFloat(selectedZone.style.left);
        startX = e.clientX; startY = e.clientY;
        e.stopPropagation();
    } else if (e.target.classList.contains('spatial-box')) {
        isDragging = true;
        selectZone(e.target);
        startT = parseFloat(selectedZone.style.top);
        startL = parseFloat(selectedZone.style.left);
        startX = e.clientX; startY = e.clientY;
        e.stopPropagation();
    } else {
        selectZone(null); 
    }
});

window.addEventListener('mousemove', (e) => {
    if (isDraggingPanel) {
        zonePanel.style.left = (panelStartLeft + e.clientX - panelStartX) + 'px';
        zonePanel.style.top = (panelStartTop + e.clientY - panelStartY) + 'px';
        return;
    }
    if (!isZoneEditMode || !selectedZone || (!isResizing && !isDragging)) return;
    
    const rect = imgCont.getBoundingClientRect();
    const dx = ((e.clientX - startX) / rect.width) * 100;
    const dy = ((e.clientY - startY) / rect.height) * 100;
    
    if (isResizing) {
        let newW = startW, newH = startH, newL = startL, newT = startT;
        if (resizeDir.includes('e')) newW = startW + dx;
        if (resizeDir.includes('s')) newH = startH + dy;
        if (resizeDir.includes('w')) { newL = startL + dx; newW = startW - dx; }
        if (resizeDir.includes('n')) { newT = startT + dy; newH = startH - dy; }
        if (newW > 1) {
            selectedZone.style.width = newW + '%';
            if (resizeDir.includes('w')) selectedZone.style.left = newL + '%';
        }
        if (newH > 1) {
            selectedZone.style.height = newH + '%';
            if (resizeDir.includes('n')) selectedZone.style.top = newT + '%';
        }
    } else if (isDragging) {
        selectedZone.style.left = (startL + dx) + '%';
        selectedZone.style.top = (startT + dy) + '%';
    }
});

window.addEventListener('mouseup', () => { 
    isDraggingPanel = false; isResizing = false; isDragging = false; resizeDir = '';
    if (typeof zoneHandle !== 'undefined' && zoneHandle) zoneHandle.style.cursor = 'grab';
});


// ==========================================
// --- 10. DRAG & DROP TOOLBAR LOGIC ---
// ==========================================

function initToolbarDragAndDrop() {
    const controls = document.querySelector('.controls');
    
    // Load saved layout
    const savedOrder = JSON.parse(localStorage.getItem('toolbarOrder'));
    if (savedOrder) {
        savedOrder.forEach(id => {
            const group = document.querySelector(`.controls-group[data-id="${id}"]`);
            if (group) controls.appendChild(group);
        });
        reorderDividers();
    }

    const groups = document.querySelectorAll('.controls-group');
    groups.forEach(group => {
        const handle = group.querySelector('.drag-handle');
        if (handle) {
            handle.addEventListener('mousedown', () => group.setAttribute('draggable', 'true'));
            handle.addEventListener('mouseup', () => group.removeAttribute('draggable'));
            handle.addEventListener('mouseleave', () => group.removeAttribute('draggable'));
        }

        group.addEventListener('dragstart', (e) => {
            group.classList.add('dragging');
            e.dataTransfer.setData('text/plain', ''); 
            e.dataTransfer.effectAllowed = 'move';
        });

        group.addEventListener('dragend', () => {
            group.classList.remove('dragging');
            group.removeAttribute('draggable');
            saveToolbarOrder(); 
        });
    });

    controls.addEventListener('dragover', (e) => {
        e.preventDefault();
        const dragging = document.querySelector('.dragging');
        if (!dragging) return;

        const elements =[...controls.querySelectorAll('.controls-group:not(.dragging)')];
        const hoveredElement = elements.find(child => {
            const box = child.getBoundingClientRect();
            return e.clientX >= box.left && e.clientX <= box.right &&
                   e.clientY >= box.top && e.clientY <= box.bottom;
        });

        if (hoveredElement) {
            const box = hoveredElement.getBoundingClientRect();
            const isLeftHalf = e.clientX < box.left + box.width / 2;
            if (isLeftHalf) controls.insertBefore(dragging, hoveredElement);
            else controls.insertBefore(dragging, hoveredElement.nextSibling);
            reorderDividers();
        }
    });
}

function reorderDividers() {
    const controls = document.querySelector('.controls');
    const oldDividers = controls.querySelectorAll(':scope > .divider');
    oldDividers.forEach(d => d.remove());

    const groups =[...controls.querySelectorAll('.controls-group')];
    groups.forEach((group, index) => {
        if (index < groups.length - 1) {
            const divider = document.createElement('div');
            divider.className = 'divider';
            controls.insertBefore(divider, group.nextSibling);
        }
    });
}

function saveToolbarOrder() {
    const order = [...document.querySelectorAll('.controls-group')].map(g => g.dataset.id);
    localStorage.setItem('toolbarOrder', JSON.stringify(order));
}


// ==========================================
// --- 11. UTILS, SHORTCUTS & EVENT LISTENERS ---
// ==========================================

function getFlagEmoji(langCode) {
    if (!langCode) return "";
    const flags = { 'JP': '🇯🇵', 'EN': '🇺🇸', 'UK': '🇬🇧', 'FR': '🇫🇷', 'DE': '🇩🇪', 'ES': '🇪🇸', 'IT': '🇮🇹', 'KR': '🇰🇷', 'CN': '🇨🇳' };
    return flags[langCode.toUpperCase()] || langCode.toUpperCase();
}

function showTab(tab) {
    document.getElementById('tab-search').classList.toggle('active', tab === 'search');
    document.getElementById('tab-bookmarks').classList.toggle('active', tab === 'bookmarks');
    document.getElementById('panel-search').style.display = tab === 'search' ? 'block' : 'none';
    document.getElementById('panel-bookmarks').style.display = tab === 'bookmarks' ? 'block' : 'none';
}

function toggleTheme() {
    document.body.classList.toggle('light-mode');
    syncContentEditorTheme();
}
function toggleSidebar() { document.getElementById('sidebar').classList.toggle('collapsed'); }
function toggleSec(id, show) { document.getElementById(id).style.display = show ? 'block' : 'none'; }
function updateFont(v) { document.documentElement.style.setProperty('--font-size', v + 'px'); }

// Help Modal Content
const HELP_MARKDOWN = `
## Rosetta Magazine Researcher
**Author:** Dustin Hubbard (Hubz) <https://www.gamingalexandria.com>

## 🍺 Support the Project

If this tool saved you some time and you'd like to support the project, feel free to buy me a beer! 

[![Buy me a beer](https://img.shields.io/badge/Buy_Me_A_Beer-f39c12?style=for-the-badge&logo=unapp&logoColor=white)](https://paypal.me/dustinhubbard1)

**Other ways to support:**
* **Venmo:** [@Dustin-Hubbard-26](https://venmo.com/Dustin-Hubbard-26)
* **PayPal:** [Donate via PayPal](https://paypal.me/dustinhubbard1)

Welcome to the Rosetta Magazine Researcher! A fully offline-capable archive viewer with smart search, text formatting, and community catalogs to download magazines along with translations and transcriptions. Designed to seamlessly read scanned PDFs alongside transcribed text, translations, and rich metadata. 

## Notice on AI Translations
Transcriptions & translations in this program are generated by various Artificial Intelligence models. AIs can and do "hallucinate," miss cultural nuances, mistranslate, or transcribe incorrectly. Please do not rely on this for 100% academic or historical accuracy. Its main goal is to provide assistance in the research process, with the intent that specific excerpts should be validated by professional translators.
- **Contribute:** If you spot errors, use the **✏️ Edit** button. If you fix an entire issue, consider sharing your corrections with the Discord community.

---

## 🚀 Getting Started
Use the **Library** to download new issues, or the **Search** tab to find specific content inside your downloaded magazines.

**To safely close the application**, simply close your browser tab! The background server will automatically shut down after 20 seconds to save memory.

---

## 🎮 Viewer Controls & Interactive Reading
Rosetta features a fully interactive spatial mapping engine. If an issue includes a coordinates JSON file, the text in the sidebar is linked directly to the scan.

- **Interactive Hovering:** Move your mouse over a translation in the sidebar to see the exact transcribed text light up in cyan on the scan. 
- **Reverse Lookup (Click-to-Scroll):** Click any cyan box on the magazine image to automatically scroll the sidebar to the translation. Clicking again toggles between the translation and transcription.
- **💬 Manga Mode:** Paints over the original transcribed text and injects translations directly onto the page for a professional "scanlation" experience.
- **👁️ Zones Toggle:** See a "heat map" of exactly where the AI detected text on the page.
- **Native Zoom & Pan:** Use the **🔍+** and **🔍-** buttons, or hold **Ctrl + Mouse Wheel** to zoom.
- **Formatting:** Click the **MD** button to toggle between formatted markdown and raw text.
- **Font Size & Theme:** Use the slider at the bottom to adjust text size, and the ☀️ button to switch between Dark and Light mode.
- **Bookmarks:** Click the ⭐ button to save your current page. You can add custom tags to your bookmarks to easily filter them in the Bookmarks sidebar tab!
- **Customizable Toolbar:** Grab the dotted handle (**⋮⋮**) on any section of the bottom toolbar to drag and reorder the controls to your liking. Your custom layout is saved automatically!

---

### 🔍 Search & Library Tips
- **Advanced Search:** Use quotes for exact phrases (\`"action packed"\`), a minus sign to exclude words (\`-boring\`), or asterisks for wildcards (\`translat*\`).
- **Filter by Section:** Use the checkboxes in the sidebar to search *only* the summaries or *only* the translations.
- **Spatial Highlights:** When you click a search result, the app auto-scrolls the PDF to the exact location of the word and flashes a **red box** over the text.
- **Library Management:** You can **Uninstall** downloaded issues to save hard drive space, or use the **🔄 Update All** button to batch download updates for all your installed magazines at once.
- **Adult Content:** In the library, magazines tagged as 18+/NSFW are hidden by default. Check the "18+ Content" box to include mature content in your library view.

### Advanced Date Searching
The Search tab has a very smart date filter. You don't need exact days!
- Type \`1999\` to search the entire year.
- Type \`1999/10\` or \`10-1999\` to search a specific month.
- Type \`10-31-99\` or \`1999-10-31\` for a specific day.

---

### ⌨️ Keyboard Shortcuts
- **Left / Right Arrows:** Previous / Next Page.
- **Page Up / Down:** Scroll the translation/transcription boxes.
- **Ctrl + (Plus/Minus):** Zoom In / Out.
- **Ctrl + 0:** Reset zoom to 100%.
- **Escape:** Close any open editor or modal window.

---

## 🛠️ Editing
Click the **✏️ Edit** button in the toolbar to begin improving a page. If you prefer to edit files manually on your computer (instead of using the Editor), navigate to the **Magazines** subfolder and edit the txt file of the magazine with your text editor of choice.

### 📝 Content Editing
- **Sections:** Each page has **Transcription** (Original), **Translation**, and **Summary** you can quickly edit.
- **Visual Editor:** Click the **Open Editor** button on any section for a full-screen Markdown experience with a live preview side-by-side of that section.
- **Markdown:** Supports tables, bolding, and lists. Ensure the **MD** button is enabled (Default behavior) to see the final formatted result.
- **Magazine Metadata:** Click **Edit Metadata** at the bottom of the content area to update searchable fields (Publisher, Date, Tags) or add Scanner/Editor credits and URLs.

---

### ⚙️ Magazine Metadata
Metadata applies to the *entire* magazine. It's main use case is to improve searchability as things in it such as publisher, date, and subject tags are used by the search engine. The file is optional, if it doesn't exist, the app will simply use the magazine's folder name as its title and leave all other metadata fields blank.

Schema (metadata.txt)
This file sits on your local hard drive next to the PDF (or inside its \`.zip\`). All the fields are optional.
\`\`\`text
Magazine Name: Game Magazine
Publisher: Nintendo
Date: 1992-10-01
Issue Name: Volume 1
Region: Japan
Translation: English
Version: 1.1
Tags: action, nes, mario
Scanner: John Doe
Scanner URL: www.whatever.com
Editor: Billy Bob
Editor URL: www.whatever.com
Notes: Missing pages 12-14.
\`\`\`

---

### 🎨 Spatial Box Manipulation
Transcriptions/Translations downloaded from the Library often come with a coordinates JSON file that maps the exact location of each line of text on the original magazine scan. If you find any misalignments, you can edit these boxes directly in the app!

- **Resize/Move:**Click a box to select it, then drag the edges to resize or move it.
- **Split Box:** Creates a new box directly below the current one. This is useful when a text string or block is spread across multiple areas on the page. Simply draw the new box over the next area of text and both boxes will highlight when clicked or hovered over in the viewer.
- **Delete Box:** Click an existing box and then click the delete button.
- **Add Box:** Click the Add New Box button and copy/paste the proper transcription text into it in order to link it.

---

### 📁 Adding Local Magazines
You do not have to use the Cloud Library! You can easily add your own personal PDFs to the viewer.
The app reads magazines from its **\`Magazines\`** subfolder.

1. Open the **Magazines** subfolder located in the application folder.
2. Create a new folder for your magazine (e.g., My Custom Mag).
3. Drop your .pdf file inside that new folder.
4. Drop the .txt or .zip file containing the transcription and/or a metadata.txt into that same folder! (As long as it's the only ZIP in the folder, or shares the exact same name as the PDF, the app will automatically link them).
5. Restart the app (or just refresh the page), and it will automatically appear in your Search dropdown!

---

### 📚 The Library Catalog (catalog.json)
While \`metadata.txt\` handles your local files, the Library tab populates its list of downloadable magazines using a master \`catalog.json\` file. 

**Official Automatic Updates:**
When you open the Library, the app automatically fetches the latest official catalog from the web. If an official magazine receives a new translation or fix, the app compares the cloud version to your local file and displays an **🔄 Update Available** badge! If you are offline, it safely falls back to reading your local \`catalog.json\` file.

**Adding Custom Catalogs:**
You can also add third-party magazine lists created by the community! 
1. Create a folder named **\`Catalogs\`** in the same folder as this application.
2. Place any community \`.json\` catalog files inside it. 
3. The app will automatically merge them into your Library!

---

## ⚙️ Configuration (config.yaml)
You can customize the app by placing a config.yaml file next to the application (or executable). If the file is missing, sensible defaults are used. All paths are relative to the app root.

---

### License (AGPLv3)
Copyright (c) 2026 Gaming Alexandria LLC.
This program is free software: you can redistribute it and/or modify it under the terms of the **GNU Affero General Public License** as published by the Free Software Foundation.
`;

function toggleHelp(forceOpen = false) {
    const overlay = document.getElementById('help-overlay');
    if (overlay.style.display === 'flex' && !forceOpen) {
        closeHelp();
    } else {
        document.getElementById('help-content').innerHTML = marked.parse(HELP_MARKDOWN);
        overlay.style.display = 'flex';
    }
}

function closeHelp(e) {
    if (e && e.target.id !== 'help-overlay' && e.target.innerText !== '×') return;
    document.getElementById('help-overlay').style.display = 'none';
}

// Keyboard Shortcuts Listener
document.addEventListener('keydown', (e) => {
    const activeEl = document.activeElement;
    const activeTag = activeEl ? activeEl.tagName.toLowerCase() : '';
    if (
        activeTag === 'input' || activeTag === 'textarea' || activeTag === 'select' ||
        (activeEl && activeEl.isContentEditable) ||
        (activeEl && activeEl.closest && activeEl.closest('.toastui-editor-defaultUI'))
    ) return;
    
    const lib = document.getElementById('library-overlay');
    const mod = document.getElementById('modal-overlay');
    const help = document.getElementById('help-overlay');
    const editorModal = document.getElementById('editor-modal-overlay');
    const metadataModal = document.getElementById('metadata-modal-overlay');
    
    if (
        (lib && lib.style.display === 'flex') || (mod && mod.style.display === 'flex') ||
        (help && help.style.display === 'flex') || (editorModal && editorModal.style.display === 'flex') ||
        (metadataModal && metadataModal.style.display === 'flex')
    ) {
        if (e.key === 'Escape') { closeContentEditor(); closeMetadataEditor(); }
        return;
    }

    if (e.ctrlKey || e.metaKey) {
        if (e.key === '=' || e.key === '+') { e.preventDefault(); adjustImgZoom(15); return; }
        if (e.key === '-') { e.preventDefault(); adjustImgZoom(-15); return; }
        if (e.key === '0') { e.preventDefault(); currentImgZoom = 100; adjustImgZoom(0); return; }
    }

    if (e.key === 'ArrowLeft') { e.preventDefault(); changePage(-1); } 
    else if (e.key === 'ArrowRight') { e.preventDefault(); changePage(1); } 
    else if (e.key === 'PageUp') { 
        e.preventDefault(); 
        const mid = document.getElementById('middle');
        mid.scrollBy({ top: -(mid.clientHeight * 0.8), behavior: 'smooth' });
    } else if (e.key === 'PageDown') { 
        e.preventDefault(); 
        const mid = document.getElementById('middle');
        mid.scrollBy({ top: (mid.clientHeight * 0.8), behavior: 'smooth' });
    }
});

// Select Dropdown listeners
document.getElementById('mag-input').addEventListener('input', (e) => {
    const path = labelToPath[e.target.value];
    if (path && path !== magSelect.value) {
        magSelect.value = path;
        pageInput.value = 1; 
        update();
        e.target.blur(); 
    }
});

document.getElementById('mag-input').addEventListener('change', (e) => {
    const path = labelToPath[e.target.value];
    if (path) {
        if (path !== magSelect.value) { magSelect.value = path; pageInput.value = 1; update(); }
    } else {
        e.target.value = pathToLabel[magSelect.value] || "";
    }
});

pageInput.onchange = () => { update(); pageInput.blur(); };

// Server Ping
setInterval(() => { fetch('/api/ping').catch(() => {}); }, 5000);

let disclaimerDisplayed = false;
function showAIDisclaimer() {
    if (localStorage.getItem('seenAIDisclaimer') || disclaimerDisplayed) return;

    disclaimerDisplayed = true;
    openConfirmModal({
        title: 'AI Notice',
        message: 'Transcriptions & translations in this program are generated by various Artificial Intelligence models. AIs can and do "hallucinate," miss cultural nuances, mistranslate, or transcribe incorrectly. Please do not rely on this for 100% academic or historical accuracy. Its main goal is to provide assistance in the research process, with the intent that specific excerpts should be validated by professional translators.',
        confirmLabel: 'I Understand',
        showCancel: false,
        onConfirm: () => { localStorage.setItem('seenAIDisclaimer', 'true'); }
    });
}

// ==========================================
// --- RUN AT STARTUP ---
// ==========================================
initToolbarDragAndDrop();
init(true);
showAIDisclaimer();