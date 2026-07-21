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

# Third-party imports
try:
    from openai import OpenAI
    import httpx
except ImportError as e:
    print(f"Error importing dependencies: {e}")
    print("Please make sure 'openai' and 'httpx' are installed in your environment.")
    sys.exit(1)


class SimpleCitationAgentV2:
    def __init__(self, args):
        self.args = args
        self.input_json = Path(args.input_json)
        self.output_json = Path(args.output_json)
        
        # Initialize OpenAI client with proxy bypass
        self.vllm_client = OpenAI(
            base_url=args.vllm_url,
            api_key="none",
            http_client=httpx.Client(proxy=None)
        )
        
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

    def _handle_signal(self, signum, frame):
        print(f"\n[SimpleAgentV2] Received signal {signum}. Gracefully shutting down and saving progress...")
        self.interrupted = True

    def _normalize_str(self, s):
        if not s:
            return ""
        s = str(s).lower().strip()
        # Remove common punctuation/signs but keep letters, numbers, and slashes
        s = re.sub(r"[^\w\s/]", "", s)
        # Normalize whitespace
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
                if "Document" in labels or "DOGC" in labels or "Law" in labels or "Decree" in labels:
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
        Returns a list of formatted citation objects.
        """
        citation_types = {"CITES", "AFFECTS", "ABROGATES", "MODIFIES", "CONSOLIDATES"}
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
                    "cited_text": rel_props.get("cited_text") or ""
                })
        return existing

    def find_candidate_target_nodes(self, text, max_candidates=15):
        """
        Finds candidate existing Document nodes in the graph that match key terms,
        document numbers, dates, or titles present in the section text.
        This provides structural node context to the LLM to prevent title mismatching.
        """
        if not text or not self.document_nodes:
            return []

        clean_text = text.lower()
        
        # 1. Extract document numbers, years, and gazette numbers from text
        doc_nums = set(re.findall(r"\b\d{1,4}/\d{4}\b", clean_text))
        years = set(re.findall(r"\b(19\d\d|20\d\d)\b", clean_text))
        dogc_nums = set(re.findall(r"\bdogc\s*(?:nº|n\.º|number)?\s*(\d+)\b", clean_text))
        
        # Stopwords to ignore in title keyword scoring
        stopwords = {
            "de", "del", "la", "el", "los", "las", "en", "por", "para", "con", "sin",
            "sobre", "a", "i", "les", "dels", "un", "una", "ley", "llei", "decreto",
            "decret", "orden", "ordre", "resolucion", "resolucio", "real", "reial"
        }
        text_words = set(re.findall(r"\b[a-zA-Zà-üÀ-Ü]{4,}\b", clean_text)) - stopwords

        scored_nodes = []
        for node in self.document_nodes:
            props = node.get("properties", {})
            score = 0
            
            node_doc_num = str(props.get("documentNumber") or "").lower()
            node_dogc_num = str(props.get("dogcNumber") or "").lower()
            node_eli = str(props.get("eliUri") or "").lower()
            title = props.get("titleEs") or props.get("titleCa") or props.get("title") or ""
            norm_title = self._normalize_str(title)

            # Check exact document number matches
            if node_doc_num and node_doc_num in doc_nums:
                score += 100
            elif node_doc_num and node_doc_num in clean_text:
                score += 80

            # Check DOGC number matches
            if node_dogc_num and node_dogc_num in dogc_nums:
                score += 90
            elif node_dogc_num and node_dogc_num in clean_text:
                score += 60

            # Title substring or keyword overlap
            if norm_title:
                title_words = set(norm_title.split()) - stopwords
                overlap = len(title_words.intersection(text_words))
                if overlap > 0:
                    score += overlap * 5
                if norm_title in clean_text:
                    score += 50

            # Year match boost
            doc_date = str(props.get("documentDate") or "")
            for y in years:
                if y in doc_date or y in norm_title or y in node_eli:
                    score += 5

            if score > 0:
                scored_nodes.append((score, node))

        # Sort by score descending
        scored_nodes.sort(key=lambda x: x[0], reverse=True)
        top_nodes = scored_nodes[:max_candidates]

        formatted_candidates = []
        for score, node in top_nodes:
            props = node.get("properties", {})
            title = props.get("titleEs") or props.get("titleCa") or props.get("title") or f"Document {node['id']}"
            formatted_candidates.append({
                "node_id": node["id"],
                "title": title,
                "document_number": props.get("documentNumber", ""),
                "eli_uri": props.get("eliUri", ""),
                "dogc_number": props.get("dogcNumber", ""),
                "type_of_law": props.get("typeOfLaw", "Document")
            })

        return formatted_candidates

    def resolve_target_node(self, title, doc_number, eli_uri, dogc_number):
        """
        Tries to map citation fields to an existing node ID.
        """
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
        """
        Tries to find a section/article node inside doc_id matching details.
        """
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
        """
        Creates a new target document node and indexes it.
        """
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
        """
        Creates a new DocumentSection or Article node under doc_id.
        """
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
                "created_by": "LLM_Simple_Agent_V2",
                "timestamp": int(time.time())
            }
        }
        self.graph_data["relationships"].append(has_section_rel)
        
        if doc_id not in self.sections_by_document:
            self.sections_by_document[doc_id] = []
        self.sections_by_document[doc_id].append(new_node)
        
        print(f"  [Created Section Node] ID={new_id} under Doc={doc_id} | Title='{title}'")
        return new_id

    def query_llm_verify_and_extract_citations(self, text, existing_citations, candidate_nodes):
        """
        Queries the LLM providing:
        1. Source text
        2. Existing detected citations (to verify correctness, relationship type, target doc)
        3. Knowledge Graph Reference Catalog (candidate nodes present in graph structure to prevent title mismatching)
        """
        
        # Prepare clean existing citations array for prompt
        clean_existing = []
        for cit in existing_citations:
            clean_existing.append({
                "citation_id": cit["citation_id"],
                "relationship_type": cit["relationship_type"],
                "target_node_id": cit["target_node_id"],
                "target_title": cit["target_title"],
                "document_number": cit["document_number"],
                "eli_uri": cit["eli_uri"],
                "dogc_number": cit["dogc_number"],
                "details": cit["details"],
                "cited_text": cit["cited_text"]
            })

        prompt = f"""You are an expert legal assistant analyzing Spanish and Catalan legal texts.
