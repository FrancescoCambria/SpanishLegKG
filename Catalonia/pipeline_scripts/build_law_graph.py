import os
import re
import sys
import json
import argparse
import unicodedata
from tqdm import tqdm

# Ensure we can import other scripts in this directory even if run from elsewhere
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
for d in [script_dir, parent_dir]:
    if d not in sys.path:
        sys.path.append(d)

try:
    from html_parser import fetch_document_from_rest_api, get_spanish_url
    HAS_PARSER = True
except ImportError:
    HAS_PARSER = False

def to_cypher_label(text):
    """
    Sanitizes a document type string to be a valid Cypher label.
    Example: 'DECRET-LLEI' -> 'DecretLlei', 'RESOLUCIÓ' -> 'Resolucio'
    """
    if not text or text.lower() in ["unknown", "other", "none", ""]:
        return "Other"
    
    # Normalize to ASCII (remove accents)
    nfkd_form = unicodedata.normalize('NFKD', text)
    ascii_text = nfkd_form.encode('ASCII', 'ignore').decode('utf-8')
    
    # CamelCase: split by any non-alphanumeric char
    parts = re.split(r'[^a-zA-Z0-9]+', ascii_text)
    camel_parts = [p.capitalize() for p in parts if p]
    if not camel_parts:
        return "Other"
        
    label = "".join(camel_parts)
    # Cypher label rules: cannot start with a digit
    if label[0].isdigit():
        label = "Type_" + label
    return label

def normalize_section_title(title):
    """
    Normalizes a section or article title to simplify comparison.
    Example: 'Article 65' -> 'article65', 'Artículo 1' -> 'article1'
    """
    if not title:
        return ""
    title_lower = title.lower()
    # Normalize ordinals / languages
    title_lower = title_lower.replace("artículo", "article").replace("articulo", "article").replace("art.", "article")
    nfkd_form = unicodedata.normalize('NFKD', title_lower)
    ascii_title = nfkd_form.encode('ASCII', 'ignore').decode('utf-8')
    ascii_title = re.sub(r'[^a-z0-9]', '', ascii_title)
    return ascii_title

def extract_doc_ref(text):
    """
    Extracts target document reference string from citation text, e.g. "Llei 3/1985" or "ACORD GOV/247/2023".
    """
    pat = re.compile(
        r'\b(llei|decret|ordre|acord|resolució|resolucion|edicte|anunci|reial decret)\s+([a-zA-Z0-9\/\-\.]+)',
        re.IGNORECASE
    )
    m = pat.search(text)
    if m:
        return m.group(0).strip()
    return None

def is_valid_article_num(t):
    """
    Checks if a token represents a valid article identifier (e.g. '12', '12-bis', '631-22', 'unic').
    """
    t = t.strip().lower()
    if not t:
        return False
    # Known ordinals
    if t in ["unic", "unico", "primer", "primero", "segon", "segundo", "tercer", "tercero", "quart", "cuarto"]:
        return True
    # Matches numbers, numbers with hyphens/letters (e.g. 631-22, 12bis)
    return bool(re.match(r'^\d+[\w\-]*$', t))

def analyze_affectation(text):
    """
    Parses the affectation citation text to extract:
    - action: 'Abrogate', 'Modify', 'Consolidate', 'Other'
    - affected_articles: list of articles/sections affected in the target document (e.g., ['Article 65'])
    """
    if not text:
        return "Other", []
        
    text_lower = text.lower()
    
    # 1. Determine action
    if any(k in text_lower for k in ["deroga", "abroga", "suprimeix", "suprimida", "elimina", "anul·la", "anulada", "suspèn", "suspesa"]):
        action = "Abrogate"
    elif any(k in text_lower for k in ["modifica", "reforma", "esmena", "afegeix", "redacta"]):
        action = "Modify"
    elif any(k in text_lower for k in ["refosa", "refon", "consolida"]):
        action = "Consolidate"
    else:
        action = "Other"
        
    # 2. Extract affected articles/sections
    affected_articles = []
    
    # Find patterns like "Articles 631-22, 631-23 i 631-24" or "Article 1"
    matches = re.finditer(
        r'\b(article|articles|artículo|artículos|art\b\.?)\s+([0-9a-zA-Z\s,i\-\.]+?)(?=\s+(?:de|la|el|d\'|l\'|en|per|\b[A-Z]{2,}\b|\b\d{4}\b)|$)', 
        text_lower
    )
    
    for m in matches:
        nums_str = m.group(2)
        # Split by spaces, commas, and coordinate conjunctions (Catalan/Spanish i, y)
        tokens = re.split(r'[\s,iy]+', nums_str)
        for t in tokens:
            t = t.strip(".- ")
            if is_valid_article_num(t):
                art_label = f"Article {t.capitalize()}"
                if art_label not in affected_articles:
                    affected_articles.append(art_label)
                    
    # Fallback standard match if the lookahead was too strict
    if not affected_articles:
        standard_matches = re.findall(
            r'\b(?:article|articles|artículo|artículos|art\.?)\s+(únic|único|primer|primero|segon|segundo|tercer|tercero|quart|cuarto|\d+[\w\-]*)\b',
            text_lower
        )
        for m in standard_matches:
            art_label = f"Article {m.capitalize()}"
            if art_label not in affected_articles:
                affected_articles.append(art_label)
                
    # Parse Annexes
    annex_matches = re.findall(r'\b(?:annex|anexo)\s*(\d*|\w*)\b', text_lower)
    for m in annex_matches:
        m = m.strip(".- ")
        val = f"Annex {m.capitalize()}" if m else "Annex"
        if val not in affected_articles:
            affected_articles.append(val)
            
    # Parse Dispositions
    disp_matches = re.findall(r'\b(?:disposició|disposición)\s+(\w+)\b', text_lower)
    for m in disp_matches:
        m = m.strip(".- ")
        val = f"Disposicio {m.capitalize()}"
        if val not in affected_articles:
            affected_articles.append(val)
            
    return action, affected_articles

def is_valid_citation_ref(ref_type, ref_val):
    """
    Filters out common stop words, prepositions, or generic values from document references.
    """
    ref_type = ref_type.strip().lower()
    ref_val = ref_val.strip().lower()
    
    # Common Catalan/Spanish articles, prepositions and connectors that trigger false matches
    STOP_WORDS = {
        "amb", "de", "la", "el", "per", "i", "y", "en", "les", "els", 
        "un", "una", "del", "dels", "al", "als", "d", "l", "s", "a", 
        "estat", "estatut", "generalitat", "catalunya", "ministeri", "govern"
    }
    
    if ref_val in STOP_WORDS or len(ref_val) <= 2:
        return False
        
    # The reference value should either contain digits (e.g. "3/1985") or be a known uppercase code (e.g. "GOV", "EMT")
    has_digit = any(c.isdigit() for c in ref_val)
    has_code = ref_val.isupper() and len(ref_val) >= 3
    
    if not (has_digit or has_code):
        return False
        
    return True

