// LawGraph Document Annotator - Client Logic with Section Parsing & PDF/HTML Text Fetching

const state = {
    sources: [],
    currentSource: "Catalonia/data/cido_subset_100.json",
    documents: [],
    currentIndex: 0,
    annotations: [],
    settings: {
        target_property: "urlPdf",
        csv_path: "annotations.csv",
        key_presets: ["law_id", "article", "entity", "jurisdiction", "date", "status", "penalty"],
        categories: [
            { name: "Legal Reference", color: "#3b82f6" },
            { name: "Entity / Institution", color: "#10b981" },
            { name: "Obligation / Rule", color: "#f59e0b" },
            { name: "Date / Procedure", color: "#8b5cf6" },
            { name: "Penalty / Sanction", color: "#ef4444" },
            { name: "General Note", color: "#6b7280" }
        ]
    },
    activeTabProperty: "urlPdf",
    activeSelection: null,
    editingAnnotationId: null,
    categoryFilter: "ALL",
    docListFilter: "all",
    docListSearchQuery: ""
};

// DOM Elements
const elements = {
    sourceSelect: document.getElementById("sourceSelect"),
    btnCustomSource: document.getElementById("btnCustomSource"),
    btnPrevDoc: document.getElementById("btnPrevDoc"),
    btnNextDoc: document.getElementById("btnNextDoc"),
    currentDocIndex: document.getElementById("currentDocIndex"),
    totalDocsCount: document.getElementById("totalDocsCount"),
    btnOpenDocList: document.getElementById("btnOpenDocList"),
    btnOpenSettings: document.getElementById("btnOpenSettings"),
    btnExportCsv: document.getElementById("btnExportCsv"),
    btnToggleTheme: document.getElementById("btnToggleTheme"),
    
    // Sidebar
    docStatusBadge: document.getElementById("docStatusBadge"),
    docMetadataCard: document.getElementById("docMetadataCard"),
    activePropSelect: document.getElementById("activePropSelect"),
    statDocAnnotations: document.getElementById("statDocAnnotations"),
    statDocTables: document.getElementById("statDocTables"),
    statTotalGlobalAnns: document.getElementById("statTotalGlobalAnns"),
    
    // Viewer
    docMainTitle: document.getElementById("docMainTitle"),
    docIdBadge: document.getElementById("docIdBadge"),
    propertyTabs: document.getElementById("propertyTabs"),
    textSearchInput: document.getElementById("textSearchInput"),
    searchResultsCount: document.getElementById("searchResultsCount"),
    docTextContainer: document.getElementById("docTextContainer"),
    selectionPopover: document.getElementById("selectionPopover"),
    btnAnnotateSelection: document.getElementById("btnAnnotateSelection"),
    
    // Right Sidebar
    annSidebarCount: document.getElementById("annSidebarCount"),
    categoryFilterBar: document.getElementById("categoryFilterBar"),
    annotationsList: document.getElementById("annotationsList"),
    
    // Annotation Modal
    annotationModal: document.getElementById("annotationModal"),
    annModalTitle: document.getElementById("annModalTitle"),
    btnCloseAnnModal: document.getElementById("btnCloseAnnModal"),
    modalSelectedText: document.getElementById("modalSelectedText"),
    modalTargetProp: document.getElementById("modalTargetProp"),
    modalSectionTitle: document.getElementById("modalSectionTitle"),
    modalOffsets: document.getElementById("modalOffsets"),
    annCategory: document.getElementById("annCategory"),
    annComment: document.getElementById("annComment"),
    kvPresetsChips: document.getElementById("kvPresetsChips"),
    kvTableBody: document.getElementById("kvTableBody"),
    btnAddKvRow: document.getElementById("btnAddKvRow"),
    btnCancelAnn: document.getElementById("btnCancelAnn"),
    btnSaveAnn: document.getElementById("btnSaveAnn"),
    
    // Doc List Modal
    docListModal: document.getElementById("docListModal"),
    btnCloseDocListModal: document.getElementById("btnCloseDocListModal"),
    btnCloseDocListModalFooter: document.getElementById("btnCloseDocListModalFooter"),
    docListSearch: document.getElementById("docListSearch"),
    docListGrid: document.getElementById("docListGrid"),
    countAllDocs: document.getElementById("countAllDocs"),
    countAnnotatedDocs: document.getElementById("countAnnotatedDocs"),
    countUnannotatedDocs: document.getElementById("countUnannotatedDocs"),
    docListStatusSummary: document.getElementById("docListStatusSummary"),
    
    // Settings Modal
    settingsModal: document.getElementById("settingsModal"),
    btnCloseSettingsModal: document.getElementById("btnCloseSettingsModal"),
    btnCancelSettings: document.getElementById("btnCancelSettings"),
    btnSaveSettings: document.getElementById("btnSaveSettings"),
    settingTargetProp: document.getElementById("settingTargetProp"),
    settingCsvPath: document.getElementById("settingCsvPath"),
    presetKeysTagContainer: document.getElementById("presetKeysTagContainer"),
    inputNewPresetKey: document.getElementById("inputNewPresetKey"),
    btnAddPresetKey: document.getElementById("btnAddPresetKey"),
    categoriesEditorList: document.getElementById("categoriesEditorList"),
    
    // Custom Source Modal
    customSourceModal: document.getElementById("customSourceModal"),
    btnCloseCustomSourceModal: document.getElementById("btnCloseCustomSourceModal"),
    btnCancelCustomSource: document.getElementById("btnCancelCustomSource"),
    btnLoadCustomSource: document.getElementById("btnLoadCustomSource"),
    inputCustomSourcePath: document.getElementById("inputCustomSourcePath")
};

