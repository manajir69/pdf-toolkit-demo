from flask import Flask, render_template, request, send_file, Response, jsonify
from pypdf import PdfWriter, PdfReader
from datetime import datetime
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import fitz  # PyMuPDF
import zipfile
import io
import uuid
import os
import sys
import numpy as np
from PIL import Image as PILImage

app = Flask(__name__)

# In-memory cache for compressed files (keyed by file_id)
_compressed_cache = {}


# ===============================
# Helpers for password-protected PDFs
# ===============================

def get_font_archive(font_path):
    """Return (archive, css_block) for insert_htmlbox, or (None, '') if no font."""
    if not font_path:
        return None, ""
    font_dir  = os.path.dirname(font_path)
    font_file = os.path.basename(font_path)
    archive = fitz.Archive(font_dir)
    css = (
        f"@font-face {{ font-family: 'UniFont'; src: url('{font_file}'); }}"
        " body { font-family: 'UniFont', sans-serif; margin: 0; padding: 0; }"
    )
    return archive, css


def get_unicode_font():
    """Return path to a Unicode-capable TTF font. Auto-downloads NotoSans if not found."""
    import urllib.request

    font_dir = os.path.join(os.path.dirname(__file__), 'static', 'fonts')
    project_font = os.path.join(font_dir, 'unicode.ttf')

    # 1. Use already-downloaded or manually placed font
    if os.path.exists(project_font):
        return project_font

    # 2. Try system fonts
    if sys.platform == 'darwin':
        candidates = [
            '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
            '/Library/Fonts/Arial Unicode.ttf',
            os.path.expanduser('~/Library/Fonts/NotoSans-Regular.ttf'),
        ]
    elif sys.platform.startswith('linux'):
        candidates = [
            '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
            '/usr/share/fonts/noto/NotoSans-Regular.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
        ]
    else:
        candidates = [
            'C:/Windows/Fonts/arialuni.ttf',
            'C:/Windows/Fonts/arial.ttf',
        ]
    for p in candidates:
        if os.path.exists(p):
            return p

    # 3. Auto-download NotoSans from Google Fonts CDN
    try:
        os.makedirs(font_dir, exist_ok=True)
        url = 'https://fonts.gstatic.com/s/notosans/v36/o-0mIpQlx3QUlC5A4PNB6Ryti20_6n1iPHjcz6L1SoM-jCpoiyElyA.ttf'
        print("[INFO] Downloading NotoSans Unicode font...")
        urllib.request.urlretrieve(url, project_font)
        print(f"[INFO] Font saved to {project_font}")
        return project_font
    except Exception as e:
        print(f"[WARN] Could not download font: {e}")
        return None


def to_roman(num, upper=True):
    val  = [1000,900,500,400,100,90,50,40,10,9,5,4,1]
    syms = ['M','CM','D','CD','C','XC','L','XL','X','IX','V','IV','I']
    result = ''
    for i in range(len(val)):
        while num >= val[i]:
            result += syms[i]
            num -= val[i]
    return result if upper else result.lower()


def open_pdf_reader(source, password=""):
    if isinstance(source, bytes):
        reader = PdfReader(io.BytesIO(source))
    else:
        reader = PdfReader(source)
    if reader.is_encrypted:
        result = reader.decrypt(password or "")
        if not result:
            raise PermissionError("Wrong PDF password")
    return reader


def open_fitz_doc(file_bytes, password=""):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    if doc.is_encrypted:
        if not doc.authenticate(password or ""):
            raise PermissionError("Wrong PDF password")
    return doc


# ===============================
# Home
# ===============================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/contact")
def contact():
    return render_template("contact.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/terms")
def terms():
    return render_template("privacy.html")


@app.route("/sitemap.xml")
def sitemap():
    xml = render_template("sitemap.xml")
    return Response(xml, mimetype="application/xml")

@app.route("/robots.txt")
def robots():
    import os
    return send_file(os.path.join(app.root_path, "static", "robots.txt"), mimetype="text/plain")


# ===============================
# Merge PDF
# ===============================

@app.route("/merge", methods=["GET", "POST"])
def merge():
    if request.method == "POST":
        files = request.files.getlist("pdf_files")
        pdf_password = request.form.get("pdf_password", "").strip()
        if len(files) < 2:
            return render_template("merge.html", error="Please select at least 2 PDF files.")
        writer = PdfWriter()
        try:
            for file in files:
                reader = open_pdf_reader(file, pdf_password)
                writer.append(reader)
        except PermissionError:
            return render_template("merge.html", error="One or more PDFs are password protected. Please unlock them first.")
        except Exception as e:
            return render_template("merge.html", error=f"Error reading PDF: {str(e)}")
        first_name = files[0].filename.rsplit(".", 1)[0]
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        download_name = f"{first_name}_{now}.pdf"
        output_path = "output/merged.pdf"
        writer.write(output_path)
        writer.close()
        return send_file(output_path, as_attachment=True, download_name=download_name)
    return render_template("merge.html")


# ===============================
# Split PDF
# ===============================

@app.route("/split", methods=["GET", "POST"])
def split():
    tool = request.args.get("tool", "split")
    titles = {
        "split": "Split PDF",
        "delete": "Delete Pages",
        "extract": "Extract Pages",
        "rearrange": "Rearrange Pages"
    }
    title = titles.get(tool, "Split PDF")

    if request.method == "POST":
        file = request.files.get("pdf_file")
        mode = request.form.get("mode")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("split.html", title=title, error="Please select a PDF file.")
        try:
            reader = open_pdf_reader(file, pdf_password)
        except PermissionError:
            return render_template("split.html", title=title, error="Wrong PDF password. Please try again.")
        except Exception as e:
            return render_template("split.html", title=title, error=f"Error reading PDF: {str(e)}")

        if mode == "range":
            ranges = request.form.get("ranges")
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as zip_file:
                for part in ranges.split(","):
                    part = part.strip()
                    writer = PdfWriter()
                    if "-" in part:
                        start, end = part.split("-")
                        start = int(start.strip()) - 1
                        end = int(end.strip())
                        for i in range(start, end):
                            writer.add_page(reader.pages[i])
                        filename = f"pages_{part}.pdf"
                    else:
                        page_num = int(part) - 1
                        writer.add_page(reader.pages[page_num])
                        filename = f"page_{part}.pdf"
                    pdf_buffer = io.BytesIO()
                    writer.write(pdf_buffer)
                    zip_file.writestr(filename, pdf_buffer.getvalue())
            zip_buffer.seek(0)
            return send_file(zip_buffer, as_attachment=True, download_name="split_pages.zip", mimetype="application/zip")

        elif mode == "all":
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as zip_file:
                for i, page in enumerate(reader.pages):
                    writer = PdfWriter()
                    writer.add_page(page)
                    pdf_buffer = io.BytesIO()
                    writer.write(pdf_buffer)
                    zip_file.writestr(f"page_{i+1}.pdf", pdf_buffer.getvalue())
            zip_buffer.seek(0)
            return send_file(zip_buffer, as_attachment=True, download_name="all_pages.zip", mimetype="application/zip")

        elif mode == "visual":
            page_order = request.form.get("page_order")
            pages = [int(p) - 1 for p in page_order.split(",")]
            writer = PdfWriter()
            for page_index in pages:
                writer.add_page(reader.pages[page_index])
            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            pdf_buffer = io.BytesIO()
            writer.write(pdf_buffer)
            pdf_buffer.seek(0)
            return send_file(pdf_buffer, as_attachment=True, download_name=f"edited_{now}.pdf", mimetype="application/pdf")

    return render_template("split.html", title=title)


# ===============================
# Delete Pages
# ===============================

@app.route("/delete-pages", methods=["GET", "POST"])
def delete_pages():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        mode = request.form.get("mode")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("delete_pages.html", error="Please select a PDF file.")
        try:
            reader = open_pdf_reader(file, pdf_password)
        except PermissionError:
            return render_template("delete_pages.html", error="Wrong PDF password. Please try again.")

        if mode == "visual":
            page_order = request.form.get("page_order")
            pages = [int(p) - 1 for p in page_order.split(",")]
            writer = PdfWriter()
            for page_index in pages:
                writer.add_page(reader.pages[page_index])
            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            pdf_buffer = io.BytesIO()
            writer.write(pdf_buffer)
            pdf_buffer.seek(0)
            return send_file(pdf_buffer, as_attachment=True, download_name=f"deleted_{now}.pdf", mimetype="application/pdf")

        elif mode == "range":
            ranges = request.form.get("ranges")
            pages_to_delete = set()
            for part in ranges.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = part.split("-")
                    for i in range(int(start.strip()) - 1, int(end.strip())):
                        pages_to_delete.add(i)
                else:
                    pages_to_delete.add(int(part) - 1)
            writer = PdfWriter()
            for i, page in enumerate(reader.pages):
                if i not in pages_to_delete:
                    writer.add_page(page)
            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            pdf_buffer = io.BytesIO()
            writer.write(pdf_buffer)
            pdf_buffer.seek(0)
            return send_file(pdf_buffer, as_attachment=True, download_name=f"deleted_{now}.pdf", mimetype="application/pdf")

    return render_template("delete_pages.html")


