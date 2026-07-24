# Catalonia Law Graph Pipeline

This directory contains the modular pipeline scripts structured to reflect the architecture defined in [`graph_data/graph_construction_pipeline.txt`](file:///home/cambria/gram3/LawGraph/Spain/Catalonia/graph_data/graph_construction_pipeline.txt).

---

## 📐 Architecture & Execution Flow

```
pipeline/
├── pipeline_manager.py                 # Main pipeline orchestrator (runs Step 1 & Step 2)
├── step1_dogc/                         # Step 1: Refresh DoGC Pipeline
│   ├── 01_scrape_dogc.py               # 1.1 & 1.2: Check & scrape new DoGC issues + document list
│   ├── 02_parse_xml_structured.py       # 1.3: Parse XML files for text, descriptors & affectations
│   └── 03_extract_dogc_citations.py     # 1.4: Apply regex for additional text & section citations
├── step2_cido/                         # Step 2: Refresh CIDO Pipeline
│   ├── 01_scrape_cido_and_resolve.py   # 2.1, 2.2, 2.3: Check CIDO docs + matèria, map to DOGC & resolve unresolved
│   ├── 02_download_parse_bop_pdfs.py   # 2.4: Download & parse BoPB/BoPG/BoPL/BoPT PDFs to structured text
│   └── 03_extract_bop_citations.py     # 2.5: Apply regex to find section & article citations in BOP text
└── graph_builder/                      # Node & Edge Construction
    └── build_graph.py                  # 1.5 & 2.6: Create final nodes & edges in graph_data/
```

---

## 🚀 Quick Start

### 1. Run Complete Pipeline (Step 1 + Step 2 + Graph Build)
```bash
/home/cambria/gram3/.venv/bin/python3 pipeline/pipeline_manager.py --mode update
```

### 2. Run Step 1 Only (DoGC Refresh)
```bash
/home/cambria/gram3/.venv/bin/python3 pipeline/pipeline_manager.py --step step1_dogc
```

### 3. Run Step 2 Only (CIDO & Provincial Bulletins Refresh)
```bash
/home/cambria/gram3/.venv/bin/python3 pipeline/pipeline_manager.py --step step2_cido
```

### 4. Rebuild Knowledge Graph JSONs
```bash
/home/cambria/gram3/.venv/bin/python3 pipeline/pipeline_manager.py --step build_graph
```