// Initialize Application
async function init() {
    setupEventListeners();
    await fetchSettings();
    await fetchSources();
    await fetchAnnotations();
    await loadSource(state.currentSource);
}

// Fetch Settings
async function fetchSettings() {
    try {
        const res = await fetch("/api/settings");
        if (res.ok) {
            state.settings = await res.json();
            if (state.settings.active_source) {
                state.currentSource = state.settings.active_source;
            }
            if (state.settings.target_property) {
                state.activeTabProperty = state.settings.target_property;
                elements.activePropSelect.value = state.settings.target_property;
            }
        }
    } catch (e) {
        console.error("Error fetching settings:", e);
    }
}

// Fetch Sources
async function fetchSources() {
    try {
        const res = await fetch("/api/sources");
        if (res.ok) {
            state.sources = await res.json();
            elements.sourceSelect.innerHTML = "";
            state.sources.forEach(src => {
                const opt = document.createElement("option");
                opt.value = src.path;
                opt.textContent = `${src.name} (${src.type === "directory" ? "Directory" : src.size_mb + " MB"})`;
                if (src.path === state.currentSource) opt.selected = true;
                elements.sourceSelect.appendChild(opt);
            });
        }
    } catch (e) {
        console.error("Error fetching sources:", e);
    }
}

// Fetch CSV Processed Annotations
async function fetchAnnotations() {
    try {
        const csvPath = state.settings.csv_path || "annotations.csv";
        const res = await fetch(`/api/annotations?csv_path=${encodeURIComponent(csvPath)}`);
        if (res.ok) {
            state.annotations = await res.json();
            updateGlobalStats();
        }
    } catch (e) {
        console.error("Error fetching annotations:", e);
    }
}

// Load Document Source
async function loadSource(sourcePath) {
    state.currentSource = sourcePath;
    elements.docTextContainer.innerHTML = '<div class="skeleton-loader"></div>';
    elements.docMainTitle.textContent = "Loading documents...";
    
    try {
        const res = await fetch(`/api/documents?source=${encodeURIComponent(sourcePath)}`);
        if (res.ok) {
            const data = await res.json();
            if (data.error) {
                alert(data.error);
                return;
            }
            state.documents = (data.documents || []).map(doc => ({
                ...doc,
                fetchedText: {},
                fetchedSections: {}
            }));
            state.currentIndex = 0;
            elements.totalDocsCount.textContent = state.documents.length;
            await renderCurrentDocument();
        }
    } catch (e) {
        console.error("Error loading source documents:", e);
    }
}

// Render Current Document
async function renderCurrentDocument() {
    if (!state.documents || state.documents.length === 0) {
        elements.docMainTitle.textContent = "No documents found in dataset";
        elements.docTextContainer.textContent = "Please select or load another source JSON file.";
        elements.docMetadataCard.innerHTML = "<div>No metadata</div>";
        return;
    }

    const doc = state.documents[state.currentIndex];
    elements.currentDocIndex.textContent = state.currentIndex + 1;
    elements.docMainTitle.textContent = doc.title || `Document #${state.currentIndex + 1}`;
    elements.docIdBadge.textContent = `ID: ${doc.id}`;

    renderMetadataCard(doc);
    renderPropertyTabs(doc.data);
    await renderDocumentText();
    renderAnnotationsSidebar();

    const docAnns = getAnnotationsForDoc(doc.id);
    if (docAnns.length > 0) {
        elements.docStatusBadge.textContent = `Annotated (${docAnns.length})`;
        elements.docStatusBadge.className = "status-badge status-done";
    } else {
        elements.docStatusBadge.textContent = "Unannotated";
        elements.docStatusBadge.className = "status-badge status-pending";
    }
}

// Render Left Sidebar Metadata Card
function renderMetadataCard(doc) {
    const item = doc.data || {};
    const keys = ["type", "identificador", "institucio", "institution", "recordDate", "datePublished", "year", "isVigent", "urlPdf", "urlHtml", "butlleti", "numButlleti"];
    
    let html = "";
    keys.forEach(k => {
        if (item[k] !== undefined && item[k] !== null && item[k] !== "") {
            let val = item[k];
            if (typeof val === "object") val = JSON.stringify(val);
            if (String(val).startsWith("http")) {
                html += `
                    <div class="meta-row">
                        <span class="meta-label">${k}</span>
                        <a href="${val}" target="_blank" class="meta-link">${val.length > 35 ? val.substring(0, 35) + '...' : val} ↗</a>
                    </div>`;
            } else {
                html += `
                    <div class="meta-row">
                        <span class="meta-label">${k}</span>
                        <span class="meta-value">${val}</span>
                    </div>`;
            }
        }
    });

    if (!html) {
        html = '<div class="meta-row"><span class="meta-value">Standard JSON Object</span></div>';
    }

    elements.docMetadataCard.innerHTML = html;
}