# ===============================
# Extract Pages
# ===============================

@app.route("/extract-pages", methods=["GET", "POST"])
def extract_pages():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        mode = request.form.get("mode")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("extract_pages.html", error="Please select a PDF file.")
        try:
            reader = open_pdf_reader(file, pdf_password)
        except PermissionError:
            return render_template("extract_pages.html", error="Wrong PDF password. Please try again.")

        if mode == "visual":
            page_order = request.form.get("page_order")
            pages = [int(p) - 1 for p in page_order.split(",")]
            writer = PdfWriter()
            for page_index in pages:
                writer.add_page(reader.pages[page_index])
            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            pdf_buffer = io.BytesIO()
            writer.write(pdf_buffer)
            pdf_buffer.seek(0)
            return send_file(pdf_buffer, as_attachment=True, download_name=f"extracted_{now}.pdf", mimetype="application/pdf")

        elif mode == "range":
            ranges = request.form.get("ranges")
            writer = PdfWriter()
            for part in ranges.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = part.split("-")
                    for i in range(int(start.strip()) - 1, int(end.strip())):
                        writer.add_page(reader.pages[i])
                else:
                    writer.add_page(reader.pages[int(part) - 1])
            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            pdf_buffer = io.BytesIO()
            writer.write(pdf_buffer)
            pdf_buffer.seek(0)
            return send_file(pdf_buffer, as_attachment=True, download_name=f"extracted_{now}.pdf", mimetype="application/pdf")

    return render_template("extract_pages.html")


# ===============================
# Rearrange Pages
# ===============================

@app.route("/rearrange", methods=["GET", "POST"])
def rearrange():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("rearrange.html", error="Please select a PDF file.")
        try:
            reader = open_pdf_reader(file, pdf_password)
        except PermissionError:
            return render_template("rearrange.html", error="Wrong PDF password. Please try again.")
        page_order = request.form.get("page_order")
        pages = [int(p) - 1 for p in page_order.split(",")]
        writer = PdfWriter()
        for page_index in pages:
            writer.add_page(reader.pages[page_index])
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        pdf_buffer = io.BytesIO()
        writer.write(pdf_buffer)
        pdf_buffer.seek(0)
        return send_file(pdf_buffer, as_attachment=True, download_name=f"rearranged_{now}.pdf", mimetype="application/pdf")
    return render_template("rearrange.html")


# ===============================
# Rotate PDF
# ===============================

@app.route("/rotate", methods=["GET", "POST"])
def rotate():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("rotate.html", error="Please select a PDF file.")
        page_rotations = request.form.get("page_rotations", "")
        try:
            reader = open_pdf_reader(file, pdf_password)
        except PermissionError:
            return render_template("rotate.html", error="Wrong PDF password. Please try again.")
        writer = PdfWriter()
        rotations = [int(r) for r in page_rotations.split(",")] if page_rotations else [0] * len(reader.pages)
        for i, page in enumerate(reader.pages):
            angle = rotations[i] if i < len(rotations) else 0
            if angle:
                page.rotate(angle)
            writer.add_page(page)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        pdf_buffer = io.BytesIO()
        writer.write(pdf_buffer)
        pdf_buffer.seek(0)
        return send_file(pdf_buffer, as_attachment=True, download_name=f"rotated_{now}.pdf", mimetype="application/pdf")
    return render_template("rotate.html")


# ===============================
# Image to PDF
# ===============================

@app.route("/image-to-pdf", methods=["GET", "POST"])
def image_to_pdf():
    if request.method == "POST":
        files = request.files.getlist("image_files")
        files = [f for f in files if f.filename]
        if not files:
            return render_template("image_to_pdf.html", error="Please select at least one image.")
        images = []
        for f in files:
            img = Image.open(f).convert("RGB")
            images.append(img)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        if len(images) == 1:
            images[0].save(output, format="PDF")
        else:
            images[0].save(output, format="PDF", save_all=True, append_images=images[1:])
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"images_{now}.pdf", mimetype="application/pdf")
    return render_template("image_to_pdf.html")


# ===============================
# PDF to Image
# ===============================

@app.route("/pdf-to-image", methods=["GET", "POST"])
def pdf_to_image():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        fmt = request.form.get("format", "png")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("pdf_to_image.html", error="Please select a PDF file.")
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("pdf_to_image.html", error="Wrong PDF password. Please try again.")
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, page in enumerate(doc):
                mat = fitz.Matrix(2, 2)
                pix = page.get_pixmap(matrix=mat)
                img_bytes = pix.tobytes(fmt)
                zf.writestr(f"page_{i+1}.{fmt}", img_bytes)
        zip_buffer.seek(0)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return send_file(zip_buffer, as_attachment=True, download_name=f"pdf_images_{now}.zip", mimetype="application/zip")
    return render_template("pdf_to_image.html")


# ===============================
# Compress PDF
# ===============================

@app.route("/compress", methods=["GET", "POST"])
def compress():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return jsonify({"error": "Please select a PDF file."})
        file_bytes = file.read()
        original_size = len(file_bytes)
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return jsonify({"error": "Wrong PDF password. Please try again."})
        output = io.BytesIO()
        doc.save(output, garbage=4, deflate=True, clean=True)
        compressed_bytes = output.getvalue()
        compressed_size = len(compressed_bytes)
        file_id = str(uuid.uuid4())
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"compressed_{now}.pdf"
        _compressed_cache[file_id] = {"data": compressed_bytes, "filename": filename}
        return jsonify({
            "original_size": original_size,
            "compressed_size": compressed_size,
            "file_id": file_id,
            "filename": filename
        })
    return render_template("compress.html")


@app.route("/compress/download/<file_id>")
def compress_download(file_id):
    entry = _compressed_cache.get(file_id)
    if not entry:
        return "File not found or expired.", 404
    return send_file(
        io.BytesIO(entry["data"]),
        as_attachment=True,
        download_name=entry["filename"],
        mimetype="application/pdf"
    )


# ===============================
# Protect PDF
# ===============================

@app.route("/protect", methods=["GET", "POST"])
def protect():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        password = request.form.get("password", "").strip()
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("protect.html", error="Please select a PDF file.")
        if not password:
            return render_template("protect.html", error="Please enter a password.")
        try:
            reader = open_pdf_reader(file, pdf_password)
        except PermissionError:
            return render_template("protect.html", error="Wrong PDF password. Please try again.")
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(password)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        pdf_buffer = io.BytesIO()
        writer.write(pdf_buffer)
        pdf_buffer.seek(0)
        return send_file(pdf_buffer, as_attachment=True, download_name=f"protected_{now}.pdf", mimetype="application/pdf")
    return render_template("protect.html")


# ===============================
# Unlock PDF
# ===============================

@app.route("/unlock", methods=["GET", "POST"])
def unlock():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        password = request.form.get("password", "").strip()
        if not file:
            return render_template("unlock.html", error="Please select a PDF file.")
        reader = PdfReader(file)
        if reader.is_encrypted:
            result = reader.decrypt(password)
            if not result:
                return render_template("unlock.html", error="Wrong password. Please try again.")
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        pdf_buffer = io.BytesIO()
        writer.write(pdf_buffer)
        pdf_buffer.seek(0)
        return send_file(pdf_buffer, as_attachment=True, download_name=f"unlocked_{now}.pdf", mimetype="application/pdf")
    return render_template("unlock.html")


# ===============================
# Watermark PDF
# ===============================

@app.route("/watermark", methods=["GET", "POST"])
def watermark():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        text = request.form.get("watermark_text", "WATERMARK").strip()
        pdf_password = request.form.get("pdf_password", "").strip()
        try:
            opacity = float(request.form.get("opacity", 0.3))
        except (ValueError, TypeError):
            opacity = 0.3
        if not file:
            return render_template("watermark.html", error="Please select a PDF file.")
        if not text:
            return render_template("watermark.html", error="Please enter watermark text.")
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("watermark.html", error="Wrong PDF password. Please try again.")

        font_path = get_unicode_font()
        archive, base_css = get_font_archive(font_path)
        fontsize = 60

        # Render text to a temporary page, then extract as rotated image
        # insert_htmlbox uses HarfBuzz shaping → correct Bengali/Unicode rendering

        # Render text on a temp page to get shaped pixels
        est_w = max(600, len(text) * fontsize)
        est_h = int(fontsize * 2.5)
        tmp_doc  = fitz.open()
        tmp_page = tmp_doc.new_page(width=est_w, height=est_h)

        wm_css  = base_css + f" p {{ font-size: {fontsize}pt; color: rgb(100,100,100); margin:0; padding:0; white-space:nowrap; }}"
        wm_html = f"<p>{text}</p>"
        kw = dict(css=wm_css, scale_low=0)
        if archive:
            kw["archive"] = archive
        tmp_page.insert_htmlbox(fitz.Rect(0, 0, est_w, est_h), wm_html, **kw)

        # Rasterise at 2× scale for sharpness
        pix = tmp_page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)

        # White → transparent, text pixels keep opacity
        alpha = np.where(arr.min(axis=2) < 220, int(opacity * 255), 0).astype(np.uint8)
        rgba  = np.dstack([arr, alpha])
        img   = PILImage.fromarray(rgba, "RGBA")

        # Crop to content then rotate 45°
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        img_rot = img.rotate(45, expand=True, resample=PILImage.BICUBIC)

        # Save rotated image once and reuse for all pages
        wm_buf = io.BytesIO()
        img_rot.save(wm_buf, format="PNG")
        wm_bytes = wm_buf.getvalue()

        for page in doc:
            rect  = page.rect
            scale = min(rect.width * 0.75 / img_rot.width,
                        rect.height * 0.75 / img_rot.height)
            w = img_rot.width  * scale
            h = img_rot.height * scale
            x = (rect.width  - w) / 2
            y = (rect.height - h) / 2
            page.insert_image(fitz.Rect(x, y, x + w, y + h),
                              stream=wm_bytes, overlay=True)

        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        doc.save(output, garbage=4, deflate=True)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"watermarked_{now}.pdf", mimetype="application/pdf")
    return render_template("watermark.html")


