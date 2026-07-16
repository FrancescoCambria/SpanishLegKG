# Catalonia Law Scraper and Graph Pipeline

This directory contains the tools and scripts used for fetching, parsing, and building a law citation graph from the **Diari Oficial de la Generalitat de Catalunya (DOGC)** (Official Gazette of the Government of Catalonia) and the **Diputació de Barcelona (CIDO)**.

The workspace is organized to keep primary entry points clear, placing all processing pipeline helpers and intermediate data in dedicated subdirectories.

---

## 📂 Directory Layout

*   🔑 **Core Entry Points (Root)**
    *   [html_parser.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/html_parser.py) - Parses individual laws (HTML pages/PDFs) into structured bilingual Catalan-Spanish JSON files.
    *   [xml_parser.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/xml_parser.py) - Concurrently downloads and parses Akoma Ntoso XML formats from EADOP.
    *   [get_dogc_documents.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/get_dogc_documents.py) - Crawls the official EADOP REST API to retrieve gazette summaries and outputs the base metadata document dataset.
*   ⚙️ [pipeline_scripts/](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts) - Orchestration, analysis, and data-loading scripts.
    *   [parse_cido.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts/parse_cido.py) - Downloads and parses local regulations from Barcelona CIDO API, cross-comparing with DOGC.
*   💾 [data/](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/data) - Crawled databases, output summaries, graph structures, and intermediate files (ignored by git).
*   📂 [old_files/](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/old_files) - Archived historical reference scripts and dataset versions.

---

## 🔑 Core Scripts Deep-Dive

This section explains the architecture and usage of the three primary crawlers and parsers.

### 1. DOGC Document Crawler
**Script**: [get_dogc_documents.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/get_dogc_documents.py)

Fetches the chronological list of issues and metadata of all documents published in the DOGC from the government API.
*   **Key Logic**:
    *   Queries EADOP REST API endpoints sequentially by issue number.
    *   Saves crawl state in a hidden checkpoint file (`data/.dogc_documents.json.checkpoint`) to allow resumption if interrupted.
    *   Collects metadata (title, emitting organism, publication date, type, document URL).
*   **Default Output**: [data/dogc_documents.json](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/data/dogc_documents.json)
*   **Usage**:
    ```bash
    /home/cambria/gram3/.venv/bin/python3 get_dogc_documents.py --start 9000 --limit 100
    ```

### 2. Structured HTML Parser
**Script**: [html_parser.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/html_parser.py) (formerly *structured_parser.py*)

Fetches specific document HTML/PDF targets, cleans them using BeautifulSoup, detects article boundaries, extracts metadata (ELI URI, emitting organisms, CVE, sections, etc.), and generates a structured bilingual Catalan/Spanish JSON representation.
*   **Key Logic**:
    *   Loads and parses HTML payloads or OCR-extracted PDF text wrappers.
    *   Uses regex compilation with word-written ordinals (such as `primero`, `segundo`, `cinquè`) sorted by length descending to prevent partial match issues (e.g. `primer` matching before `primero`).
    *   Recognizes article, section, and provision headers (up to ordinal 50) and aggregates their text bodies.
*   **Default Output Directory**: [data/structured_output/](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/data/structured_output)
*   **Usage**:
    ```bash
    /home/cambria/gram3/.venv/bin/python3 html_parser.py --urls law_urls.txt --limit 10
    ```

### 3. Converted XML Parser
**Script**: [xml_parser.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/xml_parser.py) (formerly *pipeline_scripts/batch_parse_xml.py*)

Concurrently downloads and parses Akoma Ntoso XML formats from EADOP, saving raw XML files to `data/xml_output/` and structured bilingual JSONs.
*   **Key Logic**:
    *   Uses `ThreadPoolExecutor` to download and parse Catalan and Spanish version XMLs simultaneously.
    *   Leverages the highly structured Akoma Ntoso schema structure to extract articles and hierarchical elements cleanly, falling back to regex parsing where necessary.
    *   Correlates the Catalan and Spanish equivalents by aligning their node IDs.
*   **Default Output Directory**: [data/structured_output/](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/data/structured_output)
*   **Usage**:
    ```bash
    /home/cambria/gram3/.venv/bin/python3 xml_parser.py --threads 8 --limit 50
    ```

---

## ⚙️ Additional Pipeline Scripts

All helper and step-specific scripts are located inside [pipeline_scripts/](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts):

1.  **CIDO API & Matching**:
    *   [parse_cido.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts/parse_cido.py) - Connects to Barcelona CIDO API, resolves related documents, checks overlaps with the local DOGC reference database (by ID and normalized titles), downloads PDFs, and extracts sections via Docling OCR + html_parser.
2.  **Batch Processing**:
    *   [batch_parse_recent.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts/batch_parse_recent.py) - Concurrently parses documents matching specific years in the base dataset using thread pools.
    *   [process_laws_pipeline.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts/process_laws_pipeline.py) - Fetches law URLs from Socrata Open Data and processes them.
    *   [extract_spanish_pdf_text.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts/extract_spanish_pdf_text.py) - Extracts and isolates Spanish version texts from legacy PDF files using Docling OCR.

3.  **Graph Construction and Loading**:
    *   [build_law_graph.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts/build_law_graph.py) - Extracted nodes, sections, affectations, descriptors, and citation links to form the Cypher graph data. Saves results to `data/prepared_graph_data`.
    *   [load_to_neo4j.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts/load_to_neo4j.py) - Populates a Neo4j database instance at port `23010` with the prepared graph files.
    *   [extract_subgraph.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts/extract_subgraph.py) & [extract_large_subgraph.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts/extract_large_subgraph.py) - Extracts subgraphs of defined node count or file sizes from the Neo4j database.

4.  **Data Cleanliness & Audits**:
    *   [analyze_scraped_documents.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts/analyze_scraped_documents.py) - Audits the dataset for completeness (missing IDs, URLs, dates).
    *   [fill_document_types.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts/fill_document_types.py) - Infers document types (e.g. DECRET, RESOLUCIÓ) from document titles where missing.
    *   [generate_stats_csv.py](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/pipeline_scripts/generate_stats_csv.py) - Extracts publication statistics per year, document type, and gazette issue.