// Render Property Tab Bar
function renderPropertyTabs(data) {
    elements.propertyTabs.innerHTML = "";
    
    const availableProps = [];
    if (data.urlPdf) availableProps.push("urlPdf");
    if (data.urlHtml) availableProps.push("urlHtml");
    if (data.descripcio) availableProps.push("descripcio");
    availableProps.push("auto");

    const candidateKeys = ["recordTitle", "title", "text", "content", "fase", "sections"];
    candidateKeys.forEach(k => {
        if (data[k] !== undefined && data[k] !== null && !availableProps.includes(k)) {
            availableProps.push(k);
        }
    });

    elements.activePropSelect.innerHTML = "";
    availableProps.forEach(prop => {
        let label = prop;
        if (prop === "urlPdf") label = "📄 Official PDF Text (urlPdf)";
        else if (prop === "urlHtml") label = "🌐 Official HTML Text (urlHtml)";
        else if (prop === "descripcio") label = "📝 Description (descripcio)";
        else if (prop === "auto") label = "✨ Combined Fields";

        const opt = document.createElement("option");
        opt.value = prop;
        opt.textContent = label;
        if (prop === state.activeTabProperty) opt.selected = true;
        elements.activePropSelect.appendChild(opt);

        const tab = document.createElement("button");
        tab.className = `prop-tab ${prop === state.activeTabProperty ? "active" : ""}`;
        tab.textContent = label;
        tab.onclick = async () => {
            state.activeTabProperty = prop;
            elements.activePropSelect.value = prop;
            renderPropertyTabs(data);
            await renderDocumentText();
        };
        elements.propertyTabs.appendChild(tab);
    });
}

// Fetch PDF or HTML extracted text & sections from backend
async function fetchUrlTextIfNeeded(doc, prop) {
    if (prop !== "urlPdf" && prop !== "urlHtml") return null;
    if (doc.fetchedText && doc.fetchedText[prop]) return doc.fetchedText[prop];

    const type = prop === "urlPdf" ? "pdf" : "html";
    const urlPdf = doc.data.urlPdf || "";
    const urlHtml = doc.data.urlHtml || "";

    if (!urlPdf && !urlHtml) return null;

    try {
        elements.docTextContainer.innerHTML = `
            <div style="text-align: center; padding: 40px; color: var(--text-muted);">
                <div class="skeleton-loader" style="height: 120px; margin-bottom: 16px;"></div>
                <p>⏳ Parsing document sections from official ${type.toUpperCase()} link...</p>
                <span style="font-size: 12px; font-family: var(--font-mono); color: var(--accent-blue);">${type === "pdf" ? urlPdf : urlHtml}</span>
            </div>`;

        const res = await fetch(`/api/fetch_text?doc_id=${encodeURIComponent(doc.id)}&url_pdf=${encodeURIComponent(urlPdf)}&url_html=${encodeURIComponent(urlHtml)}&type=${type}`);
        if (res.ok) {
            const data = await res.json();
            if (data.status === "success" && data.text) {
                if (!doc.fetchedText) doc.fetchedText = {};
                if (!doc.fetchedSections) doc.fetchedSections = {};
                doc.fetchedText[prop] = data.text;
                doc.fetchedSections[prop] = data.sections || [];
                return data.text;
            }
        }
    } catch (e) {
        console.error(`Error fetching ${type} text:`, e);
    }

    return null;
}

// Extract Target Document Text
async function getDocumentTextAsync(doc, propertyName) {
    const data = doc.data || {};
    
    if (propertyName === "urlPdf" || propertyName === "urlHtml") {
        const fetched = await fetchUrlTextIfNeeded(doc, propertyName);
        if (fetched) return fetched;
    }

    if (propertyName !== "auto" && data[propertyName] !== undefined && data[propertyName] !== null) {
        let val = data[propertyName];
        if (typeof val === "object") return JSON.stringify(val, null, 2);
        return String(val);
    }

    // Auto mode
    let parts = [];
    if (data.recordTitle) parts.push(`TITLE: ${data.recordTitle}`);
    if (data.title && data.title !== data.recordTitle) parts.push(`TITLE: ${data.title}`);
    if (data.institucio || data.institution) parts.push(`INSTITUTION: ${data.institucio || data.institution}`);
    if (data.fase) parts.push(`FASE: ${data.fase}`);
    if (data.descripcio) parts.push(`DESCRIPCIO:\n${data.descripcio}`);
    if (data.text) parts.push(`TEXT:\n${data.text}`);
    if (data.content) parts.push(`CONTENT:\n${data.content}`);
    if (data.sections && Array.isArray(data.sections)) {
        parts.push("SECTIONS:\n" + data.sections.map(s => typeof s === 'string' ? s : JSON.stringify(s)).join("\n---\n"));
    }

    if (parts.length === 0) {
        return JSON.stringify(data, null, 2);
    }

    return parts.join("\n\n");
}