# ===============================
# Add Page Numbers
# ===============================

@app.route("/page-numbers", methods=["GET", "POST"])
def page_numbers():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        position = request.form.get("position", "bottom-center")
        start_num = int(request.form.get("start_num", 1))
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("page_numbers.html", error="Please select a PDF file.")

        prefix = request.form.get("prefix", "").strip()
        num_style = request.form.get("num_style", "arabic")

        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("page_numbers.html", error="Wrong PDF password. Please try again.")

        font_path = get_unicode_font()
        archive, base_css = get_font_archive(font_path)
        fontsize = 12
        margin   = 20
        lh       = fontsize + 6   # label box height

        for i, page in enumerate(doc):
            rect    = page.rect
            raw_num = i + start_num
            if num_style == "roman_upper":
                num_str = to_roman(raw_num, upper=True)
            elif num_style == "roman_lower":
                num_str = to_roman(raw_num, upper=False)
            else:
                num_str = str(raw_num)
            label = prefix + num_str

            # Build rect + alignment for htmlbox
            if position == "bottom-center":
                box   = fitz.Rect(0, rect.height - margin - lh, rect.width, rect.height - margin)
                align = "center"
            elif position == "bottom-right":
                box   = fitz.Rect(rect.width - 160, rect.height - margin - lh, rect.width - margin, rect.height - margin)
                align = "right"
            elif position == "bottom-left":
                box   = fitz.Rect(margin, rect.height - margin - lh, 160 + margin, rect.height - margin)
                align = "left"
            elif position == "top-center":
                box   = fitz.Rect(0, margin, rect.width, margin + lh)
                align = "center"
            elif position == "top-right":
                box   = fitz.Rect(rect.width - 160, margin, rect.width - margin, margin + lh)
                align = "right"
            elif position == "top-left":
                box   = fitz.Rect(margin, margin, 160 + margin, margin + lh)
                align = "left"
            else:
                box   = fitz.Rect(0, rect.height - margin - lh, rect.width, rect.height - margin)
                align = "center"

            pn_css  = base_css + f" p {{ font-size: {fontsize}pt; color: black; margin:0; padding:0; text-align: {align}; }}"
            pn_html = f"<p>{label}</p>"
            kw = dict(css=pn_css, scale_low=0)
            if archive:
                kw["archive"] = archive
            page.insert_htmlbox(box, pn_html, **kw)

        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        doc.save(output, garbage=4, deflate=True)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"numbered_{now}.pdf", mimetype="application/pdf")
    return render_template("page_numbers.html")


# ===============================
# Metadata
# ===============================

@app.route("/metadata", methods=["GET", "POST"])
def metadata():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        action = request.form.get("action", "view")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("metadata.html", error="Please select a PDF file.")

        file_bytes = file.read()
        try:
            reader = open_pdf_reader(file_bytes, pdf_password)
        except PermissionError:
            return render_template("metadata.html", error="Wrong PDF password. Please try again.")
        meta = reader.metadata or {}

        if action == "view":
            info = {
                "Title": meta.get("/Title", "—"),
                "Author": meta.get("/Author", "—"),
                "Subject": meta.get("/Subject", "—"),
                "Creator": meta.get("/Creator", "—"),
                "Pages": len(reader.pages),
            }
            return render_template("metadata.html", info=info)

        elif action == "edit":
            title = request.form.get("title", "")
            author = request.form.get("author", "")
            subject = request.form.get("subject", "")
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            writer.add_metadata({
                "/Title": title,
                "/Author": author,
                "/Subject": subject,
            })
            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            pdf_buffer = io.BytesIO()
            writer.write(pdf_buffer)
            pdf_buffer.seek(0)
            return send_file(pdf_buffer, as_attachment=True, download_name=f"metadata_{now}.pdf", mimetype="application/pdf")

    return render_template("metadata.html")


# ===============================
# Compare PDF
# ===============================

@app.route("/compare", methods=["GET", "POST"])
def compare():
    if request.method == "POST":
        file1 = request.files.get("pdf_file1")
        file2 = request.files.get("pdf_file2")
        if not file1 or not file2:
            return render_template("compare.html", error="Please select two PDF files.")

        try:
            doc1 = fitz.open(stream=file1.read(), filetype="pdf")
            doc2 = fitz.open(stream=file2.read(), filetype="pdf")
        except Exception as e:
            return render_template("compare.html", error=f"Error reading PDF: {str(e)}")

        text1 = "\n".join([page.get_text() for page in doc1])
        text2 = "\n".join([page.get_text() for page in doc2])

        info = {
            "name1": file1.filename,
            "name2": file2.filename,
            "pages1": len(doc1),
            "pages2": len(doc2),
            "chars1": len(text1),
            "chars2": len(text2),
            "identical": text1.strip() == text2.strip(),
        }
        return render_template("compare.html", info=info)
    return render_template("compare.html")


# ===============================
# OCR — Extract Text
# ===============================

@app.route("/ocr", methods=["GET", "POST"])
def ocr():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("ocr.html", error="Please select a PDF file.")
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("ocr.html", error="Wrong PDF password. Please try again.")
        pages_text = []
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            pages_text.append({"page": i + 1, "text": text or "(No text found on this page)"})
        return render_template("ocr.html", pages=pages_text, filename=file.filename)
    return render_template("ocr.html")


# ===============================
# Repair PDF
# ===============================

@app.route("/repair", methods=["GET", "POST"])
def repair():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("repair.html", error="Please select a PDF file.")
        try:
            file_bytes = file.read()
            doc = open_fitz_doc(file_bytes, pdf_password)
            output = io.BytesIO()
            doc.save(output, garbage=4, deflate=True, clean=True)
            output.seek(0)
            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            return send_file(output, as_attachment=True, download_name=f"repaired_{now}.pdf", mimetype="application/pdf")
        except PermissionError:
            return render_template("repair.html", error="Wrong PDF password. Please try again.")
        except Exception as e:
            return render_template("repair.html", error=f"Could not repair file: {str(e)}")
    return render_template("repair.html")


# ===============================
# Crop PDF
# ===============================

@app.route("/crop", methods=["GET", "POST"])
def crop():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("crop.html", error="Please select a PDF file.")
        try:
            top    = float(request.form.get("crop_top", 0))
            bottom = float(request.form.get("crop_bottom", 0))
            left   = float(request.form.get("crop_left", 0))
            right  = float(request.form.get("crop_right", 0))
        except ValueError:
            return render_template("crop.html", error="Invalid margin values.")
        apply_to = request.form.get("apply_to", "all")
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("crop.html", error="Wrong PDF password. Please try again.")
        for i, page in enumerate(doc):
            if apply_to == "odd" and (i + 1) % 2 == 0:
                continue
            if apply_to == "even" and (i + 1) % 2 != 0:
                continue
            r = page.rect
            new_rect = fitz.Rect(r.x0 + left, r.y0 + top, r.x1 - right, r.y1 - bottom)
            page.set_cropbox(new_rect)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        doc.save(output, garbage=4, deflate=True)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"cropped_{now}.pdf", mimetype="application/pdf")
    return render_template("crop.html")


# ===============================
# Crop PDF — Visual (drag selection)
# ===============================
@app.route("/crop-visual", methods=["POST"])
def crop_visual():
    file = request.files.get("pdf_file")
    pdf_password = request.form.get("pdf_password", "").strip()
    if not file:
        return "No file provided", 400
    try:
        x1 = float(request.form.get("x1", 0))
        y1 = float(request.form.get("y1", 0))
        x2 = float(request.form.get("x2", 0))
        y2 = float(request.form.get("y2", 0))
    except ValueError:
        return "Invalid coordinates", 400
    if x2 <= x1 or y2 <= y1:
        return "Invalid crop region", 400
    file_bytes = file.read()
    try:
        doc = open_fitz_doc(file_bytes, pdf_password)
    except PermissionError:
        return "Wrong PDF password", 403
    crop_rect = fitz.Rect(x1, y1, x2, y2)
    for page in doc:
        # Clamp to page bounds
        pr = page.rect
        clamped = fitz.Rect(
            max(x1, pr.x0), max(y1, pr.y0),
            min(x2, pr.x1), min(y2, pr.y1)
        )
        if clamped.is_valid and clamped.width > 0 and clamped.height > 0:
            page.set_cropbox(clamped)
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output = io.BytesIO()
    doc.save(output, garbage=4, deflate=True)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f"cropped_{now}.pdf", mimetype="application/pdf")


