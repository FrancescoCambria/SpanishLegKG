# Audit Report: Provincial Gazettes (BOPG, BOPT, BOPL) and Open Data Portals vs. CIDO & DOGC

**Date**: July 22, 2026  
**Scope**: Girona (BOPG), Tarragona (BOPT), and Lleida (BOPL) document extractions, Socrata & CKAN Open Data API integration, and cross-matching against CIDO and DOGC datasets.

---

## 1. Executive Summary

This audit evaluated the coverage and alignment of local provincial gazette publications and open data portal resources across Catalonia:
1. **Girona (BOPG) & Tarragona (BOPT)**: Extracted via CKAN API (`dadesobertes.seu-e.cat/api/`) and CIDO open datasets.
2. **Lleida (BOPL)**: Extracted via Socrata Open Data API (`https://[domain]/resource/[dataset_identifier].json`) and CIDO datasets.
3. **Cross-Matching Verification**: Evaluated presence across **CIDO** (886,169 entries / 2.71M sub-documents) and **DOGC** (914,431 official regional documents).

### Key Findings
* **BOPG (Girona) & BOPT (Tarragona)**: **238,922 / 238,949 (99.99%)** documents matched cleanly in CIDO.
* **BOPL (Lleida) Local Gazette & Legal Documents**: **100.00%** of all official local legal categories (*normatives-locals*, *subvencions*, *contractacions*, *oposicions*, *convenis*) matched in CIDO.
* **Socrata Regional Norms (`n6hn-rmy7`)**: **29,792 / 30,977 (96.17%)** of the missing items in CIDO exist directly in our **DOGC** regional legislation dataset.
* **Socrata Internal Procurement Logs (`hb6v-jcbf`)**: 25,814 items represent low-level internal purchasing transaction lines (e.g. surgical equipment cart accessories) rather than gazette notices or legal norms.

---

## 2. Girona (BOPG) & Tarragona (BOPT) Analysis

### Source & Pipeline
* **API**: CKAN API (`https://dadesobertes.seu-e.cat/api/3/action/`) + CIDO Open Data.
* **Script**: `scripts/download_bopg_bopt_documents.py`
* **Dataset Output**: `data/bopg_bopt_documents.json` (195.11 MB, **238,949** unique documents)
* **Matching Script**: `scripts/check_bopg_bopt_in_cido_map.py`
* **Matching Report**: `data/bopg_bopt_cido_matching_report.json`

### Matching Results against CIDO Map
| Gazette | Total Checked | Matched in CIDO | Unmatched | Match Rate |
| :--- | :--- | :--- | :--- | :--- |
| **BOPG (Girona)** | 128,533 | 128,524 | 9 | **99.99%** |
| **BOPT (Tarragona)** | 110,416 | 110,398 | 18 | **99.98%** |
| **Combined** | **238,949** | **238,922** | **27** | **99.99%** |

*Sample Matched Record*:
```json
{
  "document": {
    "id": "bopg_ckan_1",
    "title": "Ordenances fiscals per a l'any 2026",
    "bulletin": "BOPG",
    "date": "2026-07-21",
    "institution": "Ajuntament de Girona",
    "category": "normativa-fiscals",
    "urlHtml": "https://cido.diba.cat/normativa_local/20358989",
    "link_to_text": "https://cido.diba.cat/normativa_local/20358989",
    "is_vigent": true
  },
  "matched_cido_id": "20358989",
  "match_reason": "url_match (link_to_text)"
}
```

---

## 3. Diputació de Lleida (BOPL) Socrata API Integration

### Source & Pipeline
* **API**: Socrata Open Data API (`https://analisi.transparenciacatalunya.cat/resource/[dataset_identifier].json`)
* **Socrata Endpoints**:
  * `ybgg-dgi6.json`: Contractació Pública (PSCP)
  * `hb6v-jcbf.json`: Registre Públic de Contractes
  * `n6hn-rmy7.json`: Normativa del DOGC i Portal Jurídic de Catalunya
  * `exh2-diuf.json`: Registre de Convenis
  * `s9xt-n979.json`: Registre de Subvencions (RAISC)
* **Script**: `scripts/download_bopl_documents.py`
* **Dataset Output**: `data/bopl_documents.json` (115.21 MB, **159,150** unique documents)
* **Matching Script**: `scripts/check_bopl_in_cido_map.py`
* **Matching Report**: `data/bopl_cido_matching_report.json`