def extract_affected_sections_from_source_text(text, doc_ref):
    """
    Extracts affected article numbers or special section names (like Preamble, Annex)
    by scanning the source text of the modifying section at the sentence level.
    """
    if not text:
        return []
        
    # Split text into sentences/clauses
    clauses = re.split(r'[;.\n\r]+', text)
    affected_sects = []
    
    # Extract clean number/year part of target doc ref (e.g. "74/2026")
    ref_num = ""
    if doc_ref:
        ref_num_match = re.search(r'\d+/\d+', doc_ref)
        if ref_num_match:
            ref_num = ref_num_match.group(0)
            
    for clause in clauses:
        clause_lower = clause.lower()
        if not clause.strip():
            continue
            
        # Check if clause represents a citation to another document (e.g., Constitution, Statut)
        # without referencing our target document.
        is_uninteresting_citation = any(k in clause_lower for k in [
            "constitució", "constitucion", "estatut", "estatuto", "tribunal", 
            "sentència", "sentencia", "reial decret llei", "real decreto ley", "reial decret 17/1977"
        ])
        
        has_target_ref = False
        if doc_ref:
            if doc_ref.lower() in clause_lower or (ref_num and ref_num in clause_lower):
                has_target_ref = True
                
        # If it's a citation to another document and doesn't mention our target, skip it
        if is_uninteresting_citation and not has_target_ref:
            continue
            
        # Compile billingual document types pattern sorted by length descending
        doc_types = [
            "llei", "ley", "decret", "decreto", "ordre", "orden", "acord", "acuerdo", 
            "resolució", "resolución", "resolucion", "edicte", "edicto", "anunci", "anuncio", 
            "reial decret", "real decreto", "decret legislatiu", "decreto legislativo", 
            "decret llei", "decreto ley"
        ]
        doc_types.sort(key=len, reverse=True)
        doc_types_pattern = "|".join(re.escape(t) for t in doc_types)
        doc_ref_pat = re.compile(rf'\b({doc_types_pattern})\s+([a-zA-Z0-9\/\-\.]+)', re.IGNORECASE)
        found_refs = doc_ref_pat.findall(clause)
        
        # If the clause mentions other documents but not our target, skip it
        has_other_doc_ref = False
        for ref_type, ref_val in found_refs:
            if not is_valid_citation_ref(ref_type, ref_val):
                continue
            ref_str = f"{ref_type} {ref_val}".lower()
            if ref_num and ref_num in ref_str:
                continue
            if doc_ref and doc_ref.lower() in ref_str:
                continue
            has_other_doc_ref = True
            break
            
        if has_other_doc_ref and not has_target_ref:
            continue
            
        # Extract articles and special sections from this clause
        _, articles = analyze_affectation(clause)
        for art in articles:
            if art not in affected_sects:
                affected_sects.append(art)
                
        # Check special sections
        if any(k in clause_lower for k in ["part expositiva", "exposició de motius", "preàmbul", "preámbulo", "parte expositiva"]):
            if "Preamble" not in affected_sects:
                affected_sects.append("Preamble")
                
        if "signatures" in clause_lower or "signatura" in clause_lower or "firmas" in clause_lower:
            if "Signature" not in affected_sects:
                affected_sects.append("Signature")
                
        if "annex" in clause_lower or "anexo" in clause_lower:
            if not any(a.startswith("Annex ") for a in articles):
                annex_num_match = re.search(r'\b(?:annex|anexo)\s*(\d+)\b', clause_lower)
                val = f"Annex {annex_num_match.group(1).capitalize()}" if annex_num_match else "Annex"
                if val not in affected_sects:
                    affected_sects.append(val)
                
    return affected_sects

def score_section_for_affectation(sec_text, sec_title, sec_heading, doc_ref, art_target):
    """
    Heuristic scoring to match a section of text to a specific affectation target.
    """
    score = 0
    sec_text_lower = sec_text.lower()
    sec_title_lower = sec_title.lower()
    sec_heading_lower = sec_heading.lower()
    
    # 1. Target Article Number Match
    art_target_num = "".join(c for c in art_target if c.isdigit())
    if not art_target_num:
        for w in ["unic", "unico", "primer", "primero", "segon", "segundo", "tercer", "tercero"]:
            if w in art_target.lower():
                art_target_num = w
                break
                
    if art_target_num:
        if re.search(r'\b' + re.escape(art_target_num) + r'\b', sec_text_lower):
            score += 10
        if re.search(r'\b' + re.escape(art_target_num) + r'\b', sec_heading_lower):
            score += 15
        if re.search(r'\b' + re.escape(art_target_num) + r'\b', sec_title_lower):
            score += 20
            
    # 2. Document Reference Match (e.g., "3/1985")
    if doc_ref:
        doc_ref_clean = doc_ref.lower()
        ref_num = re.search(r'\d+/\d+', doc_ref_clean)
        if ref_num:
            num_str = ref_num.group(0)
            if num_str in sec_title_lower:
                score += 40
            elif num_str in sec_heading_lower:
                score += 30
            elif num_str in sec_text_lower:
                score += 15
        else:
            if doc_ref_clean in sec_title_lower:
                score += 40
            elif doc_ref_clean in sec_heading_lower:
                score += 30
            elif doc_ref_clean in sec_text_lower:
                score += 15
                
    # 3. Preamble/Introduction Penalty
    if any(k in sec_title_lower for k in ["preamble", "preambulo", "introduccio", "introduction", "signatures"]):
        score -= 10
        
    return score

_doc_ref_index = None

def resolve_doc_id_by_ref(ref_type, ref_val, docs_by_id, source_doc_id=None):
    """
    Tries to resolve a document reference to a specific documentId.
    Only attempts resolution if the reference contains a clear year or unique identifier (e.g. '26/2010').
    """
    global _doc_ref_index
    if _doc_ref_index is None:
        _doc_ref_index = {}
        for d_id, d in docs_by_id.items():
            doc_num = str(d.get("documentNumber") or "").lower()
            doc_title = str(d.get("titleCa") or d.get("title") or "").lower()
            found_nums = set(re.findall(r'\d+/\d+', doc_num) + re.findall(r'\d+/\d+', doc_title))
            for num in found_nums:
                _doc_ref_index.setdefault(num, []).append((d_id, d))

    ref_val_lower = ref_val.lower()
    
    # We require a clear year-slash or alphanumeric number code to avoid resolving generic terms
    num_match = re.search(r'\d+/\d+', ref_val_lower)
    if not num_match:
        return None
        
    num_str = num_match.group(0)
    ref_type_lower = ref_type.lower()
    
    # Search docs using the index
    possible_docs = _doc_ref_index.get(num_str, [])
    for doc_id, doc in possible_docs:
        # Avoid resolving a document to itself
        if source_doc_id and doc_id == source_doc_id:
            continue
            
        doc_num = str(doc.get("documentNumber") or "").lower()
        doc_title = str(doc.get("titleCa") or doc.get("title") or "").lower()
        
        if num_str in doc_num or num_str in doc_title:
            # Check document type matches
            doc_type = str(doc.get("type") or doc.get("typeOfLaw") or "").lower()
            if ref_type_lower in doc_type or ref_type_lower in doc_title:
                return doc_id
                
    return None