# ===============================
# Canvas image → PDF (for crop+text export)
# ===============================
@app.route("/canvas-to-pdf", methods=["POST"])
def canvas_to_pdf():
    import base64
    from PIL import Image
    image_data = request.form.get("image_data", "")
    if not image_data or "," not in image_data:
        return "Invalid image data", 400
    try:
        img_bytes = base64.b64decode(image_data.split(",")[1])
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        pdf_buf = io.BytesIO()
        img.save(pdf_buf, "PDF", resolution=150)
        pdf_buf.seek(0)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return send_file(pdf_buf, as_attachment=True, download_name=f"cropped_text_{now}.pdf", mimetype="application/pdf")
    except Exception as e:
        return f"Error: {str(e)}", 500


# ===============================
# Redact PDF
# ===============================

@app.route("/redact", methods=["GET", "POST"])
def redact():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        redact_text = request.form.get("redact_text", "").strip()
        redact_color = request.form.get("redact_color", "black")
        if not file:
            return render_template("redact.html", error="Please select a PDF file.")
        if not redact_text:
            return render_template("redact.html", error="Please enter text to redact.")
        terms = [t.strip() for t in redact_text.splitlines() if t.strip()]
        fill_color = (0, 0, 0) if redact_color == "black" else (1, 1, 1)
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("redact.html", error="Wrong PDF password. Please try again.")
        for page in doc:
            for term in terms:
                rects = page.search_for(term)
                for rect in rects:
                    page.add_redact_annot(rect, fill=fill_color)
            page.apply_redactions()
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        doc.save(output, garbage=4, deflate=True)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"redacted_{now}.pdf", mimetype="application/pdf")
    return render_template("redact.html")


# ===============================
# PDF to PDF/A
# ===============================

@app.route("/pdf-to-pdfa", methods=["GET", "POST"])
def pdf_to_pdfa():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("pdf_to_pdfa.html", error="Please select a PDF file.")
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("pdf_to_pdfa.html", error="Wrong PDF password. Please try again.")
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        # Save with PDF/A-compatible settings (clean, no encryption, embed fonts)
        doc.save(output, garbage=4, deflate=True, clean=True, linear=True)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"pdfa_{now}.pdf", mimetype="application/pdf")
    return render_template("pdf_to_pdfa.html")


# ===============================
# Sign PDF
# ===============================

@app.route("/sign", methods=["GET", "POST"])
def sign():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        sig_file = request.files.get("signature_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        sign_pages = request.form.get("sign_pages", "all")
        sign_position = request.form.get("sign_position", "bottom-right")
        if not file:
            return render_template("sign.html", error="Please select a PDF file.")
        if not sig_file:
            return render_template("sign.html", error="Please upload a signature image.")
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("sign.html", error="Wrong PDF password. Please try again.")
        sig_bytes = sig_file.read()
        sig_w, sig_h = 150, 60  # signature size in points
        margin = 20
        for i, page in enumerate(doc):
            if sign_pages == "first" and i != 0:
                continue
            if sign_pages == "last" and i != len(doc) - 1:
                continue
            r = page.rect
            if sign_position == "bottom-right":
                rect = fitz.Rect(r.width - sig_w - margin, r.height - sig_h - margin, r.width - margin, r.height - margin)
            elif sign_position == "bottom-left":
                rect = fitz.Rect(margin, r.height - sig_h - margin, margin + sig_w, r.height - margin)
            elif sign_position == "bottom-center":
                rect = fitz.Rect((r.width - sig_w) / 2, r.height - sig_h - margin, (r.width + sig_w) / 2, r.height - margin)
            else:
                rect = fitz.Rect(r.width - sig_w - margin, r.height - sig_h - margin, r.width - margin, r.height - margin)
            page.insert_image(rect, stream=sig_bytes, overlay=True)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        doc.save(output, garbage=4, deflate=True)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"signed_{now}.pdf", mimetype="application/pdf")
    return render_template("sign.html")


# ===============================
# PDF to Word
# ===============================

@app.route("/pdf-to-word", methods=["GET", "POST"])
def pdf_to_word():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("pdf_to_word.html", error="Please select a PDF file.")
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("pdf_to_word.html", error="Wrong PDF password. Please try again.")
        from docx import Document as DocxDocument
        from docx.shared import Pt
        word_doc = DocxDocument()
        for i, page in enumerate(doc):
            text = page.get_text()
            if i > 0:
                word_doc.add_page_break()
            for line in text.split("\n"):
                para = word_doc.add_paragraph(line)
                if para.runs:
                    para.runs[0].font.size = Pt(11)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        word_doc.save(output)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"converted_{now}.docx",
                         mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    return render_template("pdf_to_word.html")


# ===============================
# Word to PDF
# ===============================

def _docx_bytes_to_story(file_bytes):
    """Convert docx bytes → reportlab story with formatting, tables, header images."""
    from docx import Document as DocxDocument
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph as DocxPara
    from docx.table import Table as DocxTable
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.platypus import Image as RLImage
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.lib.units import inch

    EMU = 914400          # EMU per inch
    TWIP = 1440           # twips per inch
    MAX_W = 6.5 * inch
    PAGE_W = 8.5 * inch
    MARGIN = 1.0 * inch

    styles = getSampleStyleSheet()
    word_doc = DocxDocument(io.BytesIO(file_bytes))

    def run_to_markup(run):
        txt = run.text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        if not txt:
            return ""
        if run.bold and run.italic: return f"<b><i>{txt}</i></b>"
        if run.bold:                return f"<b>{txt}</b>"
        if run.italic:              return f"<i>{txt}</i>"
        if run.underline:           return f"<u>{txt}</u>"
        return txt

    def get_align(dp):
        try:
            a = dp.alignment
            if a == 1: return TA_CENTER
            if a == 2: return TA_RIGHT
        except Exception:
            pass
        return TA_LEFT

    def para_to_rl(dp, max_width=None):
        markup = "".join(run_to_markup(r) for r in dp.runs)
        if not markup.strip():
            return None
        align = get_align(dp)
        fsz = 10
        try:
            for run in dp.runs:
                if run.font.size:
                    fsz = run.font.size.pt
                    break
        except Exception:
            pass
        is_heading = dp.style.name.startswith("Heading")
        if is_heading:
            try:
                lvl = int(dp.style.name.split()[-1])
            except Exception:
                lvl = 1
            base = styles.get(f"Heading{min(lvl,3)}", styles["Heading1"])
            st = ParagraphStyle("h", parent=base, alignment=align)
        else:
            st = ParagraphStyle("n", parent=styles["Normal"], fontSize=fsz,
                                leading=fsz*1.3, alignment=align)
        return Paragraph(markup, st)

    def get_images(elem, max_w=MAX_W, max_h=None, part_obj=None):
        """Extract images. part_obj = owning OPC part (header/footer/body differ)."""
        imgs = []
        # Try the passed part first, then the main doc part
        sources = []
        if part_obj is not None:
            sources.append(part_obj)
        sources.append(word_doc.part)
        for drawing in elem.iter(qn("w:drawing")):
            blip = drawing.find(".//" + qn("a:blip"))
            if blip is None: continue
            r_embed = blip.get(qn("r:embed"))
            if not r_embed: continue
            img_part = None
            for src in sources:
                try:
                    candidate = src.related_parts[r_embed]
                    if hasattr(candidate, "blob") and getattr(candidate, "content_type", "").startswith("image/"):
                        img_part = candidate
                        break
                except (KeyError, AttributeError):
                    pass
            if img_part is None: continue
            extent = drawing.find(".//" + qn("wp:extent"))
            if extent is not None:
                w_pt = int(extent.get("cx", 0)) / EMU * inch
                h_pt = int(extent.get("cy", 0)) / EMU * inch
            else:
                w_pt, h_pt = 2*inch, 1.5*inch
            if max_h and h_pt > max_h:
                w_pt = w_pt * (max_h / h_pt); h_pt = max_h
            if w_pt > max_w:
                h_pt = h_pt * (max_w / w_pt); w_pt = max_w
            try:
                imgs.append(RLImage(io.BytesIO(img_part.blob), width=w_pt, height=h_pt))
            except Exception:
                pass
        return imgs

    def get_tbl_col_widths(tbl_elem):
        """Read actual column widths from tblGrid (in twips → inches)."""
        widths = []
        for gc in tbl_elem.findall(".//" + qn("w:gridCol")):
            w = gc.get(qn("w:w"))
            widths.append(float(w) / TWIP * inch if w else None)
        return widths if widths else None

    def process_elem(elem, story, max_w=MAX_W):
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

        if tag == "p":
            dp = DocxPara(elem, word_doc)
            imgs = get_images(elem, max_w=max_w)
            for img in imgs:
                story.append(img)
                story.append(Spacer(1, 3))
            p = para_to_rl(dp, max_width=max_w)
            if p:
                story.append(p)
                story.append(Spacer(1, 2))

        elif tag == "tbl":
            tbl = DocxTable(elem, word_doc)
            # Get actual column widths
            raw_widths = get_tbl_col_widths(elem)
            ncols = max((len(row.cells) for row in tbl.rows), default=1)
            if raw_widths and len(raw_widths) == ncols:
                total = sum(w for w in raw_widths if w)
                scale = MAX_W / total if total > 0 else 1
                cw = [w * scale for w in raw_widths]
            else:
                cw = [MAX_W / ncols] * ncols

            data = []
            for ri, row in enumerate(tbl.rows):
                row_data = []
                seen_row = set()   # only track horizontal merges within THIS row
                for ci, cell in enumerate(row.cells):
                    cid = id(cell._tc)
                    if cid in seen_row:
                        row_data.append([Paragraph(" ", styles["Normal"])])
                        continue
                    seen_row.add(cid)
                    cell_story = []
                    cell_imgs = get_images(cell._tc, max_w=cw[ci] if ci < len(cw) else 1.5*inch, max_h=1.2*inch)
                    cell_story.extend(cell_imgs)
                    for cp in cell.paragraphs:
                        markup = "".join(run_to_markup(r) for r in cp.runs)
                        if markup.strip():
                            cell_story.append(Paragraph(markup, styles["Normal"]))
                    row_data.append(cell_story if cell_story else [Paragraph(" ", styles["Normal"])])
                data.append(row_data)

            if data:
                t = Table(data, colWidths=cw, repeatRows=1)
                t.setStyle(TableStyle([
                    ("GRID",          (0,0),(-1,-1), 0.5, colors.black),
                    ("FONTSIZE",      (0,0),(-1,-1), 9),
                    ("TOPPADDING",    (0,0),(-1,-1), 4),
                    ("BOTTOMPADDING",(0,0),(-1,-1),  4),
                    ("LEFTPADDING",   (0,0),(-1,-1), 5),
                    ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
                    ("BACKGROUND",    (0,0),(-1,0),  colors.lightgrey),
                ]))
                story.append(t)
                story.append(Spacer(1, 8))

    story = []

    # ── 1. Header section (logo lives here) ──────────────────────────────
    try:
        hdr = word_doc.sections[0].header
        hdr_story = []
        # Extract header images using hdr.part (not word_doc.part)
        hdr_imgs = get_images(hdr._element, max_w=MAX_W, part_obj=hdr.part)
        hdr_story.extend(hdr_imgs)
        # Extract header text paragraphs
        for child in hdr._element:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "p":
                dp = DocxPara(child, word_doc)
                markup = "".join(run_to_markup(r) for r in dp.runs)
                if markup.strip():
                    hdr_story.append(Paragraph(markup, para_to_rl(dp) and ParagraphStyle("hn", parent=styles["Normal"]) or styles["Normal"]))
        if hdr_story:
            story.extend(hdr_story)
            story.append(HRFlowable(width=MAX_W, thickness=1, color=colors.black))
            story.append(Spacer(1, 6))
    except Exception:
        pass

    # ── 2. Body ───────────────────────────────────────────────────────────
    for child in word_doc.element.body:
        process_elem(child, story, max_w=MAX_W)

    return story