// Get section containing offset
function getSectionForOffset(doc, prop, startOffset) {
    const sections = (doc.fetchedSections && doc.fetchedSections[prop]) || [];
    if (!sections || sections.length === 0) {
        return { title: "Body", type: "body" };
    }

    for (let sec of sections) {
        if (startOffset >= sec.start_offset && startOffset <= sec.end_offset) {
            return sec;
        }
    }

    return sections[0] || { title: "Body", type: "body" };
}

// Get Annotations for current doc ID
function getAnnotationsForDoc(docId) {
    return state.annotations.filter(ann => String(ann.doc_id) === String(docId));
}

// Render Document Text with Highlight Marks
async function renderDocumentText() {
    if (!state.documents || state.documents.length === 0) return;
    const doc = state.documents[state.currentIndex];
    const rawText = await getDocumentTextAsync(doc, state.activeTabProperty);
    const docAnns = getAnnotationsForDoc(doc.id);

    elements.docTextContainer.innerHTML = buildHighlightedTextHtml(rawText, docAnns);

    const marks = elements.docTextContainer.querySelectorAll("mark.annotation-highlight");
    marks.forEach(mark => {
        mark.onclick = (e) => {
            e.stopPropagation();
            const annId = mark.getAttribute("data-id");
            const ann = state.annotations.find(a => a.annotation_id === annId);
            if (ann) openAnnotationModal(ann);
        };
    });

    updateDocStats(docAnns);
}

// Build HTML string with highlight marks
function buildHighlightedTextHtml(text, annotations) {
    if (!text) {
        return `<div style="padding: 24px; color: var(--text-dim); text-align: center;">No text available for target property '${state.activeTabProperty}'.</div>`;
    }

    if (!annotations || annotations.length === 0) {
        return escapeHtml(text);
    }

    const validAnns = annotations.filter(a => {
        const start = parseInt(a.start_offset);
        const end = parseInt(a.end_offset);
        return !isNaN(start) && !isNaN(end) && start >= 0 && end <= text.length && start < end;
    }).sort((a, b) => parseInt(a.start_offset) - parseInt(b.start_offset));

    if (validAnns.length === 0) return escapeHtml(text);

    let html = "";
    let lastIndex = 0;

    validAnns.forEach(ann => {
        const start = parseInt(ann.start_offset);
        const end = parseInt(ann.end_offset);

        if (start >= lastIndex) {
            html += escapeHtml(text.substring(lastIndex, start));
            const catObj = getCategoryObj(ann.category);
            const color = catObj ? catObj.color : "#3b82f6";
            const colorRgb = hexToRgb(color);
            const bgStyle = `background-color: rgba(${colorRgb}, 0.25); border-bottom-color: ${color};`;
            
            html += `<mark class="annotation-highlight" data-id="${ann.annotation_id}" style="${bgStyle}" title="[${ann.category}] ${escapeHtml(ann.comment || 'Click to edit')}">${escapeHtml(text.substring(start, end))}</mark>`;
            lastIndex = end;
        }
    });

    if (lastIndex < text.length) {
        html += escapeHtml(text.substring(lastIndex));
    }

    return html;
}

// Text Selection Handling
async function handleTextSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) {
        elements.selectionPopover.classList.add("hidden");
        return;
    }

    const range = sel.getRangeAt(0);
    const container = elements.docTextContainer;

    if (!container.contains(range.commonAncestorContainer)) {
        elements.selectionPopover.classList.add("hidden");
        return;
    }

    const selectedText = sel.toString().trim();
    if (selectedText.length === 0) {
        elements.selectionPopover.classList.add("hidden");
        return;
    }

    const doc = state.documents[state.currentIndex];
    const fullRawText = await getDocumentTextAsync(doc, state.activeTabProperty);
    const preSelectionRange = range.cloneRange();
    preSelectionRange.selectNodeContents(container);
    preSelectionRange.setEnd(range.startContainer, range.startOffset);
    
    const startOffset = preSelectionRange.toString().length;
    const endOffset = startOffset + selectedText.length;

    // Detect Section
    const section = getSectionForOffset(doc, state.activeTabProperty, startOffset);

    state.activeSelection = {
        text: selectedText,
        startOffset: startOffset,
        endOffset: endOffset,
        targetProperty: state.activeTabProperty,
        sectionTitle: section.title || "Body",
        sectionType: section.type || "body"
    };

    const rect = range.getBoundingClientRect();
    const wrapperRect = container.parentElement.getBoundingClientRect();
    const top = rect.top - wrapperRect.top + container.parentElement.scrollTop;
    const left = rect.left - wrapperRect.left + (rect.width / 2);

    elements.selectionPopover.style.top = `${top}px`;
    elements.selectionPopover.style.left = `${left}px`;
    elements.selectionPopover.classList.remove("hidden");
}