def load_and_merge_data(dogc_json_path, structured_dir, fetch_missing=False, limit_docs=None, years=None):
    """
    Loads dogc_documents.json and enriches it with detailed structured JSONs from structured_output.
    Returns:
      - dogc_nodes: dict of dogcNumber -> {dogcNumber, dateDOGC, year}
      - doc_nodes: dict of documentId -> {documentId, specificLabel, properties}
      - relationships: list of {documentId, dogcNumber, section}
      - section_nodes: list of {sectionId, specificLabel, properties}
      - section_relationships: list of {documentId, sectionId, order}
      - affectation_relationships: list of granular edges
      - citation_relationships: list of general citation edges
      - descriptor_nodes: dict of descriptorId -> {descriptorId, specificLabel, properties}
      - has_descriptor_relationships: list of {documentId, descriptorId, type}
    """
    # 1. Load dogc_documents.json
    print(f"Loading base documents from {dogc_json_path}...")
    if not os.path.exists(dogc_json_path):
        print(f"Error: {dogc_json_path} not found.")
        sys.exit(1)
        
    with open(dogc_json_path, "r", encoding="utf-8") as f:
        base_docs = json.load(f)
    
    if years:
        year_list = [y.strip() for y in years.split(",") if y.strip()]
        print(f"Filtering base documents to years: {year_list}")
        base_docs = [d for d in base_docs if str(d.get("year") or "") in year_list]
    
    if limit_docs:
        if limit_docs < 0:
            print(f"Limiting base documents to last {abs(limit_docs)} records (recent docs) for testing/speed.")
            base_docs = base_docs[limit_docs:]
        else:
            print(f"Limiting base documents to first {limit_docs} records for testing/speed.")
            base_docs = base_docs[:limit_docs]
        
    print(f"Loaded {len(base_docs)} base documents.")

    # Convert base docs list to a dictionary keyed by documentId
    docs_by_id = {}
    for doc in base_docs:
        doc_id = doc.get("documentId")
        if doc_id:
            docs_by_id[str(doc_id)] = doc

    # 2. Scan and load structured JSON files
    print(f"Scanning structured output folder {structured_dir}...")
    structured_count = 0
    if os.path.exists(structured_dir):
        for filename in os.listdir(structured_dir):
            if filename.endswith("_structured.json"):
                filepath = os.path.join(structured_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        s_data = json.load(f)
                    
                    doc_id = str(s_data.get("documentId"))
                    if not doc_id or doc_id == "None" or doc_id == "Unknown":
                        continue
                        
                    # Enrich existing document or create a new entry
                    target_doc = docs_by_id.setdefault(doc_id, {})
                    target_doc["documentId"] = doc_id
                    
                    ca_data = s_data.get("ca")
                    es_data = s_data.get("es")
                    
                    target_doc["hasSpanish"] = (es_data is not None and es_data.get("title") is not None)
                    
                    # Store descriptor metadata (falls back to ca or s_data root)
                    desc_data = {}
                    if ca_data and ca_data.get("descriptors"):
                        desc_data = ca_data.get("descriptors")
                    elif s_data.get("descriptors"):
                        desc_data = s_data.get("descriptors")
                    target_doc["descriptors"] = desc_data
                    
                    if ca_data:
                        target_doc["titleCa"] = ca_data.get("title")
                        target_doc["htmlUrlCa"] = ca_data.get("url")
                        formats = ca_data.get("formats") or {}
                        target_doc["pdfUrlCa"] = formats.get("pdf")
                        
                        metadata = ca_data.get("metadata") or {}
                        target_doc["type"] = metadata.get("typeOfLaw") or target_doc.get("type")
                        target_doc["documentDate"] = metadata.get("documentDate") or target_doc.get("documentDate")
                        target_doc["documentNumber"] = metadata.get("documentNumber") or target_doc.get("documentNumber")
                        target_doc["controlNumber"] = metadata.get("controlNumber") or target_doc.get("controlNumber")
                        target_doc["organisme"] = metadata.get("emittingOrganism") or target_doc.get("organisme")
                        target_doc["cve"] = metadata.get("cve") or target_doc.get("cve")
                        target_doc["dogcNumber"] = metadata.get("dogcNumber") or target_doc.get("dogcNumber")
                        target_doc["dateDOGC"] = metadata.get("dogcDate") or target_doc.get("dateDOGC")
                        target_doc["section"] = metadata.get("dogcSection") or target_doc.get("section")
                        
                        target_doc["sectionsCa"] = ca_data.get("sections") or []
                        target_doc["affectations"] = ca_data.get("affectations") or {}

                    if es_data:
                        target_doc["titleEs"] = es_data.get("title")
                        target_doc["htmlUrlEs"] = es_data.get("url")
                        formats_es = es_data.get("formats") or {}
                        target_doc["pdfUrlEs"] = formats_es.get("pdf")
                        
                        target_doc["sectionsEs"] = es_data.get("sections") or []
                        if "affectations" not in target_doc or not target_doc["affectations"]:
                            target_doc["affectations"] = es_data.get("affectations") or {}
                    
                    if s_data.get("eliUri"):
                        target_doc["eliUri"] = s_data.get("eliUri")
                        
                    structured_count += 1
                except Exception as e:
                    print(f"Warning: Failed to parse structured file {filename}: {e}")
                    
    print(f"Enriched data using {structured_count} structured JSON files.")

    # 3. Two-Pass construction:
    # PASS 1: Build nodes, unique DOGCs, sections, and maintain a lookup for section-matching.
    dogc_nodes = {}
    doc_nodes = {}
    relationships = []
    section_nodes = []
    section_relationships = []
    affectation_relationships = []
    citation_relationships = []
    descriptor_nodes = {}
    has_descriptor_relationships = []
    
    doc_sections_lookup = {}
    
    print("Normalizing properties and compiling section indices...")
    for doc_id, doc in tqdm(docs_by_id.items(), desc="Compiling nodes"):
        dogc_num = doc.get("dogcNumber")
        if not dogc_num:
            continue
        dogc_num = str(dogc_num).strip()
        
        # Populate DOGC nodes
        if dogc_num not in dogc_nodes:
            dogc_nodes[dogc_num] = {
                "dogcNumber": dogc_num,
                "dateDOGC": doc.get("dateDOGC") or "",
                "year": doc.get("year") or ""
            }
        else:
            if not dogc_nodes[dogc_num]["dateDOGC"] and doc.get("dateDOGC"):
                dogc_nodes[dogc_num]["dateDOGC"] = doc["dateDOGC"]
            if not dogc_nodes[dogc_num]["year"] and doc.get("year"):
                dogc_nodes[dogc_num]["year"] = doc["year"]

        # Resolve section with fallback logic
        section = doc.get("section") or doc.get("dogcSection")
        
        if (not section or section == "None" or section == "") and fetch_missing and HAS_PARSER:
            html_url = doc.get("htmlUrl")
            if html_url:
                try:
                    parsed_res_ca = fetch_document_from_rest_api(doc_id, html_url, language="ca")
                    if parsed_res_ca and "metadata" in parsed_res_ca:
                        meta = parsed_res_ca["metadata"]
                        section = meta.get("dogcSection")
                        doc["section"] = section
                        if meta.get("typeOfLaw"):
                            doc["type"] = meta.get("typeOfLaw")
                        if meta.get("documentDate"):
                            doc["documentDate"] = meta.get("documentDate")
                        if meta.get("documentNumber"):
                            doc["documentNumber"] = meta.get("documentNumber")
                        if meta.get("controlNumber"):
                            doc["controlNumber"] = meta.get("controlNumber")
                        if meta.get("emittingOrganism"):
                            doc["organisme"] = meta.get("emittingOrganism")
                        if meta.get("cve"):
                            doc["cve"] = meta.get("cve")
                        
                        doc["sectionsCa"] = parsed_res_ca.get("sections") or []
                        doc["titleCa"] = parsed_res_ca.get("title")
                        doc["affectations"] = parsed_res_ca.get("affectations") or {}
                        
                    html_url_es = get_spanish_url(html_url)
                    try:
                        parsed_res_es = fetch_document_from_rest_api(doc_id, html_url_es, language="es")
                        if parsed_res_es:
                            doc["sectionsEs"] = parsed_res_es.get("sections") or []
                            doc["titleEs"] = parsed_res_es.get("title")
                            doc["hasSpanish"] = True
                            if not doc.get("affectations"):
                                doc["affectations"] = parsed_res_es.get("affectations") or {}
                    except Exception:
                        doc["hasSpanish"] = False
                except Exception as e:
                    pass
        
        title_ca = doc.get("titleCa") or doc.get("title") or "Untitled Document"
        title_es = doc.get("titleEs") or ""
        url_ca = doc.get("htmlUrlCa") or doc.get("htmlUrl") or doc.get("url") or ""
        url_es = doc.get("htmlUrlEs") or ""
        pdf_url_ca = doc.get("pdfUrlCa") or doc.get("pdfUrl") or ""
        pdf_url_es = doc.get("pdfUrlEs") or ""
        
        has_spanish = doc.get("hasSpanish", False)
        if title_es:
            has_spanish = True
            
        raw_type = doc.get("type") or doc.get("typeOfLaw")
        if not raw_type:
            try:
                from fill_document_types import extract_type_from_title
                raw_type = extract_type_from_title(title_ca)
            except ImportError:
                raw_type = "Unknown"
        
        specific_label = to_cypher_label(raw_type)
        
        doc_properties = {
            "documentId": str(doc_id),
            "title": title_ca,
            "titleCa": title_ca,
            "titleEs": title_es,
            "eliUri": doc.get("eliUri") or "",
            "url": url_ca,
            "urlCa": url_ca,
            "urlEs": url_es,
            "pdfUrl": pdf_url_ca,
            "pdfUrlCa": pdf_url_ca,
            "pdfUrlEs": pdf_url_es,
            "typeOfLaw": raw_type or "Unknown",
            "documentDate": doc.get("documentDate") or "",
            "documentNumber": doc.get("documentNumber") or "",
            "controlNumber": doc.get("controlNumber") or "",
            "emittingOrganism": doc.get("organisme") or doc.get("emittingOrganism") or "Unknown",
            "cve": doc.get("cve") or "",
            "section": section or "Unknown",
            "isBilingual": has_spanish
        }
        
        doc_nodes[doc_id] = {
            "documentId": doc_id,
            "specificLabel": specific_label,
            "properties": doc_properties
        }
        
        relationships.append({
            "documentId": doc_id,
            "dogcNumber": dogc_num,
            "section": section or "Unknown"
        })

        # Process and spawn bilingual section nodes
        sections_ca = doc.get("sectionsCa") or doc.get("sections") or []
        sections_es = doc.get("sectionsEs") or []
        has_es_sections = len(sections_es) > 0
        
        parsed_sections = []
        for s_idx, sec_ca in enumerate(sections_ca):
            sec_type = sec_ca.get("type", "unknown")
            
            mapped_type = "Unknown"
            if sec_type == "introduction":
                mapped_type = "Introduction"
            elif sec_type == "article":
                mapped_type = "Article"
            elif sec_type == "disposition":
                mapped_type = "Disposition"
            elif sec_type == "annex":
                mapped_type = "Annex"
            elif sec_type == "signature":
                mapped_type = "Signature"
            else:
                mapped_type = sec_type.capitalize()
            
            is_article = (sec_type == "article")
            sec_specific_label = "Article" if is_article else None
            
            t_ca = sec_ca.get("title") or ""
            h_ca = sec_ca.get("heading") or ""
            commas_ca = sec_ca.get("commas") or []
            text_ca = "\n".join(commas_ca)
            if not text_ca and h_ca:
                text_ca = h_ca
            
            t_es = ""
            h_es = ""
            text_es = ""
            is_sec_bilingual = False
            
            if has_es_sections and s_idx < len(sections_es):
                sec_es = sections_es[s_idx]
                t_es = sec_es.get("title") or ""
                h_es = sec_es.get("heading") or ""
                commas_es = sec_es.get("commas") or []
                text_es = "\n".join(commas_es)
                if not text_es and h_es:
                    text_es = h_es
                is_sec_bilingual = True
            
            sec_id = f"{doc_id}_sec_{s_idx}"
            
            sec_properties = {
                "sectionId": sec_id,
                "title": t_ca,
                "titleCa": t_ca,
                "titleEs": t_es,
                "heading": h_ca,
                "headingCa": h_ca,
                "headingEs": h_es,
                "text": text_ca,
                "textCa": text_ca,
                "textEs": text_es,
                "type": mapped_type,
                "isBilingual": is_sec_bilingual
            }
            
            section_nodes.append({
                "sectionId": sec_id,
                "specificLabel": sec_specific_label,
                "properties": sec_properties
            })
            
            section_relationships.append({
                "documentId": doc_id,
                "sectionId": sec_id,
                "order": s_idx
            })
            
            parsed_sections.append(sec_properties)
            
        doc_sections_lookup[doc_id] = parsed_sections

        # Extract and compile document descriptors (Organisms, Geographic, Thematic)
        desc_data = doc.get("descriptors") or {}
        organisms = desc_data.get("organisms") or []
        geographic = desc_data.get("geographic") or []
        thematic = desc_data.get("thematic") or []
        
        for desc_type, desc_list in [("Organism", organisms), ("Geographic", geographic), ("Thematic", thematic)]:
            for d in desc_list:
                d_id = d.get("id")
                d_name_raw = d.get("name") or ""
                if not d_id or not d_name_raw:
                    continue
                d_id = str(d_id).strip()
                
                # Strip internal IDs suffix in parentheses, e.g. "anunci públic (136188)" -> "anunci públic"
                d_name = re.sub(r'\s*\(\d+\)\s*$', '', d_name_raw).strip()
                
                if d_id not in descriptor_nodes:
                    descriptor_nodes[d_id] = {
                        "descriptorId": d_id,
                        "specificLabel": f"{desc_type}Descriptor",
                        "properties": {
                            "descriptorId": d_id,
                            "name": d_name,
                            "rawName": d_name_raw,
                            "type": desc_type
                        }
                    }
                
                has_descriptor_relationships.append({
                    "documentId": doc_id,
                    "descriptorId": d_id,
                    "type": desc_type.lower()
                })

    # PASS 2: Match affectations and extract general citations using heuristics.
    print("Mapping section-to-section affectation edges and general citations...")
    
    seen_citations = set()
    
    for doc_id, doc in tqdm(docs_by_id.items(), desc="Mapping affectations"):
        affectations = doc.get("affectations") or {}
        sections_A = doc_sections_lookup.get(doc_id) or []
        
        # 1. Extract general citations from the text of each section (Bilingual support)
        for sec in sections_A:
            sec_text_ca = (sec.get("headingCa") or sec.get("heading") or "") + "\n" + (sec.get("textCa") or sec.get("text") or "")
            sec_text_es = (sec.get("headingEs") or "") + "\n" + (sec.get("textEs") or "")
            
            sec_text_combined = sec_text_ca + "\n" + sec_text_es
            clauses = re.split(r'[;.\n\r]+', sec_text_combined)
            
            for clause in clauses:
                clause_strip = clause.strip()
                if not clause_strip:
                    continue
                    
                doc_types = [
                    "llei", "ley", "decret", "decreto", "ordre", "orden", "acord", "acuerdo", 
                    "resolució", "resolución", "resolucion", "edicte", "edicto", "anunci", "anuncio", 
                    "reial decret", "real decreto", "decret legislatiu", "decreto legislativo", 
                    "decret llei", "decreto ley"
                ]
                doc_types.sort(key=len, reverse=True)
                doc_types_pattern = "|".join(re.escape(t) for t in doc_types)
                doc_ref_pat = re.compile(rf'\b({doc_types_pattern})\s+([a-zA-Z0-9\/\-\.]+)', re.IGNORECASE)
                found_refs = doc_ref_pat.findall(clause_strip)
                
                for ref_type, ref_val in found_refs:
                    if not is_valid_citation_ref(ref_type, ref_val):
                        continue
                        
                    ref_str = f"{ref_type} {ref_val}".strip()
                    ref_str_clean = normalize_section_title(ref_str)
                    
                    _, articles = analyze_affectation(clause_strip)
                    if not articles:
                        articles = [""]
                        
                    target_doc_id = resolve_doc_id_by_ref(ref_type, ref_val, docs_by_id, source_doc_id=doc_id)
                    is_mapped = (target_doc_id is not None)
                    
                    for art in articles:
                        citation_key = (sec["sectionId"], target_doc_id or ref_str_clean, art)
                        if citation_key in seen_citations:
                            continue
                        seen_citations.add(citation_key)
                        
                        target_sec_id = None
                        target_type = "Document"
                        
                        if is_mapped:
                            sections_B = doc_sections_lookup.get(target_doc_id) or []
                            norm_art = normalize_section_title(art)
                            for sec_B in sections_B:
                                if normalize_section_title(sec_B["title"]) == norm_art:
                                    target_sec_id = sec_B["sectionId"]
                                    target_type = "DocumentSection"
                                    break
                                    
                        citation_relationships.append({
                            "sourceDocumentId": doc_id,
                            "targetDocumentId": target_doc_id or "unresolved",
                            "sourceId": sec["sectionId"],
                            "sourceType": "DocumentSection",
                            "targetId": target_sec_id or target_doc_id or "unresolved",
                            "targetType": target_type,
                            "relationshipType": "CITES",
                            "isMapped": is_mapped,
                            "properties": {
                                "citedDocument": ref_str,
                                "citedSection": art
                            }
                        })
        
        # 2. Active affectations (current document A affects target document B)
        active_affs = affectations.get("active") or []
        for aff in active_affs:
            target_id = aff.get("targetDocumentId")
            if not target_id or target_id == "None":
                continue
            target_id = str(target_id).strip()
            
            raw_text = aff.get("text") or ""
            action, affected_sects_meta = analyze_affectation(raw_text)
            doc_ref = extract_doc_ref(raw_text)
            
            rel_type = "AFFECTS"
            if action == "Abrogate":
                rel_type = "ABROGATES"
            elif action == "Modify":
                rel_type = "MODIFIES"
            elif action == "Consolidate":
                rel_type = "CONSOLIDATES"
                
            best_sec_A = None
            best_score_A = -9999
            for sec in sections_A:
                score = score_section_for_affectation(
                    sec.get("text") or "", 
                    sec.get("title") or "", 
                    sec.get("heading") or "", 
                    doc_ref, 
                    ""
                )
                for art in affected_sects_meta:
                    art_num = "".join(c for c in art if c.isdigit())
                    combined_text = (sec.get("heading") or "") + " " + (sec.get("text") or "")
                    if art_num and re.search(r'\b' + re.escape(art_num) + r'\b', combined_text.lower() + " " + sec.get("title", "").lower()):
                        score += 15
                if score > best_score_A:
                    best_score_A = score
                    best_sec_A = sec
            
            source_id = doc_id
            source_type = "Document"
            source_text_to_scan = ""
            if best_sec_A and best_score_A >= 10:
                source_id = best_sec_A["sectionId"]
                source_type = "DocumentSection"
                source_text_to_scan = (best_sec_A.get("heading") or "") + "\n" + (best_sec_A.get("text") or "")
            
            affected_sects_text = extract_affected_sections_from_source_text(source_text_to_scan, doc_ref) if source_text_to_scan else []
            affected_sects = list(set(affected_sects_meta + affected_sects_text))
            
            if affected_sects:
                for art_target in affected_sects:
                    target_id_edge = target_id
                    target_type_edge = "Document"
                    
                    sections_B = doc_sections_lookup.get(target_id) or []
                    norm_art_target = normalize_section_title(art_target)
                    for sec_B in sections_B:
                        if normalize_section_title(sec_B["title"]) == norm_art_target:
                            target_id_edge = sec_B["sectionId"]
                            target_type_edge = "DocumentSection"
                            break
                            
                    affectation_relationships.append({
                        "sourceDocumentId": doc_id,
                        "targetDocumentId": target_id,
                        "sourceId": source_id,
                        "sourceType": source_type,
                        "targetId": target_id_edge,
                        "targetType": target_type_edge,
                        "relationshipType": rel_type,
                        "properties": {
                            "text": raw_text,
                            "type": "active",
                            "action": action,
                            "affectedSections": [art_target],
                            "mappedSection": art_target
                        }
                    })
            else:
                affectation_relationships.append({
                    "sourceDocumentId": doc_id,
                    "targetDocumentId": target_id,
                    "sourceId": source_id,
                    "sourceType": source_type,
                    "targetId": target_id,
                    "targetType": "Document",
                    "relationshipType": rel_type,
                    "properties": {
                        "text": raw_text,
                        "type": "active",
                        "action": action,
                        "affectedSections": [],
                        "mappedSection": ""
                    }
                })
            
        # 3. Passive affectations (target document B affects current document A)
        passive_affs = affectations.get("passive") or []
        for aff in passive_affs:
            target_id = aff.get("targetDocumentId")
            if not target_id or target_id == "None":
                continue
            target_id = str(target_id).strip()
            
            raw_text = aff.get("text") or ""
            action, affected_sects_meta = analyze_affectation(raw_text)
            doc_ref = extract_doc_ref(raw_text)
            
            rel_type = "AFFECTS"
            if action == "Abrogate":
                rel_type = "ABROGATES"
            elif action == "Modify":
                rel_type = "MODIFIES"
            elif action == "Consolidate":
                rel_type = "CONSOLIDATES"
                
            sections_B = doc_sections_lookup.get(target_id) or []
            doc_A_ref = doc.get("documentNumber") or doc.get("title") or ""
            best_sec_B = None
            best_score_B = -9999
            for sec_B in sections_B:
                score = score_section_for_affectation(
                    sec_B.get("text") or "", 
                    sec_B.get("title") or "", 
                    sec_B.get("heading") or "", 
                    doc_A_ref, 
                    ""
                )
                for art in affected_sects_meta:
                    art_num = "".join(c for c in art if c.isdigit())
                    combined_text = (sec_B.get("heading") or "") + " " + (sec_B.get("text") or "")
                    if art_num and re.search(r'\b' + re.escape(art_num) + r'\b', combined_text.lower() + " " + sec_B.get("title", "").lower()):
                        score += 15
                if score > best_score_B:
                    best_score_B = score
                    best_sec_B = sec_B
                    
            source_id = target_id
            source_type = "Document"
            source_text_to_scan = ""
            if best_sec_B and best_score_B >= 10:
                source_id = best_sec_B["sectionId"]
                source_type = "DocumentSection"
                source_text_to_scan = (best_sec_B.get("heading") or "") + "\n" + (best_sec_B.get("text") or "")
                
            affected_sects_text = extract_affected_sections_from_source_text(source_text_to_scan, doc_A_ref) if source_text_to_scan else []
            affected_sects = list(set(affected_sects_meta + affected_sects_text))
            
            if affected_sects:
                for art_target in affected_sects:
                    target_id_edge = doc_id
                    target_type_edge = "Document"
                    
                    sections_A = doc_sections_lookup.get(doc_id) or []
                    norm_art_target = normalize_section_title(art_target)
                    for sec_A in sections_A:
                        if normalize_section_title(sec_A["title"]) == norm_art_target:
                            target_id_edge = sec_A["sectionId"]
                            target_type_edge = "DocumentSection"
                            break
                            
                    affectation_relationships.append({
                        "sourceDocumentId": target_id,
                        "targetDocumentId": doc_id,
                        "sourceId": source_id,
                        "sourceType": source_type,
                        "targetId": target_id_edge,
                        "targetType": target_type_edge,
                        "relationshipType": rel_type,
                        "properties": {
                            "text": raw_text,
                            "type": "passive",
                            "action": action,
                            "affectedSections": [art_target],
                            "mappedSection": art_target
                        }
                    })
            else:
                affectation_relationships.append({
                    "sourceDocumentId": target_id,
                    "targetDocumentId": doc_id,
                    "sourceId": source_id,
                    "sourceType": source_type,
                    "targetId": doc_id,
                    "targetType": "Document",
                    "relationshipType": rel_type,
                    "properties": {
                        "text": raw_text,
                        "type": "passive",
                        "action": action,
                        "affectedSections": [],
                        "mappedSection": ""
                    }
                })

    # 4. Generate placeholder documents for referenced target documents not loaded in base
    print("Generating placeholder documents for unresolved references...")
    referenced_ids = set()
    for rel in affectation_relationships:
        referenced_ids.add(rel["sourceDocumentId"])
        referenced_ids.add(rel["targetDocumentId"])
        
    placeholders_added = 0
    for ref_id in referenced_ids:
        if ref_id not in doc_nodes:
            doc_nodes[ref_id] = {
                "documentId": ref_id,
                "specificLabel": "Document",
                "properties": {
                    "documentId": ref_id,
                    "title": f"Referenced Law (Placeholder - Document ID {ref_id})",
                    "titleCa": f"Referenced Law (Placeholder - Document ID {ref_id})",
                    "titleEs": "",
                    "eliUri": "",
                    "url": "",
                    "urlCa": "",
                    "urlEs": "",
                    "pdfUrl": "",
                    "pdfUrlCa": "",
                    "pdfUrlEs": "",
                    "typeOfLaw": "Unknown",
                    "documentDate": "",
                    "documentNumber": "",
                    "controlNumber": "",
                    "emittingOrganism": "Unknown",
                    "cve": "",
                    "section": "Unknown",
                    "isBilingual": False
                }
            }
            placeholders_added += 1
    print(f"Added {placeholders_added} placeholder documents.")

    return dogc_nodes, doc_nodes, relationships, section_nodes, section_relationships, affectation_relationships, citation_relationships, descriptor_nodes, has_descriptor_relationships

def main():
    parser = argparse.ArgumentParser(description="Spain/Catalonia Law Graph: Extract nodes, sections, affectations, citations, and descriptors")
    parser.add_argument("--dogc-json", type=str, default="data/dogc_documents.json", help="Path to dogc_documents.json")
    parser.add_argument("--structured-dir", type=str, default="data/structured_output", help="Path to structured_output directory")
    parser.add_argument("--output-dir", type=str, default="data/prepared_graph_data", help="Output directory to save processed files")
    parser.add_argument("--limit-docs", type=int, default=None, help="Limit number of documents loaded from json for testing")
    parser.add_argument("--years", type=str, default=None, help="Comma-separated list of years to filter documents (e.g. 2024,2025,2026)")
    parser.add_argument("--fetch-missing", action="store_true", help="Fetch missing section from REST API if fallback available")
    args = parser.parse_args()

    # Resolve paths relative to the parent Catalonia root directory
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dogc_json_path = os.path.join(script_dir, args.dogc_json)
    structured_dir_path = os.path.join(script_dir, args.structured_dir)
    output_dir_path = os.path.join(script_dir, args.output_dir)

    # 1. Load and merge data
    dogc_nodes, doc_nodes, relationships, section_nodes, section_relationships, affectation_relationships, citation_relationships, descriptor_nodes, has_descriptor_relationships = load_and_merge_data(
        dogc_json_path, 
        structured_dir_path, 
        fetch_missing=args.fetch_missing, 
        limit_docs=args.limit_docs,
        years=args.years
    )

    print("\nExtraction Summary:")
    print(f"  - Total unique DOGC nodes: {len(dogc_nodes)}")
    print(f"  - Total unique Document nodes (including placeholders): {len(doc_nodes)}")
    print(f"  - Total unique DocumentSection nodes: {len(section_nodes)}")
    print(f"  - Total unique Descriptor nodes (organisms/geo/theme): {len(descriptor_nodes)}")
    print(f"  - Total PUBLISHED_IN relationships: {len(relationships)}")
    print(f"  - Total HAS_SECTION relationships: {len(section_relationships)}")
    print(f"  - Total HAS_DESCRIPTOR relationships: {len(has_descriptor_relationships)}")
    print(f"  - Total Granular Affectation relationships: {len(affectation_relationships)}")
    print(f"  - Total Citation relationships extracted: {len(citation_relationships)}")
    
    mapped_citations_count = sum(1 for c in citation_relationships if c["isMapped"])
    unmapped_citations_count = len(citation_relationships) - mapped_citations_count
    print(f"    * Mapped Citations (resolved document ID): {mapped_citations_count}")
    print(f"    * Unmapped Citations (unresolved/external references): {unmapped_citations_count}")
    
    # Count descriptor distributions
    desc_type_counts = {}
    for d in descriptor_nodes.values():
        t = d["properties"]["type"]
        desc_type_counts[t] = desc_type_counts.get(t, 0) + 1
    print("\nDescriptor nodes distribution:")
    for t, count in desc_type_counts.items():
        print(f"  - {t} Descriptor: {count}")
        
    bilingual_docs_count = sum(1 for d in doc_nodes.values() if d["properties"]["isBilingual"])
    print(f"\n  - Bilingual Document nodes: {bilingual_docs_count} ({bilingual_docs_count/len(doc_nodes)*100:.2f}%)")
    
    bilingual_secs_count = sum(1 for s in section_nodes if s["properties"]["isBilingual"])
    if section_nodes:
        print(f"  - Bilingual Section nodes: {bilingual_secs_count} ({bilingual_secs_count/len(section_nodes)*100:.2f}%)")
    
    aff_type_counts = {}
    for rel in affectation_relationships:
        t = rel["relationshipType"]
        aff_type_counts[t] = aff_type_counts.get(t, 0) + 1
    print("\nAffectation relationship type distribution:")
    for t, count in sorted(aff_type_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  - -[{t}]->: {count}")

    # Create output directory
    os.makedirs(output_dir_path, exist_ok=True)

    # Save outputs to JSON
    dogc_nodes_file = os.path.join(output_dir_path, "dogc_nodes.json")
    doc_nodes_file = os.path.join(output_dir_path, "document_nodes.json")
    relationships_file = os.path.join(output_dir_path, "published_in_relationships.json")
    sections_file = os.path.join(output_dir_path, "document_sections.json")
    sec_relationships_file = os.path.join(output_dir_path, "has_section_relationships.json")
    affectations_file = os.path.join(output_dir_path, "affectation_relationships.json")
    citations_file = os.path.join(output_dir_path, "citation_relationships.json")
    descriptors_file = os.path.join(output_dir_path, "descriptor_nodes.json")
    has_descriptor_file = os.path.join(output_dir_path, "has_descriptor_relationships.json")

    print(f"\nSaving graph data files to directory: {output_dir_path}")
    
    with open(dogc_nodes_file, "w", encoding="utf-8") as f:
        json.dump(list(dogc_nodes.values()), f, indent=2, ensure_ascii=False)
    print(f"  - Saved DOGC nodes to: {dogc_nodes_file}")

    with open(doc_nodes_file, "w", encoding="utf-8") as f:
        json.dump(list(doc_nodes.values()), f, indent=2, ensure_ascii=False)
    print(f"  - Saved Document nodes to: {doc_nodes_file}")

    with open(relationships_file, "w", encoding="utf-8") as f:
        json.dump(relationships, f, indent=2, ensure_ascii=False)
    print(f"  - Saved published_in relationships to: {relationships_file}")

    with open(sections_file, "w", encoding="utf-8") as f:
        json.dump(section_nodes, f, indent=2, ensure_ascii=False)
    print(f"  - Saved DocumentSection nodes to: {sections_file}")

    with open(sec_relationships_file, "w", encoding="utf-8") as f:
        json.dump(section_relationships, f, indent=2, ensure_ascii=False)
    print(f"  - Saved has_section relationships to: {sec_relationships_file}")

    with open(affectations_file, "w", encoding="utf-8") as f:
        json.dump(affectation_relationships, f, indent=2, ensure_ascii=False)
    print(f"  - Saved affectation relationships to: {affectations_file}")

    with open(citations_file, "w", encoding="utf-8") as f:
        json.dump(citation_relationships, f, indent=2, ensure_ascii=False)
    print(f"  - Saved citation relationships to: {citations_file}")

    with open(descriptors_file, "w", encoding="utf-8") as f:
        json.dump(list(descriptor_nodes.values()), f, indent=2, ensure_ascii=False)
    print(f"  - Saved Descriptor nodes to: {descriptors_file}")

    with open(has_descriptor_file, "w", encoding="utf-8") as f:
        json.dump(has_descriptor_relationships, f, indent=2, ensure_ascii=False)
    print(f"  - Saved has_descriptor relationships to: {has_descriptor_file}")

    # Generate a sample Cypher import script template
    cypher_script_file = os.path.join(output_dir_path, "import_queries.cypher")
    with open(cypher_script_file, "w", encoding="utf-8") as f:
        f.write("""// Cypher templates for importing the generated JSON files into Memgraph/Neo4j

// ============================================================================
// 1. UNIQUE CONSTRAINTS (Run these first to ensure indexing & performance)
// ============================================================================
CREATE CONSTRAINT ON (g:DOGC) ASSERT g.dogcNumber IS UNIQUE;
CREATE CONSTRAINT ON (d:Document) ASSERT d.documentId IS UNIQUE;
CREATE CONSTRAINT ON (s:DocumentSection) ASSERT s.sectionId IS UNIQUE;
CREATE CONSTRAINT ON (desc:Descriptor) ASSERT desc.descriptorId IS UNIQUE;

// ============================================================================
// 2. IMPORT DOGC NODES (Pass dogc_nodes.json as $batch parameter)
// ============================================================================
// UNWIND $batch AS row
// MERGE (g:DOGC {dogcNumber: row.dogcNumber})
// SET g.dateDOGC = row.dateDOGC, g.year = row.year;

// ============================================================================
// 3. IMPORT DOCUMENT NODES (Pass document_nodes.json grouped by label as $batch)
// ============================================================================
// UNWIND $batch AS row
// MERGE (d:Document {documentId: row.documentId})
// SET d.title = row.title,
//     d.titleCa = row.titleCa,
//     d.titleEs = row.titleEs,
//     d.eliUri = row.eliUri,
//     d.url = row.url,
//     d.urlCa = row.urlCa,
//     d.urlEs = row.urlEs,
//     d.pdfUrl = row.pdfUrl,
//     d.pdfUrlCa = row.pdfUrlCa,
//     d.pdfUrlEs = row.pdfUrlEs,
//     d.typeOfLaw = row.typeOfLaw,
//     d.documentDate = row.documentDate,
//     d.documentNumber = row.documentNumber,
//     d.controlNumber = row.controlNumber,
//     d.emittingOrganism = row.emittingOrganism,
//     d.cve = row.cve,
//     d.section = row.section,
//     d.isBilingual = row.isBilingual
// WITH d, row
// // Dynamic label assignment workaround:
// // In your application code, filter the batch by row.specificLabel and run:
// // SET d:YourSpecificLabel; // e.g. SET d:Llei or SET d:Decret

// ============================================================================
// 4. IMPORT DOGC RELATIONSHIPS (Pass published_in_relationships.json as $batch)
// ============================================================================
// UNWIND $batch AS row
// MATCH (d:Document {documentId: row.documentId})
// MATCH (g:DOGC {dogcNumber: row.dogcNumber})
// MERGE (d)-[r:PUBLISHED_IN]->(g)
// SET r.section = row.section;

// ============================================================================
// 5. IMPORT DOCUMENTSECTION NODES (Pass document_sections.json as $batch)
// ============================================================================
// UNWIND $batch AS row
// MERGE (s:DocumentSection {sectionId: row.sectionId})
// SET s.title = row.title,
//     s.titleCa = row.titleCa,
//     s.titleEs = row.titleEs,
//     s.heading = row.heading,
//     s.headingCa = row.headingCa,
//     s.headingEs = row.headingEs,
//     s.text = row.text,
//     s.textCa = row.textCa,
//     s.textEs = row.textEs,
//     s.type = row.type,
//     s.isBilingual = row.isBilingual
// WITH s, row
// // Double label logic for Articles:
// // Filter the batch to only rows where row.specificLabel == 'Article', then set the label:
// // SET s:Article;

// ============================================================================
// 6. IMPORT SECTION RELATIONSHIPS (Pass has_section_relationships.json as $batch)
// ============================================================================
// UNWIND $batch AS row
// MATCH (d:Document {documentId: row.documentId})
// MATCH (s:DocumentSection {sectionId: row.sectionId})
// MERGE (d)-[r:HAS_SECTION]->(s)
// SET r.order = row.order;

// ============================================================================
// 7. IMPORT GRANULAR AFFECTATION RELATIONSHIPS (Pass affectation_relationships.json as $batch)
// ============================================================================
// Group your batch by row.sourceType, row.targetType, and row.relationshipType in your code.
// Depending on the node labels, run the appropriate query:
//
// 7a. Document to Document
// UNWIND $batch AS row
// MATCH (source:Document {documentId: row.sourceId})
// MATCH (target:Document {documentId: row.targetId})
// MERGE (source)-[r:ABROGATES]->(target) // Replace ABROGATES dynamically based on row.relationshipType
// SET r.text = row.properties.text,
//     r.type = row.properties.type,
//     r.action = row.properties.action,
//     r.affectedSections = row.properties.affectedSections,
//     r.mappedSection = row.properties.mappedSection;
//
// 7b. DocumentSection to DocumentSection
// UNWIND $batch AS row
// MATCH (source:DocumentSection {sectionId: row.sourceId})
// MATCH (target:DocumentSection {sectionId: row.targetId})
// MERGE (source)-[r:ABROGATES]->(target)
// SET r.text = row.properties.text,
//     r.type = row.properties.type,
//     r.action = row.properties.action,
//     r.affectedSections = row.properties.affectedSections,
//     r.mappedSection = row.properties.mappedSection;
//
// 7c. DocumentSection to Document
// UNWIND $batch AS row
// MATCH (source:DocumentSection {sectionId: row.sourceId})
// MATCH (target:Document {documentId: row.targetId})
// MERGE (source)-[r:ABROGATES]->(target)
// SET r.text = row.properties.text,
//     r.type = row.properties.type,
//     r.action = row.properties.action,
//     r.affectedSections = row.properties.affectedSections,
//     r.mappedSection = row.properties.mappedSection;
//
// 7d. Document to DocumentSection
// UNWIND $batch AS row
// MATCH (source:Document {documentId: row.sourceId})
// MATCH (target:DocumentSection {sectionId: row.targetId})
// MERGE (source)-[r:ABROGATES]->(target)
// SET r.text = row.properties.text,
//     r.type = row.properties.type,
//     r.action = row.properties.action,
//     r.affectedSections = row.properties.affectedSections,
//     r.mappedSection = row.properties.mappedSection;

// ============================================================================
// 8. IMPORT CITATION RELATIONSHIPS (Pass citation_relationships.json as $batch)
// ============================================================================
// UNWIND $batch AS row
// WITH row WHERE row.isMapped = true
// MATCH (source:DocumentSection {sectionId: row.sourceId})
// MATCH (target:Document {documentId: row.targetId})
// MERGE (source)-[r:CITES]->(target)
// SET r.citedDocument = row.properties.citedDocument,
//     r.citedSection = row.properties.citedSection;

// ============================================================================
// 9. IMPORT DESCRIPTOR NODES (Pass descriptor_nodes.json as $batch)
// ============================================================================
// UNWIND $batch AS row
// MERGE (desc:Descriptor {descriptorId: row.descriptorId})
// SET desc.name = row.properties.name,
//     desc.rawName = row.properties.rawName,
//     desc.type = row.properties.type
// WITH desc, row
// // Dynamic label assignment workaround:
// // SET desc:YourSpecificLabel; // e.g. SET desc:ThematicDescriptor or SET desc:GeographicDescriptor

// ============================================================================
// 10. IMPORT HAS_DESCRIPTOR RELATIONSHIPS (Pass has_descriptor_relationships.json as $batch)
// ============================================================================
// UNWIND $batch AS row
// MATCH (d:Document {documentId: row.documentId})
// MATCH (desc:Descriptor {descriptorId: row.descriptorId})
// MERGE (d)-[r:HAS_DESCRIPTOR]->(desc)
// SET r.type = row.type;
""")
    print(f"  - Saved reference Cypher import queries template to: {cypher_script_file}")
    print("\nData preparation complete. No database was connected to or modified. 🎉")

if __name__ == "__main__":
    main()
