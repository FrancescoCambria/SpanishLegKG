# National Spanish Law Scraper (BOE)

This directory contains the tools and scripts used for fetching, parsing, and storing national Spanish legal documents from the **Boletín Oficial del Estado (BOE)** using its official OpenData REST API.

---

## 📂 Directory Layout

*   🔑 **Core Entry Point**
    *   [get_boe_documents.py](file:///home/cambria/gram3/LawGraph/Spain/National/get_boe_documents.py) - Chronologically downloads and incrementally updates national laws and regulations.
*   💾 [data/](file:///home/cambria/gram3/LawGraph/Spain/National/data) - Crawled JSON database, downloaded XML files, and log files (ignored by git).
    *   [boe_documents.json](file:///home/cambria/gram3/LawGraph/Spain/National/data/boe_documents.json) - The main database containing metadata of all downloaded documents.
    *   `[year]/xml/` - Directories containing raw XML document files organized by year (e.g. `2026/xml/`).
    *   `[year]/pdf/` - Directories containing raw PDF document files organized by year (if enabled).

---

## 🚀 Usage

The crawler uses the Python interpreter inside the project's virtual environment:

```bash
/home/cambria/gram3/.venv/bin/python3 get_boe_documents.py [options]
```

### 1. Default (Incremental Update)
If run without any date parameters, the script automatically parses the existing database [boe_documents.json](file:///home/cambria/gram3/LawGraph/Spain/National/data/boe_documents.json), identifies the most recent publication date, and resumes downloading from that day up to the current date:
```bash
/home/cambria/gram3/.venv/bin/python3 get_boe_documents.py
```
*If no existing database is found, it defaults to downloading the last 30 days.*

### 2. Specifying Date Ranges
To download documents from a specific date or range:
```bash
# Start from a specific date to today
/home/cambria/gram3/.venv/bin/python3 get_boe_documents.py -s 2026-07-01

# Specific range
/home/cambria/gram3/.venv/bin/python3 get_boe_documents.py -s 2026-06-01 -e 2026-06-30
```

### 3. Filtering by Section
By default, the script only crawls **Section I** (`1` - *Disposiciones generales*), which contains normative legal documents (Leyes, Reales Decretos, Ordenes). 

You can crawl additional sections or all sections using the `--sections` option:
```bash
# Crawl Section I and Section III (Otras disposiciones)
/home/cambria/gram3/.venv/bin/python3 get_boe_documents.py --sections 1,3

# Crawl all sections (including appointments, personnel, judicial notices, and announcements)
/home/cambria/gram3/.venv/bin/python3 get_boe_documents.py --sections all
```

### 4. Downloading PDFs
By default, the script downloads raw XML files (which contain full text, metadata, and citation linkages) and does not download PDFs. To enable PDF downloads, use the `--download-pdf` flag:
```bash
/home/cambria/gram3/.venv/bin/python3 get_boe_documents.py --download-pdf
```

---

## 🛠️ CLI Arguments Reference

| Argument | Shorthand | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `--start-date` | `-s` | String | *Auto-resume* | Start date (YYYY-MM-DD or YYYYMMDD) |
| `--end-date` | `-e` | String | *Today* | End date (YYYY-MM-DD or YYYYMMDD) |
| `--output` | `-o` | String | `data/boe_documents.json` | Path to output JSON database file |
| `--xml-dir` | `-x` | String | `data/xml` | Directory to save downloaded XMLs |
| `--pdf-dir` | | String | `data/pdf` | Directory to save downloaded PDFs |
| `--limit` | `-l` | Integer | `None` | Max number of daily sumarios to crawl |
| `--delay` | `-d` | Float | `0.2` | Delay between requests in seconds |
| `--sections` | `--sec` | String | `1` | Comma-separated section codes (e.g. `1,3`) or `all` |
| `--no-resume` | | Flag | `False` | Ignore existing database and start fresh |
| `--download-xml` | | Boolean | `True` | Toggle XML file downloads (`--download-xml` / `--no-download-xml`) |
| `--download-pdf` | | Flag | `False` | Download PDF version of documents |
| `--overwrite` | | Flag | `False` | Overwrite existing local files and database records |

---

## 📊 Database Schema

The output file `boe_documents.json` contains an array of objects with the following schema:

```json
[
  {
    "identificador": "BOE-A-2026-15028",
    "control": "2026/11278",
    "titulo": "Real Decreto 562/2026, de 8 de julio, por el que se regula...",
    "fecha_publicacion": "20260710",
    "diario_numero": "167",
    "seccion_codigo": "1",
    "seccion_nombre": "I. Disposiciones generales",
    "departamento_codigo": "7320",
    "departamento_nombre": "MINISTERIO DEL INTERIOR",
    "epigrafe_nombre": "Subvenciones",
    "pdf_url": "https://www.boe.es/boe/dias/2026/07/10/pdfs/BOE-A-2026-15028.pdf",
    "html_url": "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-15028",
    "xml_url": "https://www.boe.es/diario_boe/xml.php?id=BOE-A-2026-15028",
    "xml_path": "2026/xml/BOE-A-2026-15028.xml",
    "pdf_path": "2026/pdf/BOE-A-2026-15028.pdf"  // (if download-pdf was enabled)
  }
]
```