// Open Annotation Modal/Composer
function openAnnotationModal(existingAnn = null) {
    elements.selectionPopover.classList.add("hidden");
    elements.kvTableBody.innerHTML = "";
    
    elements.annCategory.innerHTML = "";
    state.settings.categories.forEach(cat => {
        const opt = document.createElement("option");
        opt.value = cat.name;
        opt.textContent = cat.name;
        elements.annCategory.appendChild(opt);
    });

    renderKvPresetsChips();

    if (existingAnn) {
        state.editingAnnotationId = existingAnn.annotation_id;
        elements.annModalTitle.textContent = "Edit Annotation";
        elements.modalSelectedText.textContent = existingAnn.selected_text;
        elements.modalTargetProp.textContent = existingAnn.target_property;
        elements.modalSectionTitle.textContent = existingAnn.section_title || "Body";
        elements.modalOffsets.textContent = `Char ${existingAnn.start_offset} - ${existingAnn.end_offset}`;
        elements.annCategory.value = existingAnn.category || state.settings.categories[0].name;
        elements.annComment.value = existingAnn.comment || "";

        if (existingAnn.key_values_json) {
            try {
                const kvObj = JSON.parse(existingAnn.key_values_json);
                Object.entries(kvObj).forEach(([k, v]) => addKvTableRow(k, v));
            } catch (e) {
                console.error("Error parsing kv_json:", e);
            }
        }
    } else {
        if (!state.activeSelection) return;
        state.editingAnnotationId = null;
        elements.annModalTitle.textContent = "Add Document Annotation";
        elements.modalSelectedText.textContent = state.activeSelection.text;
        elements.modalTargetProp.textContent = state.activeSelection.targetProperty;
        elements.modalSectionTitle.textContent = state.activeSelection.sectionTitle || "Body";
        elements.modalOffsets.textContent = `Char ${state.activeSelection.startOffset} - ${state.activeSelection.endOffset}`;
        elements.annCategory.value = state.settings.categories[0].name;
        elements.annComment.value = "";

        addKvTableRow("", "");
    }

    elements.annotationModal.classList.remove("hidden");
}

// Render Presets Chips
function renderKvPresetsChips() {
    elements.kvPresetsChips.innerHTML = "";
    state.settings.key_presets.forEach(key => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "chip-preset";
        chip.textContent = `+ ${key}`;
        chip.onclick = () => addKvTableRow(key, "");
        elements.kvPresetsChips.appendChild(chip);
    });
}

// Add Row to Key Value Builder Table
function addKvTableRow(key = "", val = "") {
    const tr = document.createElement("tr");
    tr.innerHTML = `
        <td><input type="text" class="styled-input kv-key-input" value="${escapeHtml(key)}" placeholder="Key name (e.g. law_id)" /></td>
        <td><input type="text" class="styled-input kv-val-input" value="${escapeHtml(val)}" placeholder="Value string..." /></td>
        <td style="text-align: center;"><button type="button" class="btn-remove-row" title="Delete row">&times;</button></td>
    `;
    tr.querySelector(".btn-remove-row").onclick = () => tr.remove();
    elements.kvTableBody.appendChild(tr);
}

// Save Annotation
async function saveAnnotation() {
    const doc = state.documents[state.currentIndex];
    
    const kvObj = {};
    const rows = elements.kvTableBody.querySelectorAll("tr");
    rows.forEach(tr => {
        const k = tr.querySelector(".kv-key-input").value.trim();
        const v = tr.querySelector(".kv-val-input").value.trim();
        if (k) kvObj[k] = v;
    });

    const isEdit = !!state.editingAnnotationId;
    const annId = isEdit ? state.editingAnnotationId : `ann_${Date.now()}_${Math.random().toString(36).substr(2, 5)}`;
    
    const annotationData = {
        doc_id: String(doc.id),
        doc_title: doc.title || "",
        doc_source: state.currentSource,
        annotation_id: annId,
        target_property: isEdit ? elements.modalTargetProp.textContent : state.activeSelection.targetProperty,
        selected_text: isEdit ? elements.modalSelectedText.textContent : state.activeSelection.text,
        start_offset: isEdit ? parseInt(elements.modalOffsets.textContent.split("-")[0].replace(/\D/g, "")) : state.activeSelection.startOffset,
        end_offset: isEdit ? parseInt(elements.modalOffsets.textContent.split("-")[1].replace(/\D/g, "")) : state.activeSelection.endOffset,
        section_title: isEdit ? elements.modalSectionTitle.textContent : (state.activeSelection.sectionTitle || "Body"),
        section_type: isEdit ? "section" : (state.activeSelection.sectionType || "body"),
        category: elements.annCategory.value,
        comment: elements.annComment.value.trim(),
        key_values_json: JSON.stringify(kvObj),
        created_at: new Date().toISOString(),
        doc_metadata_json: JSON.stringify({
            urlPdf: doc.data.urlPdf || "",
            urlHtml: doc.data.urlHtml || "",
            institucio: doc.data.institucio || doc.data.institution || "",
            year: doc.data.year || "",
            type: doc.data.type || ""
        })
    };

    try {
        const res = await fetch("/api/save_annotation", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                csv_path: state.settings.csv_path || "annotations.csv",
                annotation: annotationData
            })
        });

        if (res.ok) {
            const existingIdx = state.annotations.findIndex(a => a.annotation_id === annId);
            if (existingIdx >= 0) {
                state.annotations[existingIdx] = annotationData;
            } else {
                state.annotations.push(annotationData);
            }

            elements.annotationModal.classList.add("hidden");
            window.getSelection().removeAllRanges();
            state.activeSelection = null;
            
            await renderCurrentDocument();
            updateGlobalStats();
        }
    } catch (e) {
        console.error("Error saving annotation:", e);
    }
}

