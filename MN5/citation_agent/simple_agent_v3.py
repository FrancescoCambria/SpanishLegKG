import os
import sys
import json
import re
import signal
import argparse
import time
from pathlib import Path
from tqdm import tqdm

# Force local connections to bypass proxy (crucial for HPC)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"


class SimpleCitationAgentV3:
    def __init__(self, args):
        self.args = args
        self.input_json = Path(args.input_json)
        self.output_json = Path(args.output_json)
        self._vllm_client = None
        
        self.graph_data = None
        self.nodes_by_id = {}
        
        # Lookup tables for mapping citations
        self.nodes_by_title = {}
        self.nodes_by_doc_number = {}
        self.nodes_by_eli = {}
        self.nodes_by_dogc_number = {}
        self.sections_by_document = {}
        self.document_nodes = []
        self.max_node_id = 0
        
        # Keep track of active changes for signal handling
        self.interrupted = False
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    @property
    def vllm_client(self):
        if self._vllm_client is None:
            try:
                from openai import OpenAI
                import httpx
                self._vllm_client = OpenAI(
                    base_url=self.args.vllm_url,
                    api_key="none",
                    http_client=httpx.Client(proxy=None)
                )
            except ImportError as e:
                print(f"Error importing OpenAI/httpx dependencies: {e}")
                print("Please ensure 'openai' and 'httpx' are installed to run LLM extraction calls.")
                sys.exit(1)
        return self._vllm_client

    def _handle_signal(self, signum, frame):
        print(f"\n[SimpleAgentV3] Received signal {signum}. Gracefully shutting down and saving progress...")
        self.interrupted = True

    def _normalize_str(self, s):
        if not s:
            return ""
        s = str(s).lower().strip()
        s = re.sub(r"[^\w\s/]", "", s)
        s = " ".join(s.split())
        return s

    def _normalize_eli(self, s):
        if not s:
            return ""
        s = str(s).lower().strip()
        s = re.sub(r"^https?://[^/]+/", "", s)
        s = re.sub(r"^/?(?:eli/)?", "", s)
        s = re.sub(r"/dof(?:/.*)?$", "", s)
        s = s.strip("/")
        return s

    def _normalize_section_title(self, text):
        if not text:
            return ""
        text = text.lower().strip()
        text = re.sub(r"\b(artículo|articulo|article|art|art\.)\b", "art", text)
        text = re.sub(r"\b(sección|seccion|section|sec|sec\.)\b", "sec", text)
        text = re.sub(r"\b(anexo|annex|an\.)\b", "annex", text)
        text = re.sub(r"[^\w\s]", "", text)
        text = " ".join(text.split())
        return text

    def load_graph(self):
        if self.graph_data is None:
            target_path = self.output_json if self.output_json.exists() else self.input_json
            print(f"Loading graph data from {target_path}...")
            with open(target_path, "r", encoding="utf-8") as f:
                self.graph_data = json.load(f)
            
            print(f"Building node index by ID for {len(self.graph_data.get('nodes', []))} nodes...")
            self.nodes_by_id = {node["id"]: node for node in self.graph_data.get("nodes", [])}
            
            # Reset lookup structures
            self.nodes_by_title = {}
            self.nodes_by_doc_number = {}
            self.nodes_by_eli = {}
            self.nodes_by_dogc_number = {}
            self.sections_by_document = {}
            self.document_nodes = []
            self.max_node_id = 0
            
            for node in self.graph_data.get("nodes", []):
                node_id = node["id"]
                if isinstance(node_id, int):
                    if node_id > self.max_node_id:
                        self.max_node_id = node_id
                elif isinstance(node_id, str) and node_id.isdigit():
                    val = int(node_id)
                    if val > self.max_node_id:
                        self.max_node_id = val
                
                labels = node.get("labels", [])
                if any(lbl in labels for lbl in ["Document", "DOGC", "Law", "Decree"]):
                    self.document_nodes.append(node)
                    props = node.get("properties", {})
                    
                    # 1. Index by titles
                    for title_key in ["title", "titleEs", "titleCa"]:
                        title_val = props.get(title_key)
                        if title_val:
                            clean_t = self._normalize_str(title_val)
                            if clean_t:
                                self.nodes_by_title[clean_t] = node_id
                                
                    # 2. Index by document number
                    doc_num = props.get("documentNumber")
                    if doc_num:
                        clean_num = self._normalize_str(doc_num)
                        if clean_num:
                            self.nodes_by_doc_number[clean_num] = node_id
                            
                    # 3. Index by ELI URI
                    eli_uri = props.get("eliUri")
                    if eli_uri:
                        clean_eli = self._normalize_eli(eli_uri)
                        if clean_eli:
                            self.nodes_by_eli[clean_eli] = node_id
                            
                    # 4. Index by DOGC number
                    dogc_num = props.get("dogcNumber")
                    if dogc_num:
                        clean_dogc = self._normalize_str(str(dogc_num))
                        if clean_dogc:
                            self.nodes_by_dogc_number[clean_dogc] = node_id

            # Map document sections
            print("Mapping document sections and articles...")
            for rel in self.graph_data.get("relationships", []):
                if rel.get("type") == "HAS_SECTION":
                    source_id = rel.get("source")
                    target_id = rel.get("target")
                    target_node = self.nodes_by_id.get(target_id)
                    if target_node:
                        if source_id not in self.sections_by_document:
                            self.sections_by_document[source_id] = []
                        self.sections_by_document[source_id].append(target_node)
            
            print(f"Indexed {len(self.nodes_by_title)} titles, {len(self.nodes_by_doc_number)} doc numbers, "
                  f"{len(self.nodes_by_eli)} ELI URIs, {len(self.nodes_by_dogc_number)} DOGC numbers across {len(self.document_nodes)} document nodes.")
            print(f"Current maximum node ID is {self.max_node_id}.")
        return self.graph_data

    def save_graph(self, path=None):
        if self.graph_data is None:
            return
        save_path = path or self.output_json
        print(f"Saving graph data to {save_path}...")
        temp_path = save_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self.graph_data, f, ensure_ascii=False, indent=2)
        temp_path.replace(save_path)
        print("Save completed successfully.")

    def get_existing_citations_for_node(self, node_id):
        """
        Finds all existing outgoing citation relationships for a given node ID in the graph.
        """
        citation_types = {"CITES", "AFFECTS", "ABROGATES", "MODIFIES", "CONSOLIDATES", "MODIFY", "ABROGATE"}
        existing = []

        for rel in self.graph_data.get("relationships", []):
            if rel.get("source") == node_id and rel.get("type") in citation_types:
                target_id = rel.get("target")
                target_node = self.nodes_by_id.get(target_id)
                
                target_title = ""
                doc_number = ""
                eli_uri = ""
                dogc_number = ""
                type_of_law = ""
                
                if target_node:
                    props = target_node.get("properties", {})
                    target_title = props.get("titleEs") or props.get("titleCa") or props.get("title") or ""
                    doc_number = props.get("documentNumber") or ""
                    eli_uri = props.get("eliUri") or ""
                    dogc_number = props.get("dogcNumber") or ""
                    type_of_law = props.get("typeOfLaw") or ""

                rel_props = rel.get("properties", {}) or {}
                existing.append({
                    "citation_id": len(existing) + 1,
                    "rel_ref": rel,
                    "relationship_type": rel.get("type"),
                    "target_node_id": target_id,
                    "target_title": target_title,
                    "document_number": doc_number,
                    "eli_uri": eli_uri,
                    "dogc_number": dogc_number,
                    "type_of_law": type_of_law,
                    "details": rel_props.get("details") or "",
                    "cited_text": rel_props.get("cited_text") or "",
                    "citation_reason": rel_props.get("citation_reason") or ""
                })
        return existing

    def get_existing_edges_between(self, source_id, target_id):
        """
        Retrieves all existing outgoing edges from source_id to target_id in the graph.
        """
        citation_types = {"CITES", "AFFECTS", "ABROGATES", "MODIFIES", "CONSOLIDATES", "MODIFY", "ABROGATE"}
        existing = []
        for rel in self.graph_data.get("relationships", []):
            if rel.get("source") == source_id and rel.get("target") == target_id and rel.get("type") in citation_types:
                rel_props = rel.get("properties", {}) or {}
                existing.append({
                    "rel_ref": rel,
                    "relationship_type": rel.get("type"),
                    "details": rel_props.get("details", ""),
                    "cited_text": rel_props.get("cited_text", ""),
                    "citation_reason": rel_props.get("citation_reason", "")
                })
        return existing

    def query_candidate_target_nodes(self, citation_item, max_candidates=25):
        """
        Queries the graph JSON for candidate target nodes matching citation_item details:
        1. Filters by year first.
        2. Filters by law type and document number.
        3. If there is a match, checks for matching articles/sections in graph.
           If a precise article/section match exists, includes that article/section node.
           Also extracts all possible section/article nodes under candidate laws as candidates,
           as well as parent law nodes.
        """
        if not self.document_nodes:
            return []

        year = str(citation_item.get("year") or "").strip()
        doc_type = str(citation_item.get("doc_type") or "").strip().lower()
        doc_num = str(citation_item.get("document_number") or "").strip().lower()
        dogc_num = str(citation_item.get("dogc_number") or "").strip().lower()
        raw_text = str(citation_item.get("raw_citation_text") or "").strip().lower()
        art_sec = str(citation_item.get("article_or_section") or "").strip().lower()

        # Step 1: Filter document nodes by year first if year is provided
        year_matching_docs = []
        if year and re.match(r"^\d{4}$", year):
            for doc in self.document_nodes:
                props = doc.get("properties", {})
                doc_date = str(props.get("documentDate") or "")
                title = str(props.get("titleEs") or props.get("titleCa") or props.get("title") or "").lower()
                eli = str(props.get("eliUri") or "").lower()
                d_num = str(props.get("documentNumber") or "").lower()
                
                if year in doc_date or year in title or year in eli or year in d_num:
                    year_matching_docs.append(doc)
        
        # Fall back to all document nodes if no docs matched by year (or year was not specified)
        base_docs = year_matching_docs if year_matching_docs else self.document_nodes

        # Step 2: Score & filter candidate document nodes by doc_number, dogc_number, doc_type, title
        scored_docs = []
        for doc in base_docs:
            props = doc.get("properties", {})
            score = 0
            n_doc_num = str(props.get("documentNumber") or "").lower()
            n_dogc_num = str(props.get("dogcNumber") or "").lower()
            n_type = str(props.get("typeOfLaw") or "").lower()
            labels = [l.lower() for l in doc.get("labels", [])]
            title = str(props.get("titleEs") or props.get("titleCa") or props.get("title") or "").lower()

            if doc_num and doc_num in n_doc_num:
                score += 100
            elif doc_num and doc_num in title:
                score += 80
            elif doc_num and doc_num in raw_text:
                score += 60

            if dogc_num and dogc_num == n_dogc_num:
                score += 90
            elif dogc_num and dogc_num in raw_text:
                score += 50

            if doc_type:
                if doc_type in n_type or any(doc_type in l for l in labels):
                    score += 30

            if score > 0:
                scored_docs.append((score, doc))

        # Sort candidate document nodes by score
        scored_docs.sort(key=lambda x: x[0], reverse=True)
        top_docs = [doc for _, doc in scored_docs[:10]]

        # If no scored docs found, fallback to searching for title keywords in base_docs
        if not top_docs and raw_text:
            text_words = set(re.findall(r"\b[a-zA-Zà-üÀ-Ü]{4,}\b", raw_text))
            stopwords = {"para", "como", "sobre", "entre", "este", "esta", "cada", "ley", "llei", "decret", "decreto", "articulo", "article"}
            words = text_words - stopwords
            for doc in base_docs[:50]:
                props = doc.get("properties", {})
                title = str(props.get("titleEs") or props.get("titleCa") or props.get("title") or "").lower()
                if any(w in title for w in words):
                    top_docs.append(doc)
                if len(top_docs) >= 5:
                    break

        # Step 3: Expand candidate list with precise article matches and section/article nodes
        candidates = []
        seen_candidate_ids = set()

        norm_art_sec = self._normalize_section_title(art_sec) if art_sec else ""

        for doc in top_docs:
            doc_id = doc["id"]
            props = doc.get("properties", {})
            doc_title = props.get("titleEs") or props.get("titleCa") or props.get("title") or f"Document {doc_id}"
            
            # Add document node itself
            if doc_id not in seen_candidate_ids:
                candidates.append({
                    "node_id": doc_id,
                    "node_type": "Document",
                    "title": doc_title,
                    "document_number": props.get("documentNumber", ""),
                    "type_of_law": props.get("typeOfLaw", "Document"),
                    "details": "",
                    "parent_doc_id": None,
                    "parent_doc_title": "",
                    "is_precise_article_match": False
                })
                seen_candidate_ids.add(doc_id)

            # Retrieve section / article nodes under this document
            sections = self.sections_by_document.get(doc_id, [])
            for sec in sections:
                sec_id = sec["id"]
                if sec_id in seen_candidate_ids:
                    continue

                sec_props = sec.get("properties", {})
                sec_title = sec_props.get("titleEs") or sec_props.get("titleCa") or sec_props.get("title") or sec_props.get("heading") or f"Section {sec_id}"
                norm_sec_title = self._normalize_section_title(sec_title)

                is_precise = False
                if norm_art_sec and norm_sec_title:
                    if norm_art_sec == norm_sec_title or norm_sec_title.startswith(norm_art_sec):
                        is_precise = True

                candidates.append({
                    "node_id": sec_id,
                    "node_type": "Article/Section",
                    "title": sec_title,
                    "document_number": props.get("documentNumber", ""),
                    "type_of_law": sec_props.get("type", "Article"),
                    "details": sec_title,
                    "parent_doc_id": doc_id,
                    "parent_doc_title": doc_title,
                    "is_precise_article_match": is_precise
                })
                seen_candidate_ids.add(sec_id)

        # Sort candidate list: precise article matches first, then Document/Section order
        candidates.sort(key=lambda c: (not c["is_precise_article_match"], c["node_type"] != "Article/Section"))
        return candidates[:max_candidates]

    def resolve_target_node(self, title, doc_number, eli_uri, dogc_number):
        if eli_uri:
            clean_eli = self._normalize_eli(eli_uri)
            if clean_eli in self.nodes_by_eli:
                return self.nodes_by_eli[clean_eli]
                
        if doc_number:
            clean_num = self._normalize_str(doc_number)
            if clean_num in self.nodes_by_doc_number:
                return self.nodes_by_doc_number[clean_num]
                
        if title:
            clean_title = self._normalize_str(title)
            if clean_title in self.nodes_by_title:
                return self.nodes_by_title[clean_title]
                
        if dogc_number:
            clean_dogc = self._normalize_str(str(dogc_number))
            if clean_dogc in self.nodes_by_dogc_number:
                return self.nodes_by_dogc_number[clean_dogc]
                
        if title:
            clean_title = self._normalize_str(title)
            if len(clean_title) > 10:
                for existing_clean_title, node_id in self.nodes_by_title.items():
                    if clean_title in existing_clean_title or existing_clean_title in clean_title:
                        return node_id
                        
        return None

    def match_section_in_document(self, doc_id, details):
        if not details:
            return None
            
        sections = self.sections_by_document.get(doc_id, [])
        if not sections:
            return None
            
        norm_details = self._normalize_section_title(details)
        if not norm_details:
            return None
            
        for sec in sections:
            sec_props = sec.get("properties", {})
            for title_key in ["title", "titleEs", "titleCa", "heading", "headingEs", "headingCa"]:
                val = sec_props.get(title_key)
                if val:
                    norm_val = self._normalize_section_title(val)
                    if norm_val and norm_val == norm_details:
                        return sec["id"]
                        
        for sec in sections:
            sec_props = sec.get("properties", {})
            for title_key in ["title", "titleEs", "titleCa"]:
                val = sec_props.get(title_key)
                if val:
                    norm_val = self._normalize_section_title(val)
                    if norm_val and norm_val.startswith("art "):
                        val_words = norm_val.split()
                        details_words = norm_details.split()
                        if len(val_words) <= len(details_words):
                            if details_words[:len(val_words)] == val_words:
                                return sec["id"]
                        
        return None

    def create_new_target_node(self, title, doc_number, eli_uri, dogc_number, type_of_law, doc_date):
        self.max_node_id += 1
        new_id = self.max_node_id
        
        type_label = "Document"
        if type_of_law:
            clean_type = type_of_law.strip().capitalize()
            label_name = "".join(x.capitalize() for x in re.split(r"[\s_-]+", clean_type))
            if label_name:
                type_label = label_name
        
        labels = ["Document"]
        if type_label != "Document":
            labels.append(type_label)
            
        props = {
            "title": title or f"Document {doc_number or new_id}",
            "titleEs": title or "",
            "titleCa": title or "",
            "documentNumber": doc_number or "",
            "eliUri": eli_uri or "",
            "dogcNumber": dogc_number or "",
            "typeOfLaw": type_of_law or "Unknown",
            "documentDate": doc_date or "",
            "processed_by_llm_agent": True,
            "created_by_llm_agent": True,
            "section": "Disposicions generals",
            "url": ""
        }
        
        if eli_uri:
            props["url"] = f"https://portaljuridic.gencat.cat/{eli_uri.lstrip('/')}"
        elif dogc_number:
            props["url"] = f"https://dogc.gencat.cat/ca/document-del-dogc/index.html?dogcNumber={dogc_number}"
            
        new_node = {
            "id": new_id,
            "labels": labels,
            "properties": props
        }
        
        self.graph_data["nodes"].append(new_node)
        self.nodes_by_id[new_id] = new_node
        self.document_nodes.append(new_node)
        
        if title:
            clean_t = self._normalize_str(title)
            if clean_t:
                self.nodes_by_title[clean_t] = new_id
        if doc_number:
            clean_num = self._normalize_str(doc_number)
            if clean_num:
                self.nodes_by_doc_number[clean_num] = new_id
        if eli_uri:
            clean_eli = self._normalize_eli(eli_uri)
            if clean_eli:
                self.nodes_by_eli[clean_eli] = new_id
        if dogc_number:
            clean_dogc = self._normalize_str(str(dogc_number))
            if clean_dogc:
                self.nodes_by_dogc_number[clean_dogc] = new_id
                
        print(f"  [Created Target Node] ID={new_id} | Title='{props['title']}' | ELI='{props['eliUri']}'")
        return new_id

    def create_new_section_node(self, doc_id, details):
        self.max_node_id += 1
        new_id = self.max_node_id
        
        is_article = False
        details_clean = details.strip()
        
        if re.match(r"^(artículo|articulo|article|art|art\.)", details_clean, re.IGNORECASE):
            is_article = True
            
        labels = ["DocumentSection"]
        if is_article:
            labels.insert(0, "Article")
            
        title = details_clean[0].upper() + details_clean[1:] if details_clean else "Section"
        existing_count = len(self.sections_by_document.get(doc_id, []))
        sec_id_str = f"{doc_id}_sec_{existing_count + 1}"
        
        props = {
            "title": title,
            "titleEs": title,
            "titleCa": title,
            "heading": "",
            "headingEs": "",
            "headingCa": "",
            "sectionId": sec_id_str,
            "type": "Article" if is_article else "Section",
            "isBilingual": True,
            "processed_by_llm_agent": True,
            "created_by_llm_agent": True
        }
        
        new_node = {
            "id": new_id,
            "labels": labels,
            "properties": props
        }
        
        self.graph_data["nodes"].append(new_node)
        self.nodes_by_id[new_id] = new_node
        
        has_section_rel = {
            "source": doc_id,
            "target": new_id,
            "type": "HAS_SECTION",
            "properties": {
                "created_by": "LLM_Simple_Agent_V3",
                "timestamp": int(time.time())
            }
        }
        self.graph_data["relationships"].append(has_section_rel)
        
        if doc_id not in self.sections_by_document:
            self.sections_by_document[doc_id] = []
        self.sections_by_document[doc_id].append(new_node)
        
        print(f"  [Created Section Node] ID={new_id} under Doc={doc_id} | Title='{title}'")
        return new_id

    def _parse_json_list(self, text):
        if not text:
            return []
        text = text.strip()
        
        if "<think>" in text:
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        content = match.group(1) if match else text
        content = content.strip()
        
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                for val in data.values():
                    if isinstance(val, list):
                        return val
                return [data]
        except Exception:
            array_match = re.search(r"\[\s*\{.*\}\s*\]", content, re.DOTALL)
            if array_match:
                try:
                    return json.loads(array_match.group(0))
                except Exception:
                    pass
        return []

    def _parse_json_dict(self, text):
        if not text:
            return {}
        text = text.strip()
        
        if "<think>" in text:
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        content = match.group(1) if match else text
        content = content.strip()
        
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return data
            elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                return data[0]
        except Exception:
            dict_match = re.search(r"\{.*\}", content, re.DOTALL)
            if dict_match:
                try:
                    return json.loads(dict_match.group(0))
                except Exception:
                    pass
        return {}

    # =========================================================================
    # REFINED THREE-TASK PIPELINE
    # =========================================================================

    def step1_detect_and_decompose_citations(self, text):
        """
        TASK 1: Detect all legal citations with maximum detailed metadata.
        Extracts year, document/source type (local, DOGC, Edicte, Llei, etc.), document number,
        whether it is general or specific to articles, and splits compound article citations
        into individual discrete citation objects.
        """
        prompt = f"""You are an expert legal citation extraction and decomposition assistant for Spanish and Catalan law.
Your task is to analyze the source legal text and extract ALL raw legal citations and references with high precision and rich metadata.

### Source Legal Text:
\"\"\"
{text}
\"\"\"

### Detailed Extraction Rules:
1. **Detect Every Citation**: Extract every reference to another law, decree, order, resolution, edict, local ordinance, or gazette document.
2. **Detailed Metadata**: For each citation, extract:
   - `"raw_citation_text"`: The exact verbatim snippet from the text containing the citation.
   - `"year"`: The year of the cited document (e.g. "2008", "2015", or null if unknown/not mentioned).
   - `"doc_type"`: The document or source type (e.g. "Local", "DOGC", "Edicte", "Llei", "Decret", "Decret llei", "Reial decret", "Ordre", "Resolució", "Constitución").
   - `"document_number"`: The official document identifier number (e.g. "13/2008", "45/2021", or null).
   - `"dogc_number"`: Official gazette number if mentioned (e.g. "5123", or null).
   - `"eli_uri"`: Constructible ELI URI path if possible (e.g. "eli/es-ct/l/2008/11/05/13"), else null.
   - `"is_general"`: true if the citation refers to the law/document as a whole, false if it specifies particular articles or sections.
   - `"article_or_section"`: The specific article or section cited (e.g. "artículo 12", "artículo 5.1", "disposición adicional 2ª"), or null if general.
   - `"implied_relationship"`: Initial implied relationship (`CITES`, `MODIFY`, `ABROGATE`, `AFFECTS`). Default to `CITES`.

3. **Split Compound Articles**: If a citation references MULTIPLE articles or sections (e.g., "artículos 5, 8 y 12.3 de la Ley 13/2008"), SPLIT them into separate individual items in the output array (one item with `"article_or_section": "artículo 5"`, one for `"artículo 8"`, and one for `"artículo 12.3"`).

### Output Format:
Output ONLY a raw JSON array of objects. Example:
[
  {{
    "raw_citation_text": "artículos 5 y 8 de la Ley 13/2008",
    "year": "2008",
    "doc_type": "Llei",
    "document_number": "13/2008",
    "dogc_number": null,
    "eli_uri": "eli/es-ct/l/2008/11/05/13",
    "is_general": false,
    "article_or_section": "artículo 5",
    "implied_relationship": "CITES"
  }},
  {{
    "raw_citation_text": "artículos 5 y 8 de la Ley 13/2008",
    "year": "2008",
    "doc_type": "Llei",
    "document_number": "13/2008",
    "dogc_number": null,
    "eli_uri": "eli/es-ct/l/2008/11/05/13",
    "is_general": false,
    "article_or_section": "artículo 8",
    "implied_relationship": "CITES"
  }}
]

Return [] if no legal citations are present.
Do not include any explanations or markdown formatting outside the JSON array.
"""
        try:
            response = self.vllm_client.chat.completions.create(
                model=self.args.vllm_model_name,
                messages=[
                    {"role": "system", "content": "You are a legal citation extraction agent. Output strictly JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=2000
            )
            return self._parse_json_list(response.choices[0].message.content)
        except Exception as e:
            print(f"Error in Task 1 (Detection & Decomposition): {e}")
            return []

    def step2_match_candidate_node(self, text_snippet, citation_item, candidates):
        """
        TASK 2: Ask LLM to match the extracted citation with the correct target candidate node from graph.
        Takes a single detailed citation item and the pre-filtered candidate nodes list from graph.
        """
        if not candidates:
            return {"matched_node_id": None, "match_confidence": "NONE", "reasoning": "No candidate nodes retrieved."}

        prompt = f"""You are an expert legal citation matching assistant for Spanish and Catalan law.
Your task is to match an extracted legal citation against a pre-filtered list of candidate knowledge graph nodes.

### Source Text Context:
\"\"\"
{text_snippet}
\"\"\"

### Extracted Citation to Match:
{json.dumps(citation_item, ensure_ascii=False, indent=2)}

### Candidate Knowledge Graph Target Nodes:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

---

### Instructions:
1. Examine the extracted citation metadata (`raw_citation_text`, `year`, `doc_type`, `document_number`, `article_or_section`, etc.) and compare it with the candidate nodes.
2. Select the single best matching `node_id` from the Candidate Target Nodes.
3. If the citation specifically targets an article or section and a matching Article/Section candidate node exists (or has `is_precise_article_match: true`), select that Article/Section `node_id`. If only the parent Document node exists, select the Document `node_id`.
4. If NONE of the candidate nodes accurately match the citation, set `"matched_node_id": null`.

### Output Format:
Output ONLY a raw JSON object with the following fields:
{{
  "matched_node_id": <integer node_id or null>,
  "match_confidence": "EXACT" | "HIGH" | "MEDIUM" | "LOW" | "NONE",
  "reasoning": "<short sentence explaining the match decision>"
}}
Do not include any text or markdown outside the JSON object.
"""
        try:
            response = self.vllm_client.chat.completions.create(
                model=self.args.vllm_model_name,
                messages=[
                    {"role": "system", "content": "You are a legal citation candidate matching agent. Output strictly JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=1000
            )
            return self._parse_json_dict(response.choices[0].message.content)
        except Exception as e:
            print(f"Error in Task 2 (Candidate Matching): {e}")
            return {"matched_node_id": None, "match_confidence": "NONE", "reasoning": str(e)}

    def step3_classify_and_reconcile_edge(self, source_node_id, source_text, target_node_id, target_node_info, citation_item, existing_edges):
        """
        TASK 3: Reconcile and classify the relationship edge between source node and target node.
        1. Compares with current edges: if link exists, asks if 2 links are needed or if current edge should be verified/fixed.
        2. Classifies edge type strictly as CITES (with specific context/reason), MODIFY, or ABROGATE.
        3. If edge type was wrong, fixes it and states "Fixed edge type from X to Y with LLM".
        """
        clean_existing = []
        for e in existing_edges:
            clean_existing.append({
                "relationship_type": e.get("relationship_type"),
                "details": e.get("details"),
                "cited_text": e.get("cited_text"),
                "citation_reason": e.get("citation_reason")
            })

        prompt = f"""You are an expert legal relationship classification and graph reconciliation agent.

### Context:
- **Source Node ID**: {source_node_id}
- **Source Text**:
\"\"\"
{source_text}
\"\"\"

- **Target Node ID**: {target_node_id} ({target_node_info.get('title', '')})
- **Target Node Type**: {target_node_info.get('type', '')}

- **Extracted Citation Details**:
{json.dumps(citation_item, ensure_ascii=False, indent=2)}

- **Existing Edges between Source Node {source_node_id} and Target Node {target_node_id} in Graph**:
{json.dumps(clean_existing, ensure_ascii=False, indent=2) if clean_existing else "[]"}

---

### Instructions:

1. **Edge Existence & Comparison**:
   - If there is ALREADY an edge between Source Node {source_node_id} and Target Node {target_node_id}:
     - Determine whether there should be **TWO separate links** (because the source text cites/modifies the target in two distinct contexts or for distinct reasons), OR if the existing edge should be verified/updated.
     - If NOT adding a second link: check if the existing edge's relationship type is correct.
       - If the existing edge type is wrong, set `"action": "FIX_EXISTING"`, set `"is_type_fixed": true`, and provide `"fix_explanation"` stating that the edge type was fixed with LLM (e.g. "Fixed edge type from CITES to MODIFY with LLM").
       - If the existing edge type is correct, set `"action": "VERIFY_EXISTING"`, `"is_type_fixed": false`, and set `"fix_explanation": "LLM verified current edge"`.
     - If adding a second distinct link, set `"action": "ADD_SECOND_EDGE"`.
   - If NO edge exists between Source Node {source_node_id} and Target Node {target_node_id}, set `"action": "CREATE_NEW_EDGE"`.

2. **Relationship Classification**:
   - Choose strictly from:
     - `CITES`: Simple citation or reference. You MUST provide a specific `"citation_reason"` / context explaining why it is cited (e.g. "Cita como antecedente o fundamento jurídico del procedimiento", "Menciona como norma de aplicación subsidiaria").
     - `MODIFY`: Amends, alters, updates, or deletes text of the target node.
     - `ABROGATE`: Repeals, annuls, or revokes the target node.

### Output Format:
Output ONLY a raw JSON object:
{{
  "action": "VERIFY_EXISTING" | "FIX_EXISTING" | "ADD_SECOND_EDGE" | "CREATE_NEW_EDGE",
  "existing_edge_index": <0-based integer index in Existing Edges list, or null>,
  "relationship_type": "CITES" | "MODIFY" | "ABROGATE",
  "citation_reason": "<specific context/reason for citing if type is CITES, else null>",
  "is_type_fixed": true | false,
  "fix_explanation": "<e.g. 'LLM verified current edge' or 'Fixed edge type from CITES to MODIFY with LLM'>"
}}
Do not include any text or markdown outside the JSON object.
"""
        try:
            response = self.vllm_client.chat.completions.create(
                model=self.args.vllm_model_name,
                messages=[
                    {"role": "system", "content": "You are a legal relationship classification agent. Output strictly JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=1500
            )
            return self._parse_json_dict(response.choices[0].message.content)
        except Exception as e:
            print(f"Error in Task 3 (Edge Reconciliation & Classification): {e}")
            return {
                "action": "CREATE_NEW_EDGE",
                "existing_edge_index": None,
                "relationship_type": citation_item.get("implied_relationship", "CITES"),
                "citation_reason": "Default fallback due to LLM error",
                "is_type_fixed": False,
                "fix_explanation": f"Error in LLM call: {e}"
            }

    # =========================================================================
    # MAIN EXTRACTION LOOP
    # =========================================================================

    def run_extraction_loop(self):
        self.load_graph()
        
        target_nodes = []
        for node in self.graph_data.get("nodes", []):
            labels = node.get("labels", [])
            if "DocumentSection" not in labels:
                continue
                
            props = node.get("properties", {})
            text = props.get("textEs") or props.get("textCa")
            if not text:
                continue
                
            if props.get("processed_by_simple_agent_v3"):
                continue
                
            target_nodes.append(node)
            
        print(f"\n[SimpleAgentV3] Found {len(target_nodes)} unprocessed DocumentSection nodes to analyze.")
        
        if self.args.max_nodes:
            target_nodes = target_nodes[:self.args.max_nodes]
            print(f"Limiting execution to first {len(target_nodes)} nodes as requested.")
            
        if not target_nodes:
            print("No nodes to process. Exiting.")
            return

        processed_count = 0
        total_kept = 0
        total_modified = 0
        total_added = 0
        new_nodes_created = 0
        
        pbar = tqdm(target_nodes, desc="Processing nodes (Refined V3 Pipeline)")
        for node in pbar:
            if self.interrupted:
                break
                
            node_id = node["id"]
            props = node.get("properties", {})
            text = props.get("textEs") or props.get("textCa")
            
            # --- TASK 1: Detect & Decompose Detailed Citations ---
            raw_citations = self.step1_detect_and_decompose_citations(text)
            
            if not raw_citations:
                existing_citations = self.get_existing_citations_for_node(node_id)
                if not existing_citations:
                    node["properties"]["processed_by_simple_agent_v3"] = True
                    node["properties"]["processed_by_llm_agent"] = True
                    processed_count += 1
                    continue

            node_kept = 0
            node_modified = 0
            node_added = 0

            # Process each decomposed citation item through Task 2 and Task 3
            for cit_item in raw_citations:
                if not isinstance(cit_item, dict):
                    continue

                # --- TASK 2: Retrieve Graph Candidates & Match via LLM ---
                candidates = self.query_candidate_target_nodes(cit_item, max_candidates=self.args.max_candidates)
                match_res = self.step2_match_candidate_node(text, cit_item, candidates)
                
                matched_node_id = match_res.get("matched_node_id")
                final_target_id = None

                art_sec = cit_item.get("article_or_section") or ""
                cited_title = cit_item.get("raw_citation_text") or ""
                doc_number = cit_item.get("document_number") or ""
                eli_uri = cit_item.get("eli_uri") or ""
                dogc_number = cit_item.get("dogc_number") or ""
                type_of_law = cit_item.get("doc_type") or ""
                doc_date = cit_item.get("year") or ""

                if matched_node_id and matched_node_id in self.nodes_by_id:
                    matched_node = self.nodes_by_id[matched_node_id]
                    matched_labels = matched_node.get("labels", [])

                    if "DocumentSection" in matched_labels or "Article" in matched_labels:
                        final_target_id = matched_node_id
                    else:
                        # Matched a Document node; refine to article/section if specified
                        if art_sec:
                            sec_id = self.match_section_in_document(matched_node_id, art_sec)
                            if sec_id is not None:
                                final_target_id = sec_id
                            else:
                                sec_id = self.create_new_section_node(matched_node_id, art_sec)
                                final_target_id = sec_id
                                new_nodes_created += 1
                        else:
                            final_target_id = matched_node_id
                else:
                    # Fallback node resolution/creation if LLM returned null match
                    target_doc_id = self.resolve_target_node(cited_title, doc_number, eli_uri, dogc_number)
                    if target_doc_id is None:
                        target_doc_id = self.create_new_target_node(
                            title=cited_title,
                            doc_number=doc_number,
                            eli_uri=eli_uri,
                            dogc_number=dogc_number,
                            type_of_law=type_of_law,
                            doc_date=doc_date
                        )
                        new_nodes_created += 1

                    final_target_id = target_doc_id
                    if art_sec:
                        sec_id = self.match_section_in_document(target_doc_id, art_sec)
                        if sec_id is not None:
                            final_target_id = sec_id
                        else:
                            sec_id = self.create_new_section_node(target_doc_id, art_sec)
                            final_target_id = sec_id
                            new_nodes_created += 1

                if final_target_id is None:
                    continue

                target_node_obj = self.nodes_by_id.get(final_target_id, {})
                t_props = target_node_obj.get("properties", {}) if target_node_obj else {}
                target_info = {
                    "title": t_props.get("titleEs") or t_props.get("titleCa") or t_props.get("title") or f"Node {final_target_id}",
                    "type": t_props.get("typeOfLaw") or t_props.get("type") or "Document"
                }

                # --- TASK 3: Reconcile and Classify Edge ---
                existing_edges = self.get_existing_edges_between(node_id, final_target_id)
                reconcile_res = self.step3_classify_and_reconcile_edge(
                    source_node_id=node_id,
                    source_text=text,
                    target_node_id=final_target_id,
                    target_node_info=target_info,
                    citation_item=cit_item,
                    existing_edges=existing_edges
                )

                action = str(reconcile_res.get("action", "CREATE_NEW_EDGE")).strip().upper()
                rel_type = reconcile_res.get("relationship_type", "CITES")
                if rel_type not in ["CITES", "MODIFY", "ABROGATE"]:
                    rel_type = "CITES"
                reason = reconcile_res.get("citation_reason") or ""
                is_fixed = reconcile_res.get("is_type_fixed", False)
                explanation = reconcile_res.get("fix_explanation") or "Verified by LLM"
                edge_idx = reconcile_res.get("existing_edge_index")

                if action in ("VERIFY_EXISTING", "FIX_EXISTING") and existing_edges:
                    target_rel = None
                    if isinstance(edge_idx, int) and 0 <= edge_idx < len(existing_edges):
                        target_rel = existing_edges[edge_idx]["rel_ref"]
                    else:
                        target_rel = existing_edges[0]["rel_ref"]

                    target_rel["type"] = rel_type
                    if "properties" not in target_rel or target_rel["properties"] is None:
                        target_rel["properties"] = {}
                    
                    target_rel["properties"]["details"] = art_sec
                    target_rel["properties"]["cited_text"] = cit_item.get("raw_citation_text", "")
                    if reason:
                        target_rel["properties"]["citation_reason"] = reason
                    target_rel["properties"]["llm_verification"] = explanation

                    if action == "FIX_EXISTING" or is_fixed:
                        target_rel["properties"]["verified_and_modified_by_v3"] = True
                        target_rel["properties"]["fixed_by_llm"] = True
                        node_modified += 1
                        total_modified += 1
                    else:
                        target_rel["properties"]["verified_by_v3"] = True
                        node_kept += 1
                        total_kept += 1

                else:
                    # ADD_SECOND_EDGE or CREATE_NEW_EDGE
                    new_rel = {
                        "source": node_id,
                        "target": final_target_id,
                        "type": rel_type,
                        "properties": {
                            "extracted_by": "LLM_Simple_Agent_V3",
                            "cited_text": cit_item.get("raw_citation_text", ""),
                            "details": art_sec,
                            "citation_reason": reason if rel_type == "CITES" else "",
                            "llm_action": action,
                            "llm_verification": explanation,
                            "timestamp": int(time.time())
                        }
                    }
                    self.graph_data["relationships"].append(new_rel)
                    node_added += 1
                    total_added += 1

            node["properties"]["processed_by_simple_agent_v3"] = True
            node["properties"]["processed_by_llm_agent"] = True
            node["properties"]["v3_citations_kept"] = node_kept
            node["properties"]["v3_citations_modified"] = node_modified
            node["properties"]["v3_citations_added"] = node_added
            
            processed_count += 1
            pbar.set_postfix({
                "Kept": total_kept,
                "Mod": total_modified,
                "Add": total_added,
                "NewNodes": new_nodes_created
            })
            
            if processed_count % self.args.batch_size == 0:
                print(f"\n[SimpleAgentV3] Saving checkpoint at {processed_count} processed nodes...")
                self.save_graph()
                
        print(f"\n[SimpleAgentV3] Finished execution. Processed {processed_count} nodes.")
        print(f"Stats: Kept={total_kept}, Modified={total_modified}, Added={total_added}, New Nodes={new_nodes_created}.")
        self.save_graph()
        self.generate_report()

    def generate_report(self, report_path=None):
        """
        Generates a comprehensive audit report of graph modifications made by SimpleAgentV3.
        Saves both JSON and Markdown summaries.
        """
        self.load_graph()
        
        nodes = self.graph_data.get("nodes", [])
        rels = self.graph_data.get("relationships", [])
        
        v3_processed_nodes = []
        created_nodes = []
        node_labels_dist = {}
        created_labels_dist = {}
        
        for node in nodes:
            labels_str = ":".join(sorted(node.get("labels", [])))
            node_labels_dist[labels_str] = node_labels_dist.get(labels_str, 0) + 1
            
            props = node.get("properties", {}) or {}
            if props.get("processed_by_simple_agent_v3"):
                v3_processed_nodes.append(node)
            if props.get("created_by_llm_agent"):
                created_nodes.append(node)
                created_labels_dist[labels_str] = created_labels_dist.get(labels_str, 0) + 1
                
        total_kept = sum(n["properties"].get("v3_citations_kept", 0) for n in v3_processed_nodes)
        total_modified = sum(n["properties"].get("v3_citations_modified", 0) for n in v3_processed_nodes)
        total_added = sum(n["properties"].get("v3_citations_added", 0) for n in v3_processed_nodes)
        
        rel_type_dist = {}
        rel_origin_dist = {
            "verified_by_v3 (KEEP)": 0,
            "modified_by_v3 (MODIFY/FIX)": 0,
            "extracted_by_v3 (ADD)": 0,
            "original_or_other": 0
        }
        
        for rel in rels:
            rtype = rel.get("type", "UNKNOWN")
            rel_type_dist[rtype] = rel_type_dist.get(rtype, 0) + 1
            
            props = rel.get("properties", {}) or {}
            if props.get("extracted_by") == "LLM_Simple_Agent_V3":
                rel_origin_dist["extracted_by_v3 (ADD)"] += 1
            elif props.get("verified_and_modified_by_v3"):
                rel_origin_dist["modified_by_v3 (MODIFY/FIX)"] += 1
            elif props.get("verified_by_v3"):
                rel_origin_dist["verified_by_v3 (KEEP)"] += 1
            else:
                rel_origin_dist["original_or_other"] += 1
                
        report_data = {
            "summary": {
                "total_nodes_in_graph": len(nodes),
                "total_relationships_in_graph": len(rels),
                "nodes_processed_by_v3": len(v3_processed_nodes),
                "nodes_created_by_llm": len(created_nodes),
            },
            "citation_actions_breakdown": {
                "KEEP": total_kept,
                "MODIFY": total_modified,
                "ADD": total_added
            },
            "created_nodes_labels_breakdown": created_labels_dist,
            "relationships_type_breakdown": rel_type_dist,
            "relationships_provenance_breakdown": rel_origin_dist
        }
        
        base_out = report_path or self.output_json.with_suffix(".report.json")
        base_out = Path(base_out)
        
        with open(base_out, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
            
        md_path = base_out.with_suffix(".md")
        md_content = f"""# SimpleAgentV3 Audit & Inferential Execution Report

## Executive Summary
- **Target Graph File**: `{self.output_json}`
- **Total Nodes in Graph**: {len(nodes):,}
- **Total Relationships in Graph**: {len(rels):,}
- **Text Section Nodes Processed by V3 Pipeline**: {len(v3_processed_nodes):,}
- **New Nodes Created by LLM Agent**: {len(created_nodes):,}

---

## Citation Evaluation Actions Breakdown
| Action Mode | Description | Count |
| :--- | :--- | :--- |
| **KEEP / VERIFY** | Existing graph citation verified as accurate | **{total_kept}** |
| **MODIFY / FIX** | Existing citation corrected by LLM (relationship type, target document, or specific article details) | **{total_modified}** |
| **ADD / NEW** | Newly detected citation extracted and added to graph | **{total_added}** |

---

## LLM-Created Nodes Breakdown ({len(created_nodes)} Nodes)
| Node Label(s) | Count |
| :--- | :--- |
"""
        for lbl, count in sorted(created_labels_dist.items(), key=lambda x: x[1], reverse=True):
            md_content += f"| `{lbl}` | {count} |\n"
            
        md_content += """
---

## Graph Relationship Provenance
| Provenance Category | Relationship Count |
| :--- | :--- |
"""
        for category, count in rel_origin_dist.items():
            md_content += f"| {category} | {count:,} |\n"

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
            
        print(f"\n[SimpleAgentV3 Report] Successfully generated summary report:")
        print(f"  JSON Report: {base_out}")
        print(f"  Markdown Report: {md_path}")
        
        self.generate_node_level_report(base_out.with_suffix(".node_report.json"))
        return report_data

    def generate_node_level_report(self, node_report_path=None):
        """
        Generates a detailed node-by-node audit report showing exact citations kept, modified (with BEFORE vs AFTER), and added per text section.
        """
        self.load_graph()
        
        input_nodes_by_id = {}
        input_rels_by_source = {}
        if self.input_json.exists():
            try:
                with open(self.input_json, "r", encoding="utf-8") as f_in:
                    input_data = json.load(f_in)
                    input_nodes_by_id = {n["id"]: n for n in input_data.get("nodes", [])}
                    for r in input_data.get("relationships", []):
                        src = r.get("source")
                        if src not in input_rels_by_source:
                            input_rels_by_source[src] = []
                        input_rels_by_source[src].append(r)
            except Exception as e:
                print(f"  Note: Could not load input_json for historical before-state lookup: {e}")

        nodes = self.graph_data.get("nodes", [])
        rels = self.graph_data.get("relationships", [])
        
        parent_doc_by_section_id = {}
        for r in rels:
            if r.get("type") == "HAS_SECTION":
                src_id = r.get("source")
                sec_id = r.get("target")
                p_node = self.nodes_by_id.get(src_id)
                if p_node:
                    parent_doc_by_section_id[sec_id] = p_node

        def _get_parent_info(target_nid):
            p_node = parent_doc_by_section_id.get(target_nid)
            if p_node:
                p_props = p_node.get("properties", {}) or {}
                p_title = p_props.get("titleEs") or p_props.get("titleCa") or p_props.get("title") or f"Document {p_node['id']}"
                return {"parent_id": p_node["id"], "parent_title": p_title}
            return None

        v3_processed_nodes = [n for n in nodes if n.get("properties", {}).get("processed_by_simple_agent_v3")]
        
        node_details = []
        for n in v3_processed_nodes:
            nid = n["id"]
            props = n.get("properties", {})
            title = props.get("titleEs") or props.get("titleCa") or props.get("title") or f"Node {nid}"
            text = props.get("textEs") or props.get("textCa") or ""
            
            outgoing = [r for r in rels if r.get("source") == nid]
            
            kept_list = []
            modified_list = []
            added_list = []
            
            for idx_r, r in enumerate(outgoing):
                target_id = r.get("target")
                target_node = self.nodes_by_id.get(target_id, {})
                target_props = target_node.get("properties", {}) if target_node else {}
                target_title = target_props.get("titleEs") or target_props.get("titleCa") or target_props.get("title") or f"Node {target_id}"
                
                r_props = r.get("properties", {}) or {}
                extracted_by = r_props.get("extracted_by")
                modified_by = r_props.get("verified_and_modified_by_v3")
                verified_by = r_props.get("verified_by_v3")
                
                rel_info = {
                    "target_id": target_id,
                    "target_title": target_title,
                    "relationship_type": r.get("type"),
                    "details": r_props.get("details", ""),
                    "cited_text": r_props.get("cited_text", ""),
                    "citation_reason": r_props.get("citation_reason", ""),
                    "llm_verification": r_props.get("llm_verification", "")
                }
                
                p_info = _get_parent_info(target_id)
                if p_info:
                    rel_info["parent_document_id"] = p_info["parent_id"]
                    rel_info["parent_document_title"] = p_info["parent_title"]
                
                if extracted_by == "LLM_Simple_Agent_V3":
                    added_list.append(rel_info)
                elif modified_by:
                    orig_state = r_props.get("v3_original_state")
                    if not orig_state and nid in input_rels_by_source:
                        orig_list = input_rels_by_source[nid]
                        if idx_r < len(orig_list):
                            orig_r = orig_list[idx_r]
                            o_target_id = orig_r.get("target")
                            o_target_node = input_nodes_by_id.get(o_target_id, {})
                            o_target_props = o_target_node.get("properties", {}) if o_target_node else {}
                            o_title = o_target_props.get("titleEs") or o_target_props.get("titleCa") or o_target_props.get("title") or f"Node {o_target_id}"
                            o_r_props = orig_r.get("properties", {}) or {}
                            orig_state = {
                                "type": orig_r.get("type"),
                                "target_id": o_target_id,
                                "target_title": o_title,
                                "details": o_r_props.get("details", ""),
                                "cited_text": o_r_props.get("cited_text", "")
                            }
                            o_p_info = _get_parent_info(o_target_id)
                            if o_p_info:
                                orig_state["parent_document_id"] = o_p_info["parent_id"]
                                orig_state["parent_document_title"] = o_p_info["parent_title"]
                    
                    rel_info["before_state"] = orig_state or {
                        "type": "UNKNOWN",
                        "target_id": "N/A",
                        "target_title": "Original edge details unavailable",
                        "details": "",
                        "cited_text": ""
                    }
                    modified_list.append(rel_info)
                elif verified_by:
                    kept_list.append(rel_info)

            node_details.append({
                "node_id": nid,
                "title": title,
                "text_snippet": text[:300] + "..." if len(text) > 300 else text,
                "stats": {
                    "kept": props.get("v3_citations_kept", 0),
                    "modified": props.get("v3_citations_modified", 0),
                    "added": props.get("v3_citations_added", 0),
                },
                "kept_citations": kept_list,
                "modified_citations": modified_list,
                "added_citations": added_list
            })

        out_json = node_report_path or self.output_json.with_suffix(".node_report.json")
        out_json = Path(out_json)
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(node_details, f, indent=2, ensure_ascii=False)

        out_md = out_json.with_suffix(".md")
        md_content = f"# SimpleAgentV3 Node-by-Node Citation Inspection Report\n\n"
        md_content += f"- **Target Graph File**: `{self.output_json}`\n"
        md_content += f"- **Total Processed Nodes**: {len(v3_processed_nodes)}\n\n"
        md_content += "---\n\n"

        def _format_target_str(c_item):
            t_str = f"Target Node `{c_item['target_id']}` (*{c_item['target_title']}*)"
            if c_item.get("parent_document_title"):
                t_str += f" | **Parent Law**: Node `{c_item['parent_document_id']}` (*{c_item['parent_document_title']}*)"
            return t_str

        for idx, item in enumerate(node_details, 1):
            stats = item["stats"]
            md_content += f"### {idx}. Node ID `{item['node_id']}`: {item['title']}\n"
            md_content += f"**Stats**: `Kept: {stats['kept']}` | `Modified/Fixed: {stats['modified']}` | `Added: {stats['added']}`\n\n"
            md_content += f"**Source Text Snippet**:\n> {item['text_snippet']}\n\n"
            
            if item["kept_citations"]:
                md_content += "**Verified & Kept Citations (VERIFY)**:\n"
                for c in item["kept_citations"]:
                    md_content += f"- `{c['relationship_type']}` -> {_format_target_str(c)} | Reason/Context: \"{c['citation_reason']}\" | Verification: `{c['llm_verification']}`\n"
                md_content += "\n"

            if item["modified_citations"]:
                md_content += "**Modified / Fixed Citations (FIX)**:\n"
                for c in item["modified_citations"]:
                    b = c.get("before_state", {})
                    md_content += f"- **BEFORE**: `{b.get('type')}` -> {_format_target_str(b)} | Details: `{b.get('details')}`\n"
                    md_content += f"  **AFTER** : `{c['relationship_type']}` -> {_format_target_str(c)} | Details: `{c['details']}` | Fix: `{c['llm_verification']}`\n\n"

            if item["added_citations"]:
                md_content += "**Newly Extracted Citations (ADD)**:\n"
                for c in item["added_citations"]:
                    md_content += f"- `{c['relationship_type']}` -> {_format_target_str(c)} | Text: \"{c['cited_text']}\" | Reason: \"{c['citation_reason']}\"\n"
                md_content += "\n"

            md_content += "---\n\n"

        with open(out_md, "w", encoding="utf-8") as f:
            f.write(md_content)

        print(f"\n[SimpleAgentV3 Report] Successfully generated summary report:")
        print(f"  JSON Report: {out_json}")
        print(f"  Markdown Report: {out_md}")
        return node_details


def main():
    parser = argparse.ArgumentParser(description="Multi-Step Simple Citation Agent V3")
    parser.add_argument("--input-json", default="data/extracted_subgraph_custom.json", help="Path to input graph JSON")
    parser.add_argument("--output-json", default="data/extracted_subgraph_custom_updated.json", help="Path to output graph JSON")
    parser.add_argument("--vllm-url", default="http://127.0.0.1:8000/v1", help="URL of vLLM Server")
    parser.add_argument("--vllm-model-name", default="/gpfs/projects/bsc100/models/DeepSeek-R1-Distill-Qwen-32B", help="Model path/name used on vLLM server")
    parser.add_argument("--max-candidates", type=int, default=25, help="Max candidate document/article nodes to retrieve for prompt context catalog")
    parser.add_argument("--batch-size", type=int, default=50, help="Checkpoints batch save size")
    parser.add_argument("--max-nodes", type=int, default=None, help="Max nodes to process")
    parser.add_argument("--generate-report-only", action="store_true", help="Only generate audit report for the output JSON without running vLLM")
    parser.add_argument("--report-out", default=None, help="Custom path for execution report JSON")
    
    args = parser.parse_args()
    
    agent = SimpleCitationAgentV3(args)
    if args.generate_report_only:
        agent.generate_report(args.report_out)
    else:
        agent.run_extraction_loop()


if __name__ == "__main__":
    main()
