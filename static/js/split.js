console.log("Split JS Loaded");

// ===============================
// HTML Elements
// ===============================
const input = document.getElementById("pdfInput");
const dropArea = document.getElementById("dropArea");
const firstPageThumb = document.getElementById("firstPageThumb");
const fileCard = document.getElementById("fileCard");
const fileName = document.getElementById("fileName");
const modeSection = document.getElementById("modeSection");
const modeInput = document.getElementById("modeInput");
const rangeSection = document.getElementById("rangeSection");
const visualSection = document.getElementById("visualSection");
const visualGrid = document.getElementById("visualGrid");
const splitBtn = document.getElementById("splitBtn");

// PDF.js document (global)
let pdfDoc = null;

// ===============================
// File Select
// ===============================
input.addEventListener("change", function () {
    if (input.files.length > 0) {
        showFile(input.files[0]);
    }
});

function showFile(file) {
    window._pdfFile = file;
    fileName.innerHTML = "📄 " + file.name;
    dropArea.style.display = "none";
    fileCard.style.display = "flex";
    loadPDF(file, null);
}

// ===============================
// Drag & Drop
// ===============================
dropArea.addEventListener("dragover", function (e) {
    e.preventDefault();
    dropArea.classList.add("drag-over");
});

dropArea.addEventListener("dragleave", function () {
    dropArea.classList.remove("drag-over");
});

dropArea.addEventListener("drop", function (e) {
    e.preventDefault();
    dropArea.classList.remove("drag-over");

    const file = e.dataTransfer.files[0];

    if (file && file.type === "application/pdf") {
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
        showFile(file);
    }
});

// ===============================
// Load PDF with PDF.js
// ===============================
function loadPDF(file, password) {

    const fileReader = new FileReader();

    fileReader.onload = function (e) {

        window._pdfData = new Uint8Array(e.target.result);
        const opts = password ? {data: window._pdfData, password: password} : {data: window._pdfData};

        pdfjsLib.getDocument(opts).promise.then(function (pdf) {
            pdfDoc = pdf;
            const pp = document.getElementById("pdfPasswordPrompt");
            if (pp) pp.style.display = "none";
            modeSection.style.display = "block";
            renderFirstPageThumb();
            renderVisualGrid();
        }).catch(function(err) {
            if (err.name === "PasswordException") {
                const pp = document.getElementById("pdfPasswordPrompt");
                if (pp) {
                    pp.style.display = "block";
                    if (password) document.getElementById("pdfPassError").style.display = "block";
                    setTimeout(function() {
                        const inp = document.getElementById("pdfPassInput");
                        if (inp) inp.focus();
                    }, 100);
                }
            }
        });

    };

    fileReader.readAsArrayBuffer(file);
}

function confirmPdfPassword() {
    var pass = document.getElementById("pdfPassInput").value;
    if (!pass || !window._pdfFile) return;
    document.getElementById("pdfPassError").style.display = "none";
    document.getElementById("pdfPasswordHidden").value = pass;
    loadPDF(window._pdfFile, pass);
}

function togglePdfPass(btn) {
    const inp = document.getElementById("pdfPassInput");
    const svg = btn.querySelector("svg");
    inp.type = inp.type === "password" ? "text" : "password";
    svg.setAttribute("stroke", inp.type === "text" ? "#F9A825" : "#888");
}

// ===============================
// First Page Thumbnail in Drop Area
// ===============================
function renderFirstPageThumb() {

    pdfDoc.getPage(1).then(function (page) {

        const viewport = page.getViewport({ scale: 0.5 });
        firstPageThumb.width = viewport.width;
        firstPageThumb.height = viewport.height;

        const context = firstPageThumb.getContext("2d");
        page.render({ canvasContext: context, viewport: viewport });

        // File card-এ thumbnail দেখাও

    });

}

// ===============================
// Render Visual Grid
// ===============================
function renderVisualGrid() {

    if (!pdfDoc) return;

    visualGrid.innerHTML = "";

    for (let i = 1; i <= pdfDoc.numPages; i++) {

        const wrapper = document.createElement("div");
        wrapper.className = "visual-thumb";
        wrapper.dataset.page = i;

        wrapper.innerHTML = `
            <button type="button" class="thumb-remove" onclick="removePage(this)">✖</button>
            <canvas id="vthumb-${i}"></canvas>
            <p>Page ${i}</p>
        `;

        visualGrid.appendChild(wrapper);

        pdfDoc.getPage(i).then(function (page) {
            const canvas = document.getElementById("vthumb-" + page.pageNumber);
            const context = canvas.getContext("2d");
            const viewport = page.getViewport({ scale: 0.25 });
            canvas.width = viewport.width;
            canvas.height = viewport.height;
            page.render({ canvasContext: context, viewport: viewport });
        });

    }

    // Drag to reorder
    Sortable.create(visualGrid, {
        animation: 150,
        ghostClass: "sortable-ghost"
    });

}

// ===============================
// Remove Page
// ===============================
function removePage(btn) {
    btn.parentElement.remove();
}

// ===============================
// Mode Switch
// ===============================
function setMode(mode) {

    modeInput.value = mode;

    document.querySelectorAll(".mode-btn").forEach(function (btn) {
        btn.classList.remove("active");
    });

    event.target.classList.add("active");

    rangeSection.style.display = "none";
    visualSection.style.display = "none";

    if (mode === "visual") {
        visualSection.style.display = "block";
        splitBtn.innerHTML = "💾 Save PDF";
    } else if (mode === "range") {
        rangeSection.style.display = "block";
        splitBtn.innerHTML = "✂️ Split PDF";
    } else if (mode === "all") {
        splitBtn.innerHTML = "✂️ Split All Pages";
    }

}

// ===============================
// Form Submit
// ===============================
const form = document.getElementById("splitForm");

form.addEventListener("submit", function (e) {

    const mode = modeInput.value;

    if (mode === "range") {
        const ranges = document.getElementById("rangesInput").value.trim();
        if (!ranges) {
            e.preventDefault();
            alert("Please enter page ranges.");
            return;
        }
    }

    if (mode === "visual") {
        const pages = visualGrid.querySelectorAll(".visual-thumb");
        if (pages.length === 0) {
            e.preventDefault();
            alert("No pages left!");
            return;
        }
        const order = Array.from(pages).map(function (p) {
            return p.dataset.page;
        });
        document.getElementById("pageOrderInput").value = order.join(",");
    }

    splitBtn.style.display = "none";
    fileName.innerHTML = `
        <div class="loading">
            <div class="spinner"></div>
            <p>Processing PDF...</p>
        </div>
    `;

    setTimeout(function () {
        window.location.reload();
    }, 4000);

});
