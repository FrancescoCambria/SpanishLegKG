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


class SimpleCitationAgent:
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
        
        # Lookup tables for mapping citations without RAG
        self.nodes_by_title = {}
        self.nodes_by_doc_number = {}
        self.nodes_by_eli = {}
        self.nodes_by_dogc_number = {}
        self.sections_by_document = {}
        self.max_node_id = 0
        
        # Keep track of active changes for signal handling
        self.interrupted = False
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        print(f"\n[SimpleAgent] Received signal {signum}. Gracefully shutting down and saving progress...")
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
        # Remove leading http/https and domain if any
        s = re.sub(r"^https?://[^/]+/", "", s)
        # Remove leading / or eli/
        s = re.sub(r"^/?(?:eli/)?", "", s)
        # Remove trailing /dof or /dof/...
        s = re.sub(r"/dof(?:/.*)?$", "", s)
        s = s.strip("/")
        return s

    def _normalize_section_title(self, text):
        if not text:
            return ""
        text = text.lower().strip()
        # Replace common words with abbreviations for normalization
        text = re.sub(r"\b(artículo|articulo|article|art|art\.)\b", "art", text)
        text = re.sub(r"\b(sección|seccion|section|sec|sec\.)\b", "sec", text)
        text = re.sub(r"\b(anexo|annex|an\.)\b", "annex", text)
        # Remove non-alphanumeric characters but keep spaces
        text = re.sub(r"[^\w\s]", "", text)
        # Normalize whitespace
        text = " ".join(text.split())
        return text

    def load_graph(self):
        if self.graph_data is None:
            # Check if updated file exists to resume from it
            target_path = self.output_json if self.output_json.exists() else self.input_json
            print(f"Loading graph data from {target_path}...")
            with open(target_path, "r", encoding="utf-8") as f:
                self.graph_data = json.load(f)
            
            print(f"Building node index by ID for {len(self.graph_data.get('nodes', []))} nodes...")
            self.nodes_by_id = {node["id"]: node for node in self.graph_data.get("nodes", [])}
            
            # Build lookup maps for fast mapping without RAG
            self.nodes_by_title = {}
            self.nodes_by_doc_number = {}
            self.nodes_by_eli = {}
            self.nodes_by_dogc_number = {}
            self.sections_by_document = {}
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
                if "Document" in labels or "DOGC" in labels:
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

            # Build list of sections for each document
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
            
            print(f"Indexed {len(self.nodes_by_title)} titles, {len(self.nodes_by_doc_number)} document numbers, "
                  f"{len(self.nodes_by_eli)} ELI URIs, {len(self.nodes_by_dogc_number)} DOGC numbers, "
                  f"and mapped sections for {len(self.sections_by_document)} documents.")
            print(f"Current maximum node ID is {self.max_node_id}.")
        return self.graph_data

    def save_graph(self, path=None):
        if self.graph_data is None:
            return
        save_path = path or self.output_json
        print(f"Saving graph data to {save_path}...")
        # Save to temp file first to prevent corruption
        temp_path = save_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self.graph_data, f, ensure_ascii=False, indent=2)
        temp_path.replace(save_path)
        print("Save completed successfully.")

    def resolve_target_node(self, title, doc_number, eli_uri, dogc_number):
        """
        Tries to map the citation fields to an existing node ID.
        Returns the node ID if resolved, otherwise None.
        """
        # 1. Match by ELI URI (highest precision)
        if eli_uri:
            clean_eli = self._normalize_eli(eli_uri)
            if clean_eli in self.nodes_by_eli:
                return self.nodes_by_eli[clean_eli]
                
        # 2. Match by Document Number
        if doc_number:
            clean_num = self._normalize_str(doc_number)
            if clean_num in self.nodes_by_doc_number:
                return self.nodes_by_doc_number[clean_num]
                
        # 3. Match by Title (exact after normalization)
        if title:
            clean_title = self._normalize_str(title)
            if clean_title in self.nodes_by_title:
                return self.nodes_by_title[clean_title]
                
        # 4. Match by DOGC Number
        if dogc_number:
            clean_dogc = self._normalize_str(str(dogc_number))
            if clean_dogc in self.nodes_by_dogc_number:
                return self.nodes_by_dogc_number[clean_dogc]
                
        # 5. Fuzzy match check - check if title or doc_number exists as a substring in existing titles/numbers
        if title:
            clean_title = self._normalize_str(title)
            if len(clean_title) > 10:  # avoid matching extremely short generic titles
                for existing_clean_title, node_id in self.nodes_by_title.items():
                    if clean_title in existing_clean_title or existing_clean_title in clean_title:
                        return node_id
                        
        return None

    def match_section_in_document(self, doc_id, details):
        """
        Tries to find a section/article node inside doc_id that matches details.
        Returns the section node ID if found, otherwise None.
        """
        if not details:
            return None
            
        sections = self.sections_by_document.get(doc_id, [])
        if not sections:
            return None
            
        norm_details = self._normalize_section_title(details)
        if not norm_details:
            return None
            
        # First pass: look for exact match of normalized titles
        for sec in sections:
            sec_props = sec.get("properties", {})
            for title_key in ["title", "titleEs", "titleCa", "heading", "headingEs", "headingCa"]:
                val = sec_props.get(title_key)
                if val:
                    norm_val = self._normalize_section_title(val)
                    if norm_val and norm_val == norm_details:
                        return sec["id"]
                        
        # Second pass: check if the section title is a prefix or subset of the details word-by-word
        # For example, section title "art 12" matches details "art 12 1 k"
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
        Creates a new target document node, appends it to self.graph_data['nodes'],
        updates index maps, and returns the new node's ID.
        """
        self.max_node_id += 1
        new_id = self.max_node_id
        
        # Standardize type of law and label capitalization
        type_label = "Document"
        if type_of_law:
            clean_type = type_of_law.strip().capitalize()
            label_name = "".join(x.capitalize() for x in re.split(r"[\s_-]+", clean_type))
            if label_name:
                type_label = label_name
        
        # Build node labels
        labels = ["Document"]
        if type_label != "Document":
            labels.append(type_label)
            
        # Build properties
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
        
        # If we have an ELI URI, we can populate url from it
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
        
        # Update lookup indexes so future citations of the same document map to it!
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
        Adds HAS_SECTION relationship between doc_id and the new section node.
        Returns the new section node ID.
        """
        self.max_node_id += 1
        new_id = self.max_node_id
        
        # Determine if it's an Article or a general Section
        is_article = False
        details_clean = details.strip()
        
        if re.match(r"^(artículo|articulo|article|art|art\.)", details_clean, re.IGNORECASE):
            is_article = True
            
        labels = ["DocumentSection"]
        if is_article:
            labels.insert(0, "Article")
            
        # Generate title
        title = details_clean[0].upper() + details_clean[1:] if details_clean else "Section"
        
        # Calculate section ID suffix/index
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
        
        # Add node
        self.graph_data["nodes"].append(new_node)
        self.nodes_by_id[new_id] = new_node
        
        # Add HAS_SECTION relationship
        has_section_rel = {
            "source": doc_id,
            "target": new_id,
            "type": "HAS_SECTION",
            "properties": {
                "created_by": "LLM_Simple_Agent",
                "timestamp": int(time.time())
            }
        }
        self.graph_data["relationships"].append(has_section_rel)
        
        # Update lookup tables
        if doc_id not in self.sections_by_document:
            self.sections_by_document[doc_id] = []
        self.sections_by_document[doc_id].append(new_node)
        
        print(f"  [Created Section Node] ID={new_id} under Doc={doc_id} | Title='{title}' | Labels={labels}")
        return new_id

    def query_llm_citations(self, text):
        prompt = f"""You are analyzing Spanish or Catalan legal texts. Your task is to extract citations to other laws, decrees, resolutions, or official gazette publications (e.g., DOGC, BOE).

For each citation, output a JSON object with the following fields:
1. "cited_text": The exact short snippet or phrase from the source text representing the citation (e.g., "Ley 13/2008", "Decreto 085/2026", "DOGC 9671").
2. "citation_type": The type of relationship. Choose from: CITES, AFFECTS, ABROGATES, MODIFIES, CONSOLIDATES. Default to CITES.
3. "cited_document_title": A clean canonical title of the cited document. Include the number and date if mentioned in the text.
4. "document_number": The official identifier/number of the law or decree if present (e.g., "13/2008", "085/2026").
5. "document_date": The date of the cited document in YYYY-MM-DD format if mentioned or can be derived from the text (e.g., "2008-11-05"). Use empty string if unknown.
6. "eli_uri": The European Legislation Identifier (ELI) URI path if the document has one. Construct it using standard templates:
   - Catalan regional laws: `eli/es-ct/l/{{year}}/{{month}}/{{day}}/{{number}}/dof`
   - Catalan regional decrees: `eli/es-ct/d/{{year}}/{{month}}/{{day}}/{{number}}/dof`
   - Catalan regional decree-laws: `eli/es-ct/dl/{{year}}/{{month}}/{{day}}/{{number}}/dof`
   - Catalan regional orders: `eli/es-ct/o/{{year}}/{{month}}/{{day}}/{{number}}/dof`
   - State laws: `eli/es/l/{{year}}/{{month}}/{{day}}/{{number}}/dof`
   - State royal decrees: `eli/es/rd/{{year}}/{{month}}/{{day}}/{{number}}/dof`
   (Substitute year/month/day/number from the citation if available. If exact month/day are missing, use best guess or represent as `eli/es-ct/l/{{year}}/{{number}}/dof`).
7. "dogc_number": The Diari Oficial de la Generalitat de Catalunya publication number if mentioned (e.g. "9671").
8. "type_of_law": The category of the document (e.g., "Llei", "Decret", "Decret llei", "Decret legislatiu", "Ordre", "Reial decret", "Resolució", "DOGC").
9. "details": Specific articles, sections, or paragraphs cited (e.g. "artículo 12.1.k", "art. 20").

### Examples:

Example 1:
Text: "...conforme a lo establecido en la Ley 13/2008, de 5 de noviembre, de la presidencia de la Generalidad y del Gobierno..."
Output:
[
  {{
    "cited_text": "Ley 13/2008, de 5 de noviembre",
    "citation_type": "CITES",
    "cited_document_title": "Ley 13/2008, de 5 de noviembre, de la presidencia de la Generalidad y del Gobierno",
    "document_number": "13/2008",
    "document_date": "2008-11-05",
    "eli_uri": "eli/es-ct/l/2008/11/05/13/dof",
    "dogc_number": "",
    "type_of_law": "Llei",
    "details": ""
  }}
]

Example 2:
Text: "...de conformidad con el Decreto ley 10/2020, de 27 de marzo, y publicado en el DOGC nº 9671..."
Output:
[
  {{
    "cited_text": "Decreto ley 10/2020, de 27 de marzo",
    "citation_type": "CITES",
    "cited_document_title": "Decreto ley 10/2020, de 27 de marzo",
    "document_number": "10/2020",
    "document_date": "2020-03-27",
    "eli_uri": "eli/es-ct/dl/2020/03/27/10/dof",
    "dogc_number": "9671",
    "type_of_law": "Decret llei",
    "details": ""
  }}
]

### Output Format:
Output ONLY a valid JSON list of objects. If no citations are found, return an empty list: [].
Do not include any thinking tags or markdown code wrappers in your final answer, just the JSON list.

### Source Legal Text to Analyze:
\"\"\"
{text}
\"\"\""""

        system_message = "You are an expert legal assistant that outputs only raw JSON lists of citations."
        
        try:
            response = self.vllm_client.chat.completions.create(
                model=self.args.vllm_model_name,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=2048
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
        
        # Remove thinking section if present in the main content
        if "<think>" in text:
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            
        # Remove markdown code block wrappers
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
            # Fallback: try to extract JSON array using regex
            array_match = re.search(r"\[\s*\{.*\}\s*\]", content, re.DOTALL)
            if array_match:
                try:
                    return json.loads(array_match.group(0))
                except Exception:
                    pass
        return []

    def run_extraction_loop(self):
        self.load_graph()
        
        # Filter nodes of interest: DocumentSection with text, not yet processed
        target_nodes = []
        for node in self.graph_data.get("nodes", []):
            labels = node.get("labels", [])
            if "DocumentSection" not in labels:
                continue
                
            props = node.get("properties", {})
            text = props.get("textEs") or props.get("textCa")
            if not text:
                continue
                
            # Skip if already processed by simple agent or general llm agent
            if props.get("processed_by_llm_agent") or props.get("processed_by_simple_agent"):
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
        new_relationships_added = 0
        new_nodes_created = 0
        
        pbar = tqdm(target_nodes, desc="Processing nodes")
        for node in pbar:
            if self.interrupted:
                break
                
            node_id = node["id"]
            props = node.get("properties", {})
            text = props.get("textEs") or props.get("textCa")
            
            # Query LLM directly without RAG few-shot
            citations = self.query_llm_citations(text)
            
            added_for_this_node = 0
            
            # Process extracted citations
            for cit in citations:
                cited_text = cit.get("cited_text", "")
                cited_title = cit.get("cited_document_title", "")
                doc_number = cit.get("document_number", "")
                eli_uri = cit.get("eli_uri", "")
                dogc_number = cit.get("dogc_number", "")
                type_of_law = cit.get("type_of_law", "")
                doc_date = cit.get("document_date", "")
                citation_type = cit.get("citation_type", "CITES")
                details = cit.get("details", "")
                
                if not cited_title and not doc_number and not eli_uri and not dogc_number:
                    continue
                
                # 1. Resolve parent document node in existing dataset or previously created in this run
                target_doc_id = self.resolve_target_node(
                    title=cited_title,
                    doc_number=doc_number,
                    eli_uri=eli_uri,
                    dogc_number=dogc_number
                )
                
                # 2. If parent document not resolved, create it!
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
                
                # 3. Check if specific article/section details are mentioned, and map to it
                final_target_id = target_doc_id
                if details:
                    section_id = self.match_section_in_document(target_doc_id, details)
                    if section_id is not None:
                        final_target_id = section_id
                    else:
                        # Create new section/article node under the parent document
                        section_id = self.create_new_section_node(target_doc_id, details)
                        final_target_id = section_id
                        new_nodes_created += 1
                
                # 4. Create relationship from source section node to final target ID
                exists = False
                for rel in self.graph_data.get("relationships", []):
                    if (rel.get("source") == node_id and 
                        rel.get("target") == final_target_id and 
                        rel.get("type") == citation_type):
                        exists = True
                        break
                        
                if not exists:
                    new_rel = {
                        "source": node_id,
                        "target": final_target_id,
                        "type": citation_type,
                        "properties": {
                            "extracted_by": "LLM_Simple_Agent",
                            "cited_text": cited_text,
                            "details": details,
                            "timestamp": int(time.time())
                        }
                    }
                    self.graph_data["relationships"].append(new_rel)
                    new_relationships_added += 1
                    added_for_this_node += 1
            
            # Mark node as processed
            node["properties"]["processed_by_llm_agent"] = True
            node["properties"]["processed_by_simple_agent"] = True
            node["properties"]["llm_agent_citations_found"] = added_for_this_node
            
            processed_count += 1
            pbar.set_postfix({
                "New Rels": new_relationships_added,
                "New Nodes": new_nodes_created,
                "Batch": f"{processed_count}/{len(target_nodes)}"
            })
            
            # Save periodic checkpoint
            if processed_count % self.args.batch_size == 0:
                print(f"\n[SimpleAgent] Saving checkpoint at {processed_count} processed nodes...")
                self.save_graph()
                
        # Save final state
        print(f"\n[SimpleAgent] Finished run. Processed {processed_count} nodes.")
        print(f"Added {new_relationships_added} new relationships and created {new_nodes_created} new nodes/sections.")
        self.save_graph()


def main():
    parser = argparse.ArgumentParser(description="Simple Citation Extraction Agent with Section Resolution")
    parser.add_argument("--input-json", default="data/extracted_subgraph_custom.json", help="Path to input graph JSON")
    parser.add_argument("--output-json", default="data/extracted_subgraph_custom_updated.json", help="Path to output graph JSON")
    parser.add_argument("--vllm-url", default="http://127.0.0.1:8000/v1", help="URL of vLLM Server")
    parser.add_argument("--vllm-model-name", default="/gpfs/projects/bsc100/models/DeepSeek-R1-Distill-Qwen-32B", help="Model path/name used on vLLM server")
    parser.add_argument("--batch-size", type=int, default=50, help="Checkpoints batch save size")
    parser.add_argument("--max-nodes", type=int, default=None, help="Max nodes to process (useful for testing)")
    
    args = parser.parse_args()
    
    agent = SimpleCitationAgent(args)
    agent.run_extraction_loop()


if __name__ == "__main__":
    main()