@app.route("/word-to-pdf", methods=["GET", "POST"])
def word_to_pdf():
    if request.method == "POST":
        import tempfile, subprocess

        file = request.files.get("pdf_file")
        if not file:
            return render_template("word_to_pdf.html", error="Please select a Word file.")

        filename = file.filename
        ext = os.path.splitext(filename)[1].lower()
        file_bytes = file.read()

        # .doc → .docx first via macOS textutil
        if ext == ".doc":
            with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as tmp:
                tmp.write(file_bytes); tmp_path = tmp.name
            docx_path = tmp_path + ".docx"
            r = subprocess.run(["textutil", "-convert", "docx", "-output", docx_path, tmp_path],
                               capture_output=True, timeout=30)
            os.unlink(tmp_path)
            if r.returncode != 0 or not os.path.exists(docx_path):
                return render_template("word_to_pdf.html", error="Could not read .doc file.")
            with open(docx_path, "rb") as f:
                file_bytes = f.read()
            os.unlink(docx_path)

        try:
            import mammoth, base64
            from xhtml2pdf import pisa
            from docx import Document as DocxDoc
            from docx.oxml.ns import qn as dqn

            # ── Extract header images from Word header section ──
            word_doc = DocxDoc(io.BytesIO(file_bytes))
            header_html = ""
            try:
                hdr = word_doc.sections[0].header
                for drawing in hdr._element.iter(dqn("w:drawing")):
                    blip = drawing.find(".//" + dqn("a:blip"))
                    if blip is None: continue
                    r_embed = blip.get(dqn("r:embed"))
                    try:
                        part = hdr.part.related_parts[r_embed]
                        if not part.content_type.startswith("image/"): continue
                        extent = drawing.find(".//" + dqn("wp:extent"))
                        w_px = int(int(extent.get("cx", 0)) / 914400 * 96) if extent is not None else 200
                        h_px = int(int(extent.get("cy", 0)) / 914400 * 96) if extent is not None else 60
                        b64 = base64.b64encode(part.blob).decode()
                        header_html += (f'<img src="data:{part.content_type};base64,{b64}" '
                                        f'width="{w_px}" height="{h_px}" style="max-width:100%">')
                    except (KeyError, AttributeError):
                        continue
            except Exception:
                pass

            # ── mammoth: docx → HTML with base64 inline images ──
            def img_converter(image):
                with image.open() as f:
                    img_bytes = f.read()
                b64 = base64.b64encode(img_bytes).decode()
                return {"src": f"data:{image.content_type};base64,{b64}"}

            result = mammoth.convert_to_html(
                io.BytesIO(file_bytes),
                convert_image=mammoth.images.img_element(img_converter)
            )
            html_body = result.value

            # ── Inject column widths directly into <td>/<th> elements ──
            try:
                from docx.oxml.ns import qn as dqn2
                import re

                tables_in_doc = word_doc.tables

                def inject_col_widths(html_body, tables_in_doc):
                    tbl_idx = 0
                    result_parts = []
                    # Split on <table...> tags
                    segments = re.split(r'(<table(?:[^>]*)>)', html_body)
                    i = 0
                    while i < len(segments):
                        seg = segments[i]
                        if re.match(r'<table', seg) and tbl_idx < len(tables_in_doc):
                            # Get column widths for this table
                            tbl = tables_in_doc[tbl_idx]
                            grid = tbl._tbl.findall(".//" + dqn2("w:gridCol"))
                            raw_w = [float(gc.get(dqn2("w:w"), 0)) for gc in grid]
                            total = sum(raw_w) or 1
                            pcts = [w / total * 100 for w in raw_w]
                            tbl_idx += 1
                            result_parts.append(seg)
                            # Find the next </table> chunk and inject widths into first row
                            i += 1
                            tbl_content = []
                            depth = 1
                            while i < len(segments) and depth > 0:
                                s = segments[i]
                                depth += len(re.findall(r'<table', s)) - len(re.findall(r'</table>', s))
                                tbl_content.append(s)
                                i += 1
                            tbl_html = "".join(tbl_content)
                            # Add width to each <td> and <th> using per-row cell index
                            col_counter = [0]
                            in_row = [False]
                            def replace_cell(m):
                                tag = m.group(1)  # td or th
                                attrs = m.group(2)
                                ci = col_counter[0] % len(pcts) if pcts else 0
                                w = pcts[ci] if ci < len(pcts) else 0
                                col_counter[0] += 1
                                return f'<{tag}{attrs} style="width:{w:.1f}%">'
                            def reset_row(m):
                                col_counter[0] = 0
                                return m.group(0)
                            tbl_html = re.sub(r'<tr[^>]*>', reset_row, tbl_html)
                            tbl_html = re.sub(r'<(t[dh])([^>]*)>', replace_cell, tbl_html)
                            result_parts.append(tbl_html)
                            continue
                        else:
                            result_parts.append(seg)
                        i += 1
                    return "".join(result_parts)

                html_body = inject_col_widths(html_body, tables_in_doc)
            except Exception:
                pass

            hr = '<hr style="border:1px solid #555; margin:6pt 0;">' if header_html else ""
            html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  @page {{ margin: 1.5cm 1.5cm; }}
  body {{ font-family: Arial, sans-serif; font-size: 11pt; }}
  table {{ border-collapse: collapse; width: 100%; margin: 6pt 0; table-layout: fixed; }}
  td, th {{ border: 1px solid #333; padding: 4pt 5pt; font-size: 10pt; vertical-align: middle; word-wrap: break-word; }}
  img {{ max-width: 100%; height: auto; }}
  h1,h2,h3 {{ margin: 4pt 0; }}
  p {{ margin: 2pt 0; line-height: 1.4; }}
  strong {{ font-weight: bold; }}
  .header-block {{ text-align: center; margin-bottom: 4pt; }}
</style>
</head><body>
<div class="header-block">{header_html}</div>
{hr}
{html_body}
</body></html>"""

            output = io.BytesIO()
            status = pisa.CreatePDF(html, dest=output)
            if status.err:
                raise Exception("PDF generation failed")
            output.seek(0)
            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            return send_file(output, as_attachment=True, download_name=f"converted_{now}.pdf",
                             mimetype="application/pdf")

        except ImportError:
            return render_template("word_to_pdf.html",
                error="Missing library. Run: pip install mammoth xhtml2pdf")
        except Exception as e:
            return render_template("word_to_pdf.html", error=f"Conversion error: {str(e)}")

    return render_template("word_to_pdf.html")


# ===============================
# PDF to PowerPoint
# ===============================

@app.route("/pdf-to-powerpoint", methods=["GET", "POST"])
def pdf_to_powerpoint():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("pdf_to_powerpoint.html", error="Please select a PDF file.")
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("pdf_to_powerpoint.html", error="Wrong PDF password. Please try again.")
        from pptx import Presentation
        from pptx.util import Inches, Pt
        prs = Presentation()
        prs.slide_width  = Inches(10)
        prs.slide_height = Inches(7.5)
        blank_layout = prs.slide_layouts[6]
        for i, page in enumerate(doc):
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)
            img_buf = io.BytesIO(pix.tobytes("png"))
            slide = prs.slides.add_slide(blank_layout)
            slide.shapes.add_picture(img_buf, 0, 0, prs.slide_width, prs.slide_height)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        prs.save(output)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"converted_{now}.pptx",
                         mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    return render_template("pdf_to_powerpoint.html")


# ===============================
# PowerPoint to PDF
# ===============================

@app.route("/powerpoint-to-pdf", methods=["GET", "POST"])
def powerpoint_to_pdf():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        if not file:
            return render_template("powerpoint_to_pdf.html", error="Please select a PowerPoint file.")
        from pptx import Presentation
        from pptx.util import Inches
        try:
            prs = Presentation(file)
        except Exception as e:
            return render_template("powerpoint_to_pdf.html", error=f"Could not read file: {str(e)}")
        # Render each slide as image then build PDF
        writer = PdfWriter()
        for slide in prs.slides:
            # Create a blank page at slide dimensions
            slide_w = float(prs.slide_width) / 914400 * 72  # EMU -> points
            slide_h = float(prs.slide_height) / 914400 * 72
            tmp_doc = fitz.open()
            page = tmp_doc.new_page(width=slide_w, height=slide_h)
            # Add slide title text if available
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        txt = para.text.strip()
                        if txt:
                            page.insert_text(fitz.Point(36, 72), txt, fontsize=14)
                            break
                    break
            page_buf = io.BytesIO()
            tmp_doc.save(page_buf)
            page_buf.seek(0)
            reader = PdfReader(page_buf)
            writer.add_page(reader.pages[0])
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"converted_{now}.pdf", mimetype="application/pdf")
    return render_template("powerpoint_to_pdf.html")


# ===============================
# PDF to Excel
# ===============================

@app.route("/pdf-to-excel", methods=["GET", "POST"])
def pdf_to_excel():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        if not file:
            return render_template("pdf_to_excel.html", error="Please select a PDF file.")
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("pdf_to_excel.html", error="Wrong PDF password. Please try again.")
        import openpyxl
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for i, page in enumerate(doc):
            ws = wb.create_sheet(title=f"Page {i+1}")
            blocks = page.get_text("blocks")
            row = 1
            for block in blocks:
                text = block[4].strip()
                if text:
                    for line in text.split("\n"):
                        if line.strip():
                            ws.cell(row=row, column=1, value=line.strip())
                            row += 1
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"converted_{now}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return render_template("pdf_to_excel.html")


# ===============================
# Excel to PDF
# ===============================

@app.route("/excel-to-pdf", methods=["GET", "POST"])
def excel_to_pdf():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        if not file:
            return render_template("excel_to_pdf.html", error="Please select an Excel file.")
        import openpyxl
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
        from reportlab.lib.units import inch
        try:
            wb = openpyxl.load_workbook(file, data_only=True)
        except Exception as e:
            return render_template("excel_to_pdf.html", error=f"Could not read Excel file: {str(e)}")
        styles = getSampleStyleSheet()
        story = []
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            story.append(Paragraph(f"Sheet: {sheet}", styles['Heading2']))
            data = []
            for row in ws.iter_rows(values_only=True):
                data.append([str(c) if c is not None else "" for c in row])
            if data:
                t = Table(data, repeatRows=1)
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.grey),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.black),
                    ('FONTSIZE', (0,0), (-1,-1), 8),
                    ('PADDING', (0,0), (-1,-1), 4),
                ]))
                story.append(t)
            story.append(Spacer(1, 12))
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        pdf = SimpleDocTemplate(output, pagesize=letter,
                                rightMargin=0.5*inch, leftMargin=0.5*inch,
                                topMargin=0.5*inch, bottomMargin=0.5*inch)
        pdf.build(story)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"converted_{now}.pdf", mimetype="application/pdf")
    return render_template("excel_to_pdf.html")


# ===============================
# HTML to PDF
# ===============================

@app.route("/html-to-pdf", methods=["GET", "POST"])
def html_to_pdf():
    if request.method == "POST":
        html_content = request.form.get("html_content", "").strip()
        if not html_content:
            return render_template("html_to_pdf.html", error="Please enter HTML content.")
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        import re
        # Strip tags for simple text extraction
        clean_text = re.sub(r'<[^>]+>', '\n', html_content)
        clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()
        styles = getSampleStyleSheet()
        story = []
        for line in clean_text.split("\n"):
            if line.strip():
                story.append(Paragraph(line.strip(), styles['Normal']))
                story.append(Spacer(1, 4))
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        pdf = SimpleDocTemplate(output, pagesize=letter,
                                rightMargin=inch, leftMargin=inch,
                                topMargin=inch, bottomMargin=inch)
        pdf.build(story)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"html_{now}.pdf", mimetype="application/pdf")
    return render_template("html_to_pdf.html")


# ===============================
# Edit PDF (Add Text)
# ===============================

@app.route("/edit-pdf", methods=["GET", "POST"])
def edit_pdf():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        add_text = request.form.get("add_text", "").strip()
        text_position = request.form.get("text_position", "center")
        try:
            font_size = int(request.form.get("font_size", 14))
        except ValueError:
            font_size = 14
        if not file:
            return render_template("edit_pdf.html", error="Please select a PDF file.")
        if not add_text:
            return render_template("edit_pdf.html", error="Please enter text to add.")
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("edit_pdf.html", error="Wrong PDF password. Please try again.")
        font_path = get_unicode_font()
        archive, base_css = get_font_archive(font_path)
        for page in doc:
            r = page.rect
            lh = font_size + 8
            if text_position == "center":
                box   = fitz.Rect(r.width * 0.1, (r.height - lh) / 2, r.width * 0.9, (r.height + lh) / 2)
                align = "center"
            elif text_position == "top-left":
                box   = fitz.Rect(20, 20, r.width * 0.7, 20 + lh)
                align = "left"
            else:  # bottom-center
                box   = fitz.Rect(0, r.height - 40 - lh, r.width, r.height - 40)
                align = "center"
            css  = base_css + f" p {{ font-size: {font_size}pt; color: black; margin:0; padding:0; text-align:{align}; }}"
            html = f"<p>{add_text}</p>"
            kw = dict(css=css, scale_low=0)
            if archive:
                kw["archive"] = archive
            page.insert_htmlbox(box, html, **kw)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        doc.save(output, garbage=4, deflate=True)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"edited_{now}.pdf", mimetype="application/pdf")
    return render_template("edit_pdf.html")


# ===============================
# AI Summarize PDF
# ===============================

@app.route("/summarize", methods=["GET", "POST"])
def summarize():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        try:
            num_sentences = int(request.form.get("num_sentences", 10))
        except ValueError:
            num_sentences = 10
        if not file:
            return render_template("summarize.html", error="Please select a PDF file.")
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("summarize.html", error="Wrong PDF password. Please try again.")
        # Extract all text
        full_text = "\n".join(page.get_text() for page in doc).strip()
        if not full_text:
            return render_template("summarize.html", error="No extractable text found in this PDF.")
        # Simple extractive summarization: pick sentences by position/length
        import re
        sentences = re.split(r'(?<=[.!?])\s+', full_text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 30]
        # Score: longer sentences near beginning get higher score
        scored = [(len(s) + max(0, 500 - i * 5), s) for i, s in enumerate(sentences)]
        scored.sort(key=lambda x: -x[0])
        top = [s for _, s in scored[:num_sentences]]
        # Restore rough order
        order = {s: i for i, s in enumerate(sentences)}
        top.sort(key=lambda s: order.get(s, 0))
        summary = " ".join(top)
        return render_template("summarize.html", summary=summary, filename=file.filename)
    return render_template("summarize.html")


# ===============================
# Translate PDF
# ===============================

@app.route("/translate", methods=["GET", "POST"])
def translate():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        target_lang = request.form.get("target_lang", "en")
        if not file:
            return render_template("translate.html", error="Please select a PDF file.")
        file_bytes = file.read()
        try:
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("translate.html", error="Wrong PDF password. Please try again.")
        try:
            from deep_translator import GoogleTranslator
        except ImportError:
            return render_template("translate.html", error="Translation library not installed. Run: pip install deep-translator")
        translator = GoogleTranslator(source='auto', target=target_lang)
        font_path = get_unicode_font()
        archive, base_css = get_font_archive(font_path)
        # Build new PDF with translated text
        out_doc = fitz.open()
        for page in doc:
            orig_text = page.get_text().strip()
            if not orig_text:
                out_doc.copy_page(page)
                continue
            # Translate in chunks (GoogleTranslator limit ~5000 chars)
            chunks = [orig_text[i:i+4500] for i in range(0, len(orig_text), 4500)]
            translated_chunks = []
            for chunk in chunks:
                try:
                    translated_chunks.append(translator.translate(chunk))
                except Exception:
                    translated_chunks.append(chunk)
            translated = "\n".join(translated_chunks)
            new_page = out_doc.new_page(width=page.rect.width, height=page.rect.height)
            css  = base_css + " p { font-size: 11pt; color: black; margin: 0 0 4pt 0; }"
            html_parts = "".join(f"<p>{line}</p>" for line in translated.split("\n") if line.strip())
            kw = dict(css=css, scale_low=0)
            if archive:
                kw["archive"] = archive
            new_page.insert_htmlbox(fitz.Rect(36, 36, page.rect.width - 36, page.rect.height - 36),
                                    html_parts, **kw)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output = io.BytesIO()
        out_doc.save(output, garbage=4, deflate=True)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"translated_{now}.pdf", mimetype="application/pdf")
    return render_template("translate.html")


# ===============================
# Word to Image
# ===============================

@app.route("/word-to-image", methods=["GET", "POST"])
def word_to_image():
    if request.method == "POST":
        import tempfile, subprocess, zipfile
        file = request.files.get("pdf_file")
        if not file:
            return render_template("word_to_image.html", error="Please select a Word file.")
        filename = file.filename
        ext = os.path.splitext(filename)[1].lower()
        file_bytes = file.read()

        # .doc → .docx via macOS textutil
        if ext == ".doc":
            with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            docx_path = tmp_path + ".docx"
            r = subprocess.run(
                ["textutil", "-convert", "docx", "-output", docx_path, tmp_path],
                capture_output=True
            )
            os.unlink(tmp_path)
            if r.returncode != 0 or not os.path.exists(docx_path):
                return render_template("word_to_image.html", error="Could not convert .doc file.")
            with open(docx_path, "rb") as f:
                file_bytes = f.read()
            os.unlink(docx_path)

        try:
            import mammoth, base64
            from xhtml2pdf import pisa
            from docx import Document as DocxDoc
            from docx.oxml.ns import qn as dqn2

            word_doc = DocxDoc(io.BytesIO(file_bytes))

            # Extract header images
            header_html = ""
            for section in word_doc.sections:
                hdr = section.header
                for para in hdr.paragraphs:
                    for run in para.runs:
                        for tag in run._r.iter():
                            if tag.tag.endswith("}blip"):
                                rId = tag.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                                if rId and rId in hdr.part.related_parts:
                                    part = hdr.part.related_parts[rId]
                                    if "image" in part.content_type:
                                        b64 = base64.b64encode(part.blob).decode()
                                        ct = part.content_type
                                        header_html += f'<img src="data:{ct};base64,{b64}" style="max-height:80px;max-width:100%;"/>'

            # mammoth → HTML with base64 images
            def img_converter(image):
                with image.open() as f:
                    img_data = f.read()
                b64 = base64.b64encode(img_data).decode()
                return {"src": f"data:{image.content_type};base64,{b64}"}

            result = mammoth.convert_to_html(
                io.BytesIO(file_bytes),
                convert_image=mammoth.images.img_element(img_converter)
            )
            html_body = result.value

            # Inject column widths
            def inject_col_widths(html_body, tables_in_doc):
                tbl_idx = 0
                result_parts = []
                segments = re.split(r'(<table(?:[^>]*)>)', html_body)
                i = 0
                while i < len(segments):
                    seg = segments[i]
                    if re.match(r'<table', seg) and tbl_idx < len(tables_in_doc):
                        tbl = tables_in_doc[tbl_idx]
                        grid = tbl._tbl.findall(".//" + dqn2("w:gridCol"))
                        raw_w = [float(gc.get(dqn2("w:w"), 0)) for gc in grid]
                        total = sum(raw_w) or 1
                        pcts = [w / total * 100 for w in raw_w]
                        tbl_idx += 1
                        result_parts.append(seg)
                        i += 1
                        tbl_content = []
                        depth = 1
                        while i < len(segments) and depth > 0:
                            s = segments[i]
                            depth += len(re.findall(r'<table', s)) - len(re.findall(r'</table>', s))
                            tbl_content.append(s)
                            i += 1
                        tbl_html = "".join(tbl_content)
                        col_counter = [0]
                        def reset_row(m):
                            col_counter[0] = 0
                            return m.group(0)
                        def replace_cell(m):
                            tag = m.group(1); attrs = m.group(2)
                            ci = col_counter[0] % len(pcts) if pcts else 0
                            w = pcts[ci] if ci < len(pcts) else 0
                            col_counter[0] += 1
                            return f'<{tag}{attrs} style="width:{w:.1f}%">'
                        tbl_html = re.sub(r'<tr[^>]*>', reset_row, tbl_html)
                        tbl_html = re.sub(r'<(t[dh])([^>]*)>', replace_cell, tbl_html)
                        result_parts.append(tbl_html)
                        continue
                    else:
                        result_parts.append(seg)
                    i += 1
                return "".join(result_parts)

            try:
                html_body = inject_col_widths(html_body, word_doc.tables)
            except Exception:
                pass

            hr = '<hr style="border:1px solid #555; margin:6pt 0;">' if header_html else ""
            html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  @page {{ margin: 1.5cm 1.5cm; }}
  body {{ font-family: Arial, sans-serif; font-size: 11pt; }}
  table {{ border-collapse: collapse; width: 100%; margin: 6pt 0; table-layout: fixed; }}
  td, th {{ border: 1px solid #333; padding: 4pt 5pt; font-size: 10pt; vertical-align: middle; word-wrap: break-word; }}
  img {{ max-width: 100%; height: auto; }}
  h1,h2,h3 {{ margin: 4pt 0; }}
  p {{ margin: 2pt 0; line-height: 1.4; }}
  strong {{ font-weight: bold; }}
  .header-block {{ text-align: center; margin-bottom: 4pt; }}
</style>
</head><body>
<div class="header-block">{header_html}</div>
{hr}
{html_body}
</body></html>"""

            # Step 1: HTML → PDF (in memory)
            pdf_buf = io.BytesIO()
            status = pisa.CreatePDF(html, dest=pdf_buf)
            if status.err:
                raise Exception("PDF generation failed")
            pdf_buf.seek(0)

            # Step 2: PDF → Images via PyMuPDF
            fmt = request.form.get("fmt", "jpg").lower()
            dpi = int(request.form.get("dpi", "150"))
            zoom = dpi / 72
            mat = fitz.Matrix(zoom, zoom)

            doc = fitz.open(stream=pdf_buf.read(), filetype="pdf")
            images = []
            for i, page in enumerate(doc):
                pix = page.get_pixmap(matrix=mat)
                images.append((f"page_{i+1}.{fmt}", pix.tobytes("jpeg" if fmt == "jpg" else "png")))
            doc.close()

            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            if len(images) == 1:
                mime = "image/jpeg" if fmt == "jpg" else "image/png"
                return send_file(
                    io.BytesIO(images[0][1]),
                    as_attachment=True,
                    download_name=f"word_image_{now}.{fmt}",
                    mimetype=mime
                )
            else:
                zip_buf = io.BytesIO()
                with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for name, data in images:
                        zf.writestr(name, data)
                zip_buf.seek(0)
                return send_file(zip_buf, as_attachment=True,
                                 download_name=f"word_images_{now}.zip",
                                 mimetype="application/zip")

        except ImportError:
            return render_template("word_to_image.html",
                error="Missing library. Run: pip install mammoth xhtml2pdf")
        except Exception as e:
            return render_template("word_to_image.html", error=f"Conversion error: {str(e)}")

    return render_template("word_to_image.html")


# ===============================
# Webpage to PDF
# ===============================

@app.route("/webpage-to-pdf", methods=["GET", "POST"])
def webpage_to_pdf():
    if request.method == "POST":
        import requests as req
        from xhtml2pdf import pisa
        from urllib.parse import urlparse
        from bs4 import BeautifulSoup

        url = request.form.get("url", "").strip()
        if not url:
            return render_template("webpage_to_pdf.html", error="Please enter a URL.")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
            }
            resp = req.get(url, headers=headers, timeout=20, verify=False)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # Get page title
            title = soup.title.string.strip() if soup.title and soup.title.string else url

            # Remove noise tags
            for tag in soup(["script", "style", "noscript", "iframe", "nav",
                              "header", "footer", "aside", "form", "button",
                              "input", "select", "textarea", "svg", "canvas"]):
                tag.decompose()

            # Extract plain text lines — works for any page, no CSS issues
            main = (soup.find("main") or soup.find("article") or
                    soup.find(id="content") or soup.find(id="main") or
                    soup.body)

            raw_text = main.get_text(separator="\n") if main else soup.get_text(separator="\n")

            # Clean up blank lines, limit to 800 lines
            lines = [l.strip() for l in raw_text.splitlines() if l.strip()][:800]

            # Build simple HTML paragraphs
            paras = "".join(f"<p>{l}</p>" for l in lines)

            clean_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  @page {{ margin: 2cm; }}
  body {{ font-family: Arial, sans-serif; font-size: 10pt; line-height: 1.5; color: #111; }}
  p {{ margin: 2pt 0; }}
  .page-title {{ font-size: 16pt; font-weight: bold; margin-bottom: 4pt; border-bottom: 2px solid #e53935; padding-bottom: 4pt; }}
  .page-url {{ font-size: 8pt; color: #888; margin-bottom: 12pt; }}
</style>
</head><body>
<p class="page-title">{title}</p>
<p class="page-url">{url}</p>
{paras}
</body></html>"""

            pdf_buf = io.BytesIO()
            pisa.CreatePDF(clean_html, dest=pdf_buf, encoding="utf-8")
            pdf_buf.seek(0)
            if pdf_buf.getbuffer().nbytes < 100:
                raise Exception("PDF conversion failed — no content could be extracted.")

            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            domain = urlparse(url).netloc.replace("www.", "")
            return send_file(pdf_buf, as_attachment=True,
                             download_name=f"{domain}_{now}.pdf",
                             mimetype="application/pdf")

        except req.exceptions.Timeout:
            return render_template("webpage_to_pdf.html", error="The website took too long to respond.")
        except Exception as e:
            return render_template("webpage_to_pdf.html", error=f"Error: {str(e)}")

    return render_template("webpage_to_pdf.html")


# ===============================
# Extract Images from PDF
# ===============================

@app.route("/extract-images", methods=["GET", "POST"])
def extract_images():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        img_format = request.form.get("img_format", "png").lower()
        min_size = int(request.form.get("min_size", "50") or 50)

        if not file:
            return render_template("extract_images.html", error="Please select a PDF file.")

        try:
            file_bytes = file.read()
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("extract_images.html", error="Wrong PDF password. Please try again.")
        except Exception as e:
            return render_template("extract_images.html", error=f"Could not open PDF: {str(e)}")

        try:
            zip_buffer = io.BytesIO()
            count = 0
            seen_xrefs = set()

            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    for img_index, img in enumerate(page.get_images(full=True)):
                        xref = img[0]
                        if xref in seen_xrefs:
                            continue
                        seen_xrefs.add(xref)
                        try:
                            base = doc.extract_image(xref)
                        except Exception:
                            continue
                        img_bytes = base.get("image")
                        if not img_bytes:
                            continue

                        try:
                            pil = Image.open(io.BytesIO(img_bytes))
                        except Exception:
                            continue

                        # skip tiny images (icons, separators)
                        if pil.width < min_size or pil.height < min_size:
                            continue

                        out = io.BytesIO()
                        if img_format == "jpg":
                            if pil.mode in ("RGBA", "P", "LA"):
                                pil = pil.convert("RGB")
                            pil.save(out, "JPEG", quality=92)
                            ext = "jpg"
                        else:
                            if pil.mode == "CMYK":
                                pil = pil.convert("RGB")
                            pil.save(out, "PNG")
                            ext = "png"
                        out.seek(0)
                        count += 1
                        zf.writestr(f"page{page_num+1}_img{img_index+1}.{ext}", out.read())

            doc.close()

            if count == 0:
                return render_template("extract_images.html",
                    error="No images found in this PDF (or all images were smaller than the minimum size).")

            zip_buffer.seek(0)
            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            return send_file(zip_buffer, as_attachment=True,
                             download_name=f"extracted_images_{now}.zip",
                             mimetype="application/zip")
        except Exception as e:
            return render_template("extract_images.html", error=f"Error extracting images: {str(e)}")

    return render_template("extract_images.html")


# ===============================
# Remove Blank Pages
# ===============================

def _page_ink_ratio(page, dpi=50):
    """Return fraction of non-white pixels on a page (0.0 = fully blank)."""
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
    arr = np.frombuffer(pix.samples, dtype=np.uint8)
    if arr.size == 0:
        return 0.0
    # pixels darker than 245 count as "ink"
    return float(np.count_nonzero(arr < 245)) / float(arr.size)


@app.route("/remove-blank-pages", methods=["GET", "POST"])
def remove_blank_pages():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        action = request.form.get("action", "remove")  # 'scan' or 'remove'
        try:
            sensitivity = float(request.form.get("sensitivity", "0.2") or 0.2)
        except ValueError:
            sensitivity = 0.2
        threshold = sensitivity / 100.0  # percent → ratio

        if not file:
            return render_template("remove_blank_pages.html", error="Please select a PDF file.")

        try:
            file_bytes = file.read()
            doc = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("remove_blank_pages.html", error="Wrong PDF password. Please try again.")
        except Exception as e:
            return render_template("remove_blank_pages.html", error=f"Could not open PDF: {str(e)}")

        try:
            blank_pages = []
            for i in range(len(doc)):
                page = doc[i]
                has_text = bool(page.get_text().strip())
                has_images = bool(page.get_images(full=True))
                if has_text or has_images:
                    continue
                if _page_ink_ratio(page) <= threshold:
                    blank_pages.append(i)

            total = len(doc)

            if action == "scan":
                doc.close()
                return jsonify({
                    "total": total,
                    "blank": [p + 1 for p in blank_pages],
                    "kept": total - len(blank_pages)
                })

            if not blank_pages:
                doc.close()
                return render_template("remove_blank_pages.html",
                    error="No blank pages found — your PDF is already clean!")

            if len(blank_pages) == total:
                doc.close()
                return render_template("remove_blank_pages.html",
                    error="Every page looks blank. Try lowering the sensitivity.")

            doc.delete_pages(blank_pages)
            out = io.BytesIO()
            doc.save(out, garbage=3, deflate=True)
            doc.close()
            out.seek(0)
            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            return send_file(out, as_attachment=True,
                             download_name=f"cleaned_{now}.pdf",
                             mimetype="application/pdf")
        except Exception as e:
            return render_template("remove_blank_pages.html", error=f"Error: {str(e)}")

    return render_template("remove_blank_pages.html")


# ===============================
# Organize PDF (all-in-one page manager)
# ===============================

@app.route("/organize-pdf", methods=["GET", "POST"])
def organize_pdf():
    if request.method == "POST":
        file = request.files.get("pdf_file")
        pdf_password = request.form.get("pdf_password", "").strip()
        # format: "3:90,1:0,2:180"  → pageNumber:rotation
        page_data = request.form.get("page_data", "").strip()

        if not file:
            return render_template("organize_pdf.html", error="Please select a PDF file.")
        if not page_data:
            return render_template("organize_pdf.html", error="No pages selected. Please keep at least one page.")

        try:
            file_bytes = file.read()
            src = open_fitz_doc(file_bytes, pdf_password)
        except PermissionError:
            return render_template("organize_pdf.html", error="Wrong PDF password. Please try again.")
        except Exception as e:
            return render_template("organize_pdf.html", error=f"Could not open PDF: {str(e)}")

        try:
            out_doc = fitz.open()
            for item in page_data.split(","):
                item = item.strip()
                if not item:
                    continue
                if ":" in item:
                    pno_str, rot_str = item.split(":", 1)
                else:
                    pno_str, rot_str = item, "0"
                pno = int(pno_str) - 1
                rot = int(rot_str) % 360
                if pno < 0 or pno >= len(src):
                    continue
                out_doc.insert_pdf(src, from_page=pno, to_page=pno)
                new_page = out_doc[-1]
                if rot:
                    new_page.set_rotation((new_page.rotation + rot) % 360)

            if len(out_doc) == 0:
                src.close(); out_doc.close()
                return render_template("organize_pdf.html", error="No valid pages to save.")

            out = io.BytesIO()
            out_doc.save(out, garbage=3, deflate=True)
            src.close(); out_doc.close()
            out.seek(0)
            now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            return send_file(out, as_attachment=True,
                             download_name=f"organized_{now}.pdf",
                             mimetype="application/pdf")
        except Exception as e:
            return render_template("organize_pdf.html", error=f"Error: {str(e)}")

    return render_template("organize_pdf.html")


if __name__ == "__main__":
    app.run(debug=True)