// Delete Annotation
async function deleteAnnotation(annId) {
    if (!confirm("Are you sure you want to delete this annotation?")) return;

    try {
        const res = await fetch("/api/delete_annotation", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                csv_path: state.settings.csv_path || "annotations.csv",
                annotation_id: annId
            })
        });

        if (res.ok) {
            state.annotations = state.annotations.filter(a => a.annotation_id !== annId);
            await renderCurrentDocument();
            updateGlobalStats();
        }
    } catch (e) {
        console.error("Error deleting annotation:", e);
    }
}

// Render Annotations Right Sidebar
function renderAnnotationsSidebar() {
    if (!state.documents || state.documents.length === 0) return;
    const doc = state.documents[state.currentIndex];
    let docAnns = getAnnotationsForDoc(doc.id);

    if (state.categoryFilter !== "ALL") {
        docAnns = docAnns.filter(a => a.category === state.categoryFilter);
    }

    elements.annSidebarCount.textContent = docAnns.length;
    renderCategoryFilterBar();

    if (docAnns.length === 0) {
        elements.annotationsList.innerHTML = `
            <div class="empty-state" style="text-align: center; color: var(--text-dim); padding: 24px 0;">
                <p>No annotations processed for this document.</p>
                <span style="font-size: 12px;">Highlight text in the document viewer to add comments and key-value tables.</span>
            </div>`;
        return;
    }

    let html = "";
    docAnns.forEach(ann => {
        const catObj = getCategoryObj(ann.category);
        const catColor = catObj ? catObj.color : "#3b82f6";
        
        let kvTableHtml = "";
        if (ann.key_values_json) {
            try {
                const kvObj = JSON.parse(ann.key_values_json);
                const keys = Object.keys(kvObj);
                if (keys.length > 0) {
                    kvTableHtml = '<table class="ann-kv-table-rendered"><tbody>';
                    keys.forEach(k => {
                        kvTableHtml += `<tr><td class="ann-kv-key">${escapeHtml(k)}</td><td class="ann-kv-val">${escapeHtml(kvObj[k])}</td></tr>`;
                    });
                    kvTableHtml += '</tbody></table>';
                }
            } catch (e) {}
        }

        html += `
            <div class="annotation-card" style="border-left-color: ${catColor};">
                <div class="ann-card-header">
                    <span class="ann-category-tag" style="background-color: ${catColor};">${escapeHtml(ann.category)}</span>
                    <span class="ann-section-tag" title="${escapeHtml(ann.section_title || 'Body')}">🏷️ ${escapeHtml(ann.section_title || 'Body')}</span>
                </div>
                <div class="ann-offsets-tag">${ann.target_property} | Char ${ann.start_offset}-${ann.end_offset}</div>
                <div class="ann-quote">"${escapeHtml(ann.selected_text)}"</div>
                ${ann.comment ? `<div class="ann-comment">${escapeHtml(ann.comment)}</div>` : ''}
                ${kvTableHtml}
                <div class="ann-card-actions">
                    <button class="btn btn-ghost btn-sm btn-edit-ann" data-id="${ann.annotation_id}" title="Edit annotation">✏️ Edit</button>
                    <button class="btn btn-ghost btn-sm btn-delete-ann" data-id="${ann.annotation_id}" title="Delete annotation" style="color: var(--accent-rose);">🗑️ Delete</button>
                </div>
            </div>`;
    });

    elements.annotationsList.innerHTML = html;

    elements.annotationsList.querySelectorAll(".btn-edit-ann").forEach(btn => {
        btn.onclick = () => {
            const ann = state.annotations.find(a => a.annotation_id === btn.getAttribute("data-id"));
            if (ann) openAnnotationModal(ann);
        };
    });

    elements.annotationsList.querySelectorAll(".btn-delete-ann").forEach(btn => {
        btn.onclick = () => deleteAnnotation(btn.getAttribute("data-id"));
    });
}

// Render Category Filter Bar
function renderCategoryFilterBar() {
    elements.categoryFilterBar.innerHTML = "";
    
    const allChip = document.createElement("button");
    allChip.className = `cat-filter-chip ${state.categoryFilter === "ALL" ? "active" : ""}`;
    allChip.textContent = "All";
    allChip.onclick = () => {
        state.categoryFilter = "ALL";
        renderAnnotationsSidebar();
    };
    elements.categoryFilterBar.appendChild(allChip);

    state.settings.categories.forEach(cat => {
        const chip = document.createElement("button");
        chip.className = `cat-filter-chip ${state.categoryFilter === cat.name ? "active" : ""}`;
        chip.textContent = cat.name;
        chip.onclick = () => {
            state.categoryFilter = cat.name;
            renderAnnotationsSidebar();
        };
        elements.categoryFilterBar.appendChild(chip);
    });
}

// Open Document List Browser Modal
function openDocListModal() {
    elements.docListModal.classList.remove("hidden");
    renderDocListGrid();
}