Your task is to VERIFY existing detected citations for correctness and EXTRACT any missing citations.

### Source Legal Text to Analyze:
\"\"\"
{text}
\"\"\"

### 1. Existing Citations Already Detected for this Text:
{json.dumps(clean_existing, ensure_ascii=False, indent=2) if clean_existing else "[] (No existing citations detected yet)"}

### 2. Knowledge Graph Reference Catalog (Candidate Document Nodes):
Below is a list of candidate document nodes currently existing in our legal graph database:
{json.dumps(candidate_nodes, ensure_ascii=False, indent=2) if candidate_nodes else "[]"}

---

### Instructions:

1. **Verify Existing Citations**:
   - For EACH citation in "Existing Citations Already Detected", determine if it is accurate based on the Source Legal Text.
   - If it is correct (points to the right document, right relationship type, right details), set `"action": "KEEP"`.
   - If fields are incorrect or imprecise (e.g. wrong relationship type, wrong target node ID, wrong details/article), set `"action": "MODIFY"` and provide the corrected values.
   - If the citation is completely false, erroneous, or not supported by the source text, set `"action": "REMOVE"`.

2. **Detect Missing Citations**:
   - Identify any citations present in the Source Legal Text that are NOT listed in "Existing Citations Already Detected".
   - For each missing citation, add a new entry with `"action": "ADD"`.
   - If the cited document matches one of the nodes in the "Knowledge Graph Reference Catalog", set `"target_node_id"` to that node's `node_id`.
   - If the cited document is NOT in the Reference Catalog, set `"target_node_id": null`.

3. **Field Guidelines**:
   - `"action"`: Must be one of `"KEEP"`, `"MODIFY"`, `"REMOVE"`, `"ADD"`.
   - `"existing_citation_id"`: The numeric ID from the existing citations list (if action is KEEP, MODIFY, or REMOVE), or `null` for ADD.
   - `"target_node_id"`: Integer node ID from the Reference Catalog or existing graph, or `null` if unknown/new document.
   - `"relationship_type"`: Choose from `CITES`, `AFFECTS`, `ABROGATES`, `MODIFIES`, `CONSOLIDATES`. Default to `CITES`.
   - `"cited_text"`: Exact phrase from source text (e.g. "Ley 13/2008, de 5 de noviembre").
   - `"cited_document_title"`: Clean canonical title of the law.
   - `"document_number"`: Identifier number (e.g. "13/2008").
   - `"document_date"`: Date in YYYY-MM-DD format if available, else "".
   - `"eli_uri"`: European Legislation Identifier URI path if constructible (e.g. `eli/es-ct/l/2008/11/05/13/dof`).
   - `"dogc_number"`: Official gazette number if present.
   - `"type_of_law"`: Document type (e.g. "Llei", "Decret", "Decret llei", "Ordre", "Reial decret").
   - `"details"`: Specific article or section cited (e.g. "artículo 12.1").