### CIDO Matching Results by Category
| Category / Source | Total Checked | Matched in CIDO | Unmatched | Match Rate |
| :--- | :--- | :--- | :--- | :--- |
| **`normatives-locals`** | 78,007 | 78,007 | 0 | **100.00%** |
| **`subvencions`** | 4,992 | 4,992 | 0 | **100.00%** |
| **`contractacions`** | 9,578 | 9,578 | 0 | **100.00%** |
| **`oposicions`** | 6,591 | 6,591 | 0 | **100.00%** |
| **`convenis`** | 459 | 459 | 0 | **100.00%** |
| **Socrata PSCP (`ybgg-dgi6`)** | 2,705 | 1,548 | 1,157 | **57.23%** |
| **Socrata Registre (`hb6v-jcbf`)** | 25,814 | 3 | 25,811 | **0.01%** |
| **Socrata Normativa (`n6hn-rmy7`)** | 30,977 | 3 | 30,974 | **0.01%** |
| **Socrata Convenis (`exh2-diuf`)** | 27 | 0 | 27 | **0.00%** |
| **Total BOPL/Socrata** | **159,150** | **101,181** | **57,969** | **63.58%** |

---

## 4. DOGC Cross-Check of Unmatched Socrata Records

To understand the **57,969 unmatched Socrata entries**, we executed a cross-matching script against our full **DOGC dataset** (914,431 official regional records).

* **Script**: `scripts/check_unmatched_in_dogc.py`
* **Matching Report**: `data/unmatched_dogc_matching_report.json`

### DOGC Matching Results
| Unmatched Socrata Category | Unmatched in CIDO | Matched in DOGC | DOGC Match Rate | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Socrata Normativa (`n6hn-rmy7`)** | 30,977 | **29,792** | **96.17%** | **Regional Generalitat Laws & Decrees** published in DOGC. |
| **Socrata PSCP (`ybgg-dgi6`)** | 2,705 | **1** | **0.04%** | Electronic tender postings on `contractaciopublica.cat`. |
| **Socrata Registre (`hb6v-jcbf`)** | 25,814 | **0** | **0.00%** | Internal municipal/hospital accounting & purchase lines. |
| **Socrata Convenis (`exh2-diuf`)** | 27 | **0** | **0.00%** | Specific inter-institutional agreements. |
| **Total** | **59,523** | **29,793** | **50.05%** | **50% of missing items were Generalitat regional laws in DOGC.** |

### Definitions & Unmatched Record Types

1. **Socrata Contractació PSCP** (`ybgg-dgi6.json`):
   * *Definition*: Open dataset from the **Plataforma de Serveis de Contractació Pública** (Catalan Public Procurement Platform) containing bidding notices, awards, and contract formalization announcements.
   * *Unmatched examples*: Minor municipal tender specs (e.g. *"Subministrament de tauletes tàctils per a signatura biomètrica"*, *"Manteniment de senyalització"*) posted electronically on PSCP but not published in official gazettes.

2. **Socrata Normativa** (`n6hn-rmy7.json`):
   * *Definition*: Open dataset indexing regional laws, decrees, orders, and resolutions from **DOGC** (*Diari Oficial de la Generalitat de Catalunya*) and *Portal Jurídic de Catalunya*.
   * *Why unmatched in CIDO*: CIDO indexes local municipal/provincial notices, while `n6hn-rmy7` contains Generalitat regional laws. **96.17% of them matched directly in our DOGC dataset**.

3. **Socrata Contract Registre** (`hb6v-jcbf.json`):
   * *Definition*: Public Contracts Register (*Registre Públic de Contractes*) containing internal transaction rows for contract execution.
   * *Why unmatched everywhere*: Internal operational purchasing logs (e.g., *"Carro per accessoris de taules quirúrgiques per L'HUAV de Lleida"* or *"Compra de farmàcia"* by Hospital Arnau de Vilanova) that are never published as gazette notices or legal norms.

---

## 5. Pipeline Scripts and Dataset Sitemap

| Script Name | Purpose / Output |
| :--- | :--- |
| **`scripts/download_bopg_bopt_documents.py`** | Fetches BOPG & BOPT documents from CKAN API into `data/bopg_bopt_documents.json`. |
| **`scripts/check_bopg_bopt_in_cido_map.py`** | Cross-checks BOPG & BOPT documents against CIDO; outputs `data/bopg_bopt_cido_matching_report.json`. |
| **`scripts/download_bopl_documents.py`** | Fetches BOPL & Lleida documents from Socrata API into `data/bopl_documents.json`. |
| **`scripts/check_bopl_in_cido_map.py`** | Cross-checks BOPL documents against CIDO; outputs `data/bopl_cido_matching_report.json`. |
| **`scripts/check_unmatched_in_dogc.py`** | Cross-checks unmatched Socrata items against 914,431 DOGC records; outputs `data/unmatched_dogc_matching_report.json`. |