// Render Document Cards in List Browser
function renderDocListGrid() {
    const query = elements.docListSearch.value.trim().toLowerCase();
    const filter = state.docListFilter;

    let filtered = state.documents.filter((doc) => {
        const docAnns = getAnnotationsForDoc(doc.id);
        const hasAnns = docAnns.length > 0;

        if (filter === "annotated" && !hasAnns) return false;
        if (filter === "unannotated" && hasAnns) return false;

        if (query) {
            const matchTitle = (doc.title || "").toLowerCase().includes(query);
            const matchId = String(doc.id).toLowerCase().includes(query);
            const matchInst = String(doc.data.institucio || doc.data.institution || "").toLowerCase().includes(query);
            return matchTitle || matchId || matchInst;
        }

        return true;
    });

    const totalCount = state.documents.length;
    const annotatedCount = state.documents.filter(d => getAnnotationsForDoc(d.id).length > 0).length;
    const unannotatedCount = totalCount - annotatedCount;

    elements.countAllDocs.textContent = totalCount;
    elements.countAnnotatedDocs.textContent = annotatedCount;
    elements.countUnannotatedDocs.textContent = unannotatedCount;
    elements.docListStatusSummary.textContent = `Showing ${filtered.length} of ${totalCount} documents`;

    let html = "";
    filtered.forEach(doc => {
        const docAnns = getAnnotationsForDoc(doc.id);
        const count = docAnns.length;
        const isCurrent = doc.index === state.currentIndex;

        html += `
            <div class="doc-card-item ${isCurrent ? 'active' : ''}" onclick="selectDocFromList(${doc.index})">
                <div class="doc-card-header">
                    <span class="badge-id">#${doc.index + 1} | ${doc.id}</span>
                    <span class="status-badge ${count > 0 ? 'status-done' : 'status-pending'}">${count > 0 ? count + ' Anns' : 'Pending'}</span>
                </div>
                <div class="doc-card-title">${escapeHtml(doc.title)}</div>
                <div class="doc-card-body">
                    <span>${doc.data.institucio || doc.data.institution || 'N/A'}</span>
                    <span>${doc.data.datePublished || doc.data.recordDate || ''}</span>
                </div>
                <div class="doc-card-footer">
                    <span>${doc.data.type || 'JSON Document'}</span>
                    <span style="color: var(--accent-blue); font-weight: 600;">Open Document ➔</span>
                </div>
            </div>`;
    });

    elements.docListGrid.innerHTML = html || '<div style="padding: 24px; text-align: center; color: var(--text-dim);">No matching documents found.</div>';
}

function selectDocFromList(index) {
    state.currentIndex = index;
    elements.docListModal.classList.add("hidden");
    renderCurrentDocument();
}

// Open Settings Modal
function openSettingsModal() {
    elements.settingTargetProp.value = state.settings.target_property || "urlPdf";
    elements.settingCsvPath.value = state.settings.csv_path || "annotations.csv";
    
    renderPresetKeysTags();
    renderCategoriesEditor();

    elements.settingsModal.classList.remove("hidden");
}

function renderPresetKeysTags() {
    elements.presetKeysTagContainer.innerHTML = "";
    state.settings.key_presets.forEach((key, idx) => {
        const chip = document.createElement("span");
        chip.className = "chip-preset";
        chip.innerHTML = `${key} <button type="button" onclick="removePresetKey(${idx})" style="border:none;background:transparent;color:white;cursor:pointer;margin-left:4px;">&times;</button>`;
        elements.presetKeysTagContainer.appendChild(chip);
    });
}

function removePresetKey(idx) {
    state.settings.key_presets.splice(idx, 1);
    renderPresetKeysTags();
}

function renderCategoriesEditor() {
    elements.categoriesEditorList.innerHTML = "";
    state.settings.categories.forEach((cat, idx) => {
        const div = document.createElement("div");
        div.style.cssText = "display: flex; gap: 8px; align-items: center; margin-bottom: 6px;";
        div.innerHTML = `
            <input type="color" value="${cat.color}" onchange="state.settings.categories[${idx}].color = this.value" style="border:none;background:transparent;cursor:pointer;width:30px;height:30px;" />
            <input type="text" class="styled-input" value="${escapeHtml(cat.name)}" onchange="state.settings.categories[${idx}].name = this.value" style="flex:1;" />
        `;
        elements.categoriesEditorList.appendChild(div);
    });
}

// Save Settings
async function saveSettings() {
    state.settings.target_property = elements.settingTargetProp.value;
    state.settings.csv_path = elements.settingCsvPath.value.trim() || "annotations.csv";

    try {
        const res = await fetch("/api/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(state.settings)
        });
        if (res.ok) {
            elements.settingsModal.classList.add("hidden");
            await fetchAnnotations();
            await renderCurrentDocument();
        }
    } catch (e) {
        console.error("Error saving settings:", e);
    }
}

// Text Search in Document
function handleDocumentTextSearch() {
    const query = elements.textSearchInput.value.trim();
    if (!query) {
        elements.searchResultsCount.textContent = "";
        renderDocumentText();
        return;
    }

    const container = elements.docTextContainer;
    const text = container.textContent;
    const regex = new RegExp(escapeRegExp(query), "gi");
    const matches = text.match(regex);

    elements.searchResultsCount.textContent = matches ? `${matches.length} matches` : "0 matches";
}