### Output Format:
Output ONLY a JSON array of citation evaluation objects. Return [] if no citations exist and no new ones are found.
Do not include any explanation or thinking tags outside the JSON array.
"""

        system_message = "You are an expert legal citation verification agent. You output strictly raw JSON lists."

        try:
            response = self.vllm_client.chat.completions.create(
                model=self.args.vllm_model_name,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=2500
            )
            content = response.choices[0].message.content
            return self._parse_json_list(content)
        except Exception as e:
            print(f"Error querying LLM: {e}")
            return []

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

    def run_extraction_loop(self):
        self.load_graph()
        
        # Filter nodes of interest: DocumentSection with text, not yet processed by v2
        target_nodes = []
        for node in self.graph_data.get("nodes", []):
            labels = node.get("labels", [])
            if "DocumentSection" not in labels:
                continue
                
            props = node.get("properties", {})
            text = props.get("textEs") or props.get("textCa")
            if not text:
                continue
                
            if props.get("processed_by_simple_agent_v2"):
                continue
                
            target_nodes.append(node)
            
        print(f"\nFound {len(target_nodes)} unprocessed DocumentSection nodes to analyze.")
        
        if self.args.max_nodes:
            target_nodes = target_nodes[:self.args.max_nodes]
            print(f"Limiting execution to first {len(target_nodes)} nodes as requested.")
            
        if not target_nodes:
            print("No nodes to process. Exiting.")
            return

        processed_count = 0
        total_kept = 0
        total_modified = 0
        total_removed = 0
        total_added = 0
        new_nodes_created = 0
        
        pbar = tqdm(target_nodes, desc="Processing nodes")
        for node in pbar:
            if self.interrupted:
                break
                
            node_id = node["id"]
            props = node.get("properties", {})
            text = props.get("textEs") or props.get("textCa")
            
            # 1. Get existing citations on this node
            existing_citations = self.get_existing_citations_for_node(node_id)
            
            # 2. Get candidate document nodes from graph structure
            candidate_nodes = self.find_candidate_target_nodes(text, max_candidates=self.args.max_candidates)
            
            # 3. Query LLM to verify existing and detect missing citations
            evaluations = self.query_llm_verify_and_extract_citations(text, existing_citations, candidate_nodes)
            
            node_kept = 0
            node_modified = 0
            node_removed = 0
            node_added = 0
            
            # Map existing citations by ID for fast lookup
            existing_by_cit_id = {c["citation_id"]: c for c in existing_citations}
            
            for item in evaluations:
                if not isinstance(item, dict):
                    continue
                    
                action = str(item.get("action", "KEEP")).strip().upper()
                cit_id = item.get("existing_citation_id")
                target_node_id = item.get("target_node_id")
                relationship_type = item.get("relationship_type", "CITES")
                cited_text = item.get("cited_text", "")
                cited_title = item.get("cited_document_title", "")
                doc_number = item.get("document_number", "")
                eli_uri = item.get("eli_uri", "")
                dogc_number = item.get("dogc_number", "")
                type_of_law = item.get("type_of_law", "")
                doc_date = item.get("document_date", "")
                details = item.get("details", "")
                
                # Handling REMOVE
                if action == "REMOVE" and cit_id in existing_by_cit_id:
                    rel_ref = existing_by_cit_id[cit_id]["rel_ref"]
                    if rel_ref in self.graph_data.get("relationships", []):
                        self.graph_data["relationships"].remove(rel_ref)
                        node_removed += 1
                        total_removed += 1
                
                # Handling MODIFY
                elif action == "MODIFY" and cit_id in existing_by_cit_id:
                    rel_ref = existing_by_cit_id[cit_id]["rel_ref"]
                    # Update relationship type
                    if relationship_type:
                        rel_ref["type"] = relationship_type
                    
                    # Update target node if valid target_node_id supplied or resolvable
                    new_target = target_node_id if (target_node_id and target_node_id in self.nodes_by_id) else None
                    if new_target is None and (cited_title or doc_number or eli_uri or dogc_number):
                        new_target = self.resolve_target_node(cited_title, doc_number, eli_uri, dogc_number)
                    if new_target is not None:
                        rel_ref["target"] = new_target
                        
                    # Update properties
                    if "properties" not in rel_ref or rel_ref["properties"] is None:
                        rel_ref["properties"] = {}
                    if details:
                        rel_ref["properties"]["details"] = details
                    if cited_text:
                        rel_ref["properties"]["cited_text"] = cited_text
                    rel_ref["properties"]["verified_and_modified_by_v2"] = True
                    
                    node_modified += 1
                    total_modified += 1
                
                # Handling KEEP
                elif action == "KEEP" and cit_id in existing_by_cit_id:
                    rel_ref = existing_by_cit_id[cit_id]["rel_ref"]
                    if "properties" not in rel_ref or rel_ref["properties"] is None:
                        rel_ref["properties"] = {}
                    rel_ref["properties"]["verified_by_v2"] = True
                    node_kept += 1
                    total_kept += 1
                
                # Handling ADD
                elif action == "ADD":
                    if not cited_title and not doc_number and not eli_uri and not dogc_number:
                        continue
                        
                    # Resolve target node: first check LLM target_node_id, then graph resolver
                    target_doc_id = None
                    if target_node_id and target_node_id in self.nodes_by_id:
                        target_doc_id = target_node_id
                    else:
                        target_doc_id = self.resolve_target_node(cited_title, doc_number, eli_uri, dogc_number)
                        
                    # Create new target node if non-existent in graph
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
                        
                    # Resolve or create specific article/section target
                    final_target_id = target_doc_id
                    if details:
                        sec_id = self.match_section_in_document(target_doc_id, details)
                        if sec_id is not None:
                            final_target_id = sec_id
                        else:
                            sec_id = self.create_new_section_node(target_doc_id, details)
                            final_target_id = sec_id
                            new_nodes_created += 1
                            
                    # Check if relationship already exists before adding
                    rel_exists = False
                    for rel in self.graph_data.get("relationships", []):
                        if (rel.get("source") == node_id and 
                            rel.get("target") == final_target_id and 
                            rel.get("type") == relationship_type):
                            rel_exists = True
                            break
                            
                    if not rel_exists:
                        new_rel = {
                            "source": node_id,
                            "target": final_target_id,
                            "type": relationship_type,
                            "properties": {
                                "extracted_by": "LLM_Simple_Agent_V2",
                                "cited_text": cited_text,
                                "details": details,
                                "timestamp": int(time.time())
                            }
                        }
                        self.graph_data["relationships"].append(new_rel)
                        node_added += 1
                        total_added += 1

            # Mark node as processed
            node["properties"]["processed_by_simple_agent_v2"] = True
            node["properties"]["processed_by_llm_agent"] = True
            node["properties"]["v2_citations_kept"] = node_kept
            node["properties"]["v2_citations_modified"] = node_modified
            node["properties"]["v2_citations_removed"] = node_removed
            node["properties"]["v2_citations_added"] = node_added
            
            processed_count += 1
            pbar.set_postfix({
                "Kept": total_kept,
                "Mod": total_modified,
                "Del": total_removed,
                "Add": total_added,
                "NewNodes": new_nodes_created
            })
            
            # Checkpoint save
            if processed_count % self.args.batch_size == 0:
                print(f"\n[SimpleAgentV2] Saving checkpoint at {processed_count} processed nodes...")
                self.save_graph()
                
        # Final save
        print(f"\n[SimpleAgentV2] Finished execution. Processed {processed_count} nodes.")
        print(f"Stats: Kept={total_kept}, Modified={total_modified}, Removed={total_removed}, Added={total_added}, New Nodes={new_nodes_created}.")
        self.save_graph()


def main():
    parser = argparse.ArgumentParser(description="Simple Citation Verification & Extraction Agent V2")
    parser.add_argument("--input-json", default="data/extracted_subgraph_custom.json", help="Path to input graph JSON")
    parser.add_argument("--output-json", default="data/extracted_subgraph_custom_updated.json", help="Path to output graph JSON")
    parser.add_argument("--vllm-url", default="http://127.0.0.1:8000/v1", help="URL of vLLM Server")
    parser.add_argument("--vllm-model-name", default="/gpfs/projects/bsc100/models/DeepSeek-R1-Distill-Qwen-32B", help="Model path/name used on vLLM server")
    parser.add_argument("--max-candidates", type=int, default=15, help="Max candidate document nodes to include in prompt context catalog")
    parser.add_argument("--batch-size", type=int, default=50, help="Checkpoints batch save size")
    parser.add_argument("--max-nodes", type=int, default=None, help="Max nodes to process")
    
    args = parser.parse_args()
    
    agent = SimpleCitationAgentV2(args)
    agent.run_extraction_loop()


if __name__ == "__main__":
    main()
