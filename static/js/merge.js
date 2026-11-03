

console.log("Merge JS Loaded");

const count = document.getElementById("count");
const mergeBtn = document.getElementById("mergeBtn");
const input = document.getElementById("pdfInput");
const fileList = document.getElementById("fileList");


const dataTransfer = new DataTransfer();

mergeBtn.style.display = "none";
count.style.display = "none";

input.addEventListener("change", function () {

    for (const file of input.files) {
        dataTransfer.items.add(file);
    }

    input.files = dataTransfer.files;

    refreshList();

});


function refreshList() {

    fileList.innerHTML = "";
    count.innerHTML = dataTransfer.files.length + " PDF Selected";

    if (dataTransfer.files.length > 0) {
        mergeBtn.style.display = "inline-block";
        count.style.display = "block";
    } else {
        mergeBtn.style.display = "none";
        count.style.display = "none";
    }

    Array.from(dataTransfer.files).forEach(function (file, index) {

        // প্রতিটা file-এর জন্য unique id
        const canvasId = "canvas-" + index;

        fileList.innerHTML += `
<div class="pdf-item" data-index="${index}">
    <canvas id="${canvasId}" class="pdf-thumbnail"></canvas>
    <span>📄 ${file.name}</span>
    <button type="button" class="remove-btn" onclick="removeFile(${index})">✖</button>
</div>
`; `
        <div class="pdf-item">

            <canvas id="${canvasId}" class="pdf-thumbnail"></canvas>

            <span>📄 ${file.name}</span>

            <button
                type="button"
                class="remove-btn"
                onclick="removeFile(${index})">
                ✖
            </button>
            
        </div>
        `;

        // Canvas render করো
        renderThumbnail(file, canvasId);

    });
    // Drag to reorder
    Sortable.create(fileList, {
        animation: 150,
        onEnd: function () {

            // নতুন order অনুযায়ী dataTransfer update করো
            const items = fileList.querySelectorAll(".pdf-item");
            const newData = new DataTransfer();

            items.forEach(function (item) {
                const index = parseInt(item.dataset.index);
                newData.items.add(dataTransfer.files[index]);
            });

            dataTransfer.items.clear();

            Array.from(newData.files).forEach(function (file) {
                dataTransfer.items.add(file);
            });

            input.files = dataTransfer.files;

        }
    });
}


function removeFile(index) {

    const newData = new DataTransfer();

    Array.from(dataTransfer.files).forEach(function (file, i) {

        if (i !== index) {
            newData.items.add(file);
        }

    });

    dataTransfer.items.clear();

    Array.from(newData.files).forEach(function (file) {

        dataTransfer.items.add(file);

    });

    input.files = dataTransfer.files;

    refreshList();

}
// ===============================
// Drag & Drop
// ===============================
const dropArea = document.getElementById("dropArea");

// File drag করে উপরে আনলে highlight
dropArea.addEventListener("dragover", function (e) {
    e.preventDefault(); // browser default block করো
    dropArea.classList.add("drag-over");
});

// File সরিয়ে নিলে highlight তুলে নাও
dropArea.addEventListener("dragleave", function () {
    dropArea.classList.remove("drag-over");
});

// File drop করলে
dropArea.addEventListener("drop", function (e) {
    e.preventDefault();
    dropArea.classList.remove("drag-over");

    // Drop করা files নাও
    const droppedFiles = e.dataTransfer.files;

    // শুধু PDF নাও
    for (const file of droppedFiles) {
        if (file.type === "application/pdf") {
            dataTransfer.items.add(file);
        }
    }

    // Input update করো
    input.files = dataTransfer.files;

    refreshList();
});

function renderThumbnail(file, canvasId) {

    const fileReader = new FileReader();

    fileReader.onload = function (e) {

        const typedArray = new Uint8Array(e.target.result);

        pdfjsLib.getDocument(typedArray).promise.then(function (pdf) {

            pdf.getPage(1).then(function (page) {

                const canvas = document.getElementById(canvasId);
                const context = canvas.getContext("2d");

                const viewport = page.getViewport({ scale: 0.3 });
                canvas.width = viewport.width;
                canvas.height = viewport.height;

                page.render({ canvasContext: context, viewport: viewport });

            });

        });

    };

    fileReader.readAsArrayBuffer(file);

}
const form = document.querySelector("form");

form.addEventListener("submit", function () {

    mergeBtn.style.display = "none";
    count.style.display = "none";

    fileList.innerHTML = `
        <div class="loading">
            <div class="spinner"></div>
            <p>Merging PDF files...</p>
        </div>
    `;

});
form.addEventListener("submit", function (e) {

    if (dataTransfer.files.length < 2) {
        e.preventDefault();
        alert("Please select at least 2 PDF files.");
        return;
    }

    mergeBtn.style.display = "none";
    count.style.display = "none";

    fileList.innerHTML = `
        <div class="loading">
            <div class="spinner"></div>
            <p>Merging PDF files...</p>
        </div>
    `;

    // Merge complete হওয়ার পর page reload
    setTimeout(function () {
        window.location.reload();
    }, 3000);

});

window.addEventListener("load", function () {
    dataTransfer.items.clear();
    input.files = dataTransfer.files;
    fileList.innerHTML = "";
    count.style.display = "none";
    mergeBtn.style.display = "none";
});