// Update Stats
function updateDocStats(docAnns) {
    elements.statDocAnnotations.textContent = docAnns.length;
    let tablesCount = 0;
    docAnns.forEach(ann => {
        if (ann.key_values_json && ann.key_values_json !== "{}" && ann.key_values_json !== "[]") {
            tablesCount++;
        }
    });
    elements.statDocTables.textContent = tablesCount;
}

function updateGlobalStats() {
    elements.statTotalGlobalAnns.textContent = state.annotations.length;
}

// Utility Helpers
function getCategoryObj(name) {
    return state.settings.categories.find(c => c.name === name);
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function escapeRegExp(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function hexToRgb(hex) {
    hex = hex.replace('#', '');
    if (hex.length === 3) hex = hex.split('').map(char => char + char).join('');
    const num = parseInt(hex, 16);
    return `${(num >> 16) & 255}, ${(num >> 8) & 255}, ${num & 255}`;
}

// Setup Event Listeners
function setupEventListeners() {
    elements.btnPrevDoc.onclick = async () => {
        if (state.currentIndex > 0) {
            state.currentIndex--;
            await renderCurrentDocument();
        }
    };

    elements.btnNextDoc.onclick = async () => {
        if (state.currentIndex < state.documents.length - 1) {
            state.currentIndex++;
            await renderCurrentDocument();
        }
    };

    elements.sourceSelect.onchange = (e) => loadSource(e.target.value);
    
    elements.btnCustomSource.onclick = () => elements.customSourceModal.classList.remove("hidden");
    elements.btnCloseCustomSourceModal.onclick = () => elements.customSourceModal.classList.add("hidden");
    elements.btnCancelCustomSource.onclick = () => elements.customSourceModal.classList.add("hidden");
    elements.btnLoadCustomSource.onclick = () => {
        const val = elements.inputCustomSourcePath.value.trim();
        if (val) {
            elements.customSourceModal.classList.add("hidden");
            loadSource(val);
        }
    };

    elements.docTextContainer.onmouseup = handleTextSelection;
    elements.btnAnnotateSelection.onclick = () => openAnnotationModal();

    elements.btnCloseAnnModal.onclick = () => elements.annotationModal.classList.add("hidden");
    elements.btnCancelAnn.onclick = () => elements.annotationModal.classList.add("hidden");
    elements.btnSaveAnn.onclick = saveAnnotation;
    elements.btnAddKvRow.onclick = () => addKvTableRow("", "");

    elements.btnOpenDocList.onclick = openDocListModal;
    elements.btnCloseDocListModal.onclick = () => elements.docListModal.classList.add("hidden");
    elements.btnCloseDocListModalFooter.onclick = () => elements.docListModal.classList.add("hidden");
    elements.docListSearch.oninput = renderDocListGrid;

    document.querySelectorAll(".filter-tab").forEach(tab => {
        tab.onclick = () => {
            document.querySelectorAll(".filter-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            state.docListFilter = tab.getAttribute("data-filter");
            renderDocListGrid();
        };
    });

    elements.btnOpenSettings.onclick = openSettingsModal;
    elements.btnCloseSettingsModal.onclick = () => elements.settingsModal.classList.add("hidden");
    elements.btnCancelSettings.onclick = () => elements.settingsModal.classList.add("hidden");
    elements.btnSaveSettings.onclick = saveSettings;

    elements.btnAddPresetKey.onclick = () => {
        const val = elements.inputNewPresetKey.value.trim();
        if (val && !state.settings.key_presets.includes(val)) {
            state.settings.key_presets.push(val);
            elements.inputNewPresetKey.value = "";
            renderPresetKeysTags();
        }
    };

    elements.btnExportCsv.onclick = () => {
        const csvPath = state.settings.csv_path || "annotations.csv";
        alert(`Annotations are continuously saved and synced to:\n${csvPath}`);
    };

    elements.btnToggleTheme.onclick = () => {
        const current = document.documentElement.getAttribute("data-theme");
        const next = current === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", next);
        elements.btnToggleTheme.textContent = next === "dark" ? "🌙" : "☀️";
    };

    elements.textSearchInput.oninput = handleDocumentTextSearch;

    elements.activePropSelect.onchange = async (e) => {
        state.activeTabProperty = e.target.value;
        renderPropertyTabs(state.documents[state.currentIndex].data);
        await renderDocumentText();
    };

    window.onkeydown = (e) => {
        if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;

        if (e.altKey && e.key === "ArrowLeft") {
            elements.btnPrevDoc.click();
        } else if (e.altKey && e.key === "ArrowRight") {
            elements.btnNextDoc.click();
        } else if (e.altKey && (e.key === "l" || e.key === "L")) {
            openDocListModal();
        } else if (e.key === "Escape") {
            elements.annotationModal.classList.add("hidden");
            elements.docListModal.classList.add("hidden");
            elements.settingsModal.classList.add("hidden");
            elements.customSourceModal.classList.add("hidden");
            elements.selectionPopover.classList.add("hidden");
        }
    };
}

document.addEventListener("DOMContentLoaded", init);
