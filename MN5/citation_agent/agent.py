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

# Third-party imports (will check dependencies on run)
try:
    import numpy as np
    import faiss
    from sentence_transformers import SentenceTransformer
    from openai import OpenAI
    import httpx
except ImportError as e:
    print(f"Error importing dependencies: {e}")
    print("Please run setup_env.sh first or install required packages.")
    sys.exit(1)


class CitationAgent:
    def __init__(self, args):
        self.args = args
        self.input_json = Path(args.input_json)
        self.output_json = Path(args.output_json)
        self.index_dir = Path(args.index_dir)
        self.index_dir.mkdir(exist_ok=True)
        
        # Initialize OpenAI client with proxy bypass
        self.vllm_client = OpenAI(
            base_url=args.vllm_url,
            api_key="none",
            http_client=httpx.Client(proxy=None)
        )
        
        # Lazy loaded models and indexes
        self.embed_model = None
        self.few_shot_index = None
        self.few_shot_metadata = []
        self.target_index = None
        self.target_metadata = []
        self.graph_data = None
        self.nodes_by_id = {}
        
        # Keep track of active changes for signal handling
        self.interrupted = False
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        print(f"\n[Agent] Received signal {signum}. Gracefully shutting down and saving progress...")
        self.interrupted = True

    def get_embedding_model(self):
        if self.embed_model is None:
            print(f"Loading embedding model: {self.args.model_name} on {self.args.device}...")
            self.embed_model = SentenceTransformer(self.args.model_name, device=self.args.device)
        return self.embed_model

    def load_graph(self):
        if self.graph_data is None:
            # Check if updated file exists to resume from it
            target_path = self.output_json if self.output_json.exists() else self.input_json
            print(f"Loading graph data from {target_path}...")
            with open(target_path, "r", encoding="utf-8") as f:
                self.graph_data = json.load(f)
            
            print(f"Building node index by ID for {len(self.graph_data.get('nodes', []))} nodes...")
            self.nodes_by_id = {node["id"]: node for node in self.graph_data.get("nodes", [])}
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

    def get_node_citations(self, node_id):
        """Finds all outgoing citations for a given node ID from the current graph data."""
        citation_types = {"CITES", "AFFECTS", "ABROGATES", "MODIFIES", "CONSOLIDATES"}
        citations = []
        for rel in self.graph_data.get("relationships", []):
            if rel.get("source") == node_id and rel.get("type") in citation_types:
                target_id = rel["target"]
                target_node = self.nodes_by_id.get(target_id, {})
                target_props = target_node.get("properties", {})
                
                title = (target_props.get("titleEs") or 
                         target_props.get("titleCa") or 
                         target_props.get("title") or 
                         rel.get("properties", {}).get("citedDocument", "No Title"))
                
                citations.append({
                    "target_id": target_id,
                    "target_labels": target_node.get("labels", []),
                    "target_title": title,
                    "type": rel["type"],
                    "properties": rel.get("properties", {})
                })
        return citations

    def build_indexes(self):
        self.load_graph()
        model = self.get_embedding_model()
        
        # 1. Build Few-Shot Database (Nodes that have text AND have existing citations)
        print("\n--- Identifying Few-Shot Candidates ---")
        few_shot_nodes = []
        few_shot_texts = []
        
        for node in tqdm(self.graph_data.get("nodes", []), desc="Checking citations"):
            props = node.get("properties", {})
            text = props.get("textEs") or props.get("textCa")
            if not text:
                continue
                
            citations = self.get_node_citations(node["id"])
            if citations:
                few_shot_nodes.append({
                    "id": node["id"],
                    "labels": node.get("labels", []),
                    "title": props.get("titleEs") or props.get("titleCa") or props.get("title") or "No Title",
                    "citations": citations
                })
                # Truncate text slightly if excessively long for embedding model limit
                few_shot_texts.append(text[:2000])

        print(f"Found {len(few_shot_nodes)} nodes with text and existing citations.")
        
        if few_shot_texts:
            print("Generating embeddings for few-shot examples...")
            few_shot_embeddings = model.encode(few_shot_texts, show_progress_bar=True, convert_to_numpy=True)
            
            # Setup FAISS Cosine Similarity Index
            faiss.normalize_L2(few_shot_embeddings)
            fs_index = faiss.IndexFlatIP(few_shot_embeddings.shape[1])
            fs_index.add(few_shot_embeddings)
            
            # Save few shot index and metadata
            faiss.write_index(fs_index, str(self.index_dir / "few_shot.index"))
            with open(self.index_dir / "few_shot_metadata.json", "w", encoding="utf-8") as f:
                json.dump(few_shot_nodes, f, ensure_ascii=False, indent=2)
            print("Few-shot index saved successfully.")
        else:
            print("Warning: No few-shot candidates found in the graph.")

        # 2. Build Target Search Database (All nodes in the graph that can be targets of a citation)
        print("\n--- Identifying Potential Citation Target Nodes ---")
        target_nodes = []
        target_texts = []
        
        # Typical labels for documents or high level nodes
        target_labels = {"Document", "DOGC", "Law", "Decree", "Section", "DocumentSection", "Norma"}
        
        for node in tqdm(self.graph_data.get("nodes", []), desc="Collecting targets"):
            labels = set(node.get("labels", []))
            props = node.get("properties", {})
            
            # If the node has a title/number or is of a matching label
            title = props.get("titleEs") or props.get("titleCa") or props.get("title") or props.get("name")
            dogc_num = props.get("dogcNumber")
            
            if title or dogc_num:
                # Construct a search context string for embeddings
                label_str = ", ".join(labels)
                context = f"Label: {label_str} | Title: {title or 'No Title'}"
                if dogc_num:
                    context += f" | DOGC Number: {dogc_num}"
                if props.get("year"):
                    context += f" | Year: {props.get('year')}"
                if props.get("dateDOGC"):
                    context += f" | Date: {props.get('dateDOGC')}"
                    
                target_nodes.append({
                    "id": node["id"],
                    "labels": list(labels),
                    "title": title or f"Node {node['id']}",
                    "dogcNumber": dogc_num or "",
                    "year": props.get("year", ""),
                    "date": props.get("dateDOGC", "")
                })
                target_texts.append(context)

        print(f"Found {len(target_nodes)} potential target nodes.")
        
        if target_texts:
            print("Generating embeddings for target nodes...")
            # We can process in batches to save memory
            target_embeddings = model.encode(target_texts, batch_size=256, show_progress_bar=True, convert_to_numpy=True)
            
            # Setup FAISS Cosine Similarity Index
            faiss.normalize_L2(target_embeddings)
            t_index = faiss.IndexFlatIP(target_embeddings.shape[1])
            t_index.add(target_embeddings)
            
            # Save targets index and metadata
            faiss.write_index(t_index, str(self.index_dir / "targets.index"))
            with open(self.index_dir / "targets_metadata.json", "w", encoding="utf-8") as f:
                json.dump(target_nodes, f, ensure_ascii=False, indent=2)
            print("Target nodes index saved successfully.")

    def load_indexes(self):
        fs_idx_path = self.index_dir / "few_shot.index"
        fs_meta_path = self.index_dir / "few_shot_metadata.json"
        t_idx_path = self.index_dir / "targets.index"
        t_meta_path = self.index_dir / "targets_metadata.json"
        
        if not (fs_idx_path.exists() and fs_meta_path.exists() and t_idx_path.exists() and t_meta_path.exists()):
            print("Error: Indexes not found. Please run the build-index command first.")
            sys.exit(1)
            
        print("Loading indexes from disk...")
        self.few_shot_index = faiss.read_index(str(fs_idx_path))
        with open(fs_meta_path, "r", encoding="utf-8") as f:
            self.few_shot_metadata = json.load(f)
            
        self.target_index = faiss.read_index(str(t_idx_path))
        with open(t_meta_path, "r", encoding="utf-8") as f:
            self.target_metadata = json.load(f)
            
        print(f"Loaded {len(self.few_shot_metadata)} few-shot examples and {len(self.target_metadata)} targets.")

    def find_few_shot_examples(self, query_text, k=2):
        if not self.few_shot_index:
            return []
        
        model = self.get_embedding_model()
        query_emb = model.encode([query_text], convert_to_numpy=True)
        faiss.normalize_L2(query_emb)
        
        distances, indices = self.few_shot_index.search(query_emb, k)
        
        examples = []
        for i, idx in enumerate(indices[0]):
            if idx < len(self.few_shot_metadata) and idx >= 0:
                examples.append(self.few_shot_metadata[idx])
        return examples

    def resolve_target_node(self, target_title, threshold=0.75):
        """Resolves a free-text citation title to a node ID using exact matching, substring, or FAISS vector search."""
        if not target_title:
            return None
            
        target_title_clean = target_title.strip().lower()
        
        # 1. Exact string match or subtitle match in metadata (fast O(N) but precise)
        for target in self.target_metadata:
            t_title = target.get("title", "").strip().lower()
            if t_title == target_title_clean:
                return target["id"]
                
        # 2. Check if the target_title contains a DOGC number and match that
        dogc_match = re.search(r"dogc\s*(?:nº|n\.º|number)?\s*(\d+)", target_title_clean)
        if dogc_match:
            dogc_num = dogc_match.group(1)
            for target in self.target_metadata:
                if target.get("dogcNumber") == dogc_num:
                    return target["id"]

        # 3. Fuzzy/Semantic vector matching using FAISS
        model = self.get_embedding_model()
        query_emb = model.encode([target_title], convert_to_numpy=True)
        faiss.normalize_L2(query_emb)
        
        distances, indices = self.target_index.search(query_emb, 1)
        sim = distances[0][0]
        idx = indices[0][0]
        
        if sim >= threshold and idx < len(self.target_metadata) and idx >= 0:
            matched_node = self.target_metadata[idx]
            # print(f"  [RAG Target Match] '{target_title}' -> '{matched_node['title']}' (Sim: {sim:.3f})")
            return matched_node["id"]
            
        return None

    def query_llm_citations(self, text, few_shot_examples):
        # Format the few-shot examples
        examples_str = ""
        for idx, ex in enumerate(few_shot_examples):
            examples_str += f"### Example {idx+1}:\n"
            examples_str += f"Source Text: \"{ex['title']}\"\n"
            
            citations_list = []
            for c in ex["citations"]:
                citations_list.append({
                    "cited_text": c["properties"].get("cited_text") or c["target_title"],
                    "target_title": c["target_title"],
                    "citation_type": c["type"],
                    "details": c["properties"].get("details", "")
                })
            examples_str += f"Extracted Citations:\n{json.dumps(citations_list, ensure_ascii=False, indent=2)}\n\n"

        prompt = f"""You are analyzing Spanish or Catalan legal texts. Your task is to extract citations to other laws, decrees, sections, or articles.

Here are some examples of how to perform the extraction:

{examples_str}

### Task:
Analyze the following source text and extract any citations. Output a JSON list of objects containing:
- "cited_text": The exact short snippet/phrase from the source text representing the citation.
- "target_title": The name/title of the cited document or section (e.g. "Ley 13/2008").
- "citation_type": The type of citation. Choose from: CITES, AFFECTS, ABROGATES, MODIFIES, CONSOLIDATES.
- "details": Specific section, article, or details if mentioned.

Source Legal Text:
\"\"\"
{text}
\"\"\"

Output ONLY a valid JSON list. If no citations are found, return an empty list: [].
Do not include any thinking tags or markdown code wrappers in your final answer, just the JSON list."""

        system_message = "You are an expert legal assistant that outputs only raw JSON lists of citations."
        
        try:
            response = self.vllm_client.chat.completions.create(
                model=self.args.vllm_model_name,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=2048
            )
            
            # Extract content (handle reasoning content if vLLM splits it)
            content = response.choices[0].message.content
            
            # Clean and parse JSON
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
                # If LLM returned a dictionary with a list inside
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
        self.load_indexes()
        
        # Filter nodes of interest: DocumentSection with text, not yet processed
        # If they already have graph citations, we skip them or process them to find *new* ones.
        # User specified: "read all documentsSection texts and find new citations when it finds them add them to the json (marked them as found with LLM)"
        target_nodes = []
        for node in self.graph_data.get("nodes", []):
            labels = node.get("labels", [])
            if "DocumentSection" not in labels:
                continue
                
            props = node.get("properties", {})
            text = props.get("textEs") or props.get("textCa")
            if not text:
                continue
                
            # Skip if already processed by the LLM agent
            if props.get("processed_by_llm_agent"):
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
        
        pbar = tqdm(target_nodes, desc="Processing nodes")
        for node in pbar:
            if self.interrupted:
                break
                
            node_id = node["id"]
            props = node.get("properties", {})
            text = props.get("textEs") or props.get("textCa")
            
            # 1. Retrieve RAG Few-shot examples
            examples = self.find_few_shot_examples(text, k=self.args.few_shot_k)
            
            # 2. Query LLM
            citations = self.query_llm_citations(text, examples)
            
            # Keep track of relationships added in this step
            added_for_this_node = 0
            
            # 3. Resolve targets and add relationships
            for cit in citations:
                target_title = cit.get("target_title")
                citation_type = cit.get("citation_type", "CITES")
                cited_text = cit.get("cited_text", "")
                details = cit.get("details", "")
                
                target_id = self.resolve_target_node(target_title, threshold=self.args.similarity_threshold)
                
                if target_id is not None:
                    # Check if relationship already exists
                    exists = False
                    for rel in self.graph_data.get("relationships", []):
                        if (rel.get("source") == node_id and 
                            rel.get("target") == target_id and 
                            rel.get("type") == citation_type):
                            exists = True
                            break
                            
                    if not exists:
                        new_rel = {
                            "source": node_id,
                            "target": target_id,
                            "type": citation_type,
                            "properties": {
                                "extracted_by": "LLM_RAG_Agent",
                                "cited_text": cited_text,
                                "details": details,
                                "timestamp": int(time.time())
                            }
                        }
                        self.graph_data["relationships"].append(new_rel)
                        new_relationships_added += 1
                        added_for_this_node += 1
            
            # 4. Mark node as processed
            node["properties"]["processed_by_llm_agent"] = True
            node["properties"]["llm_agent_citations_found"] = added_for_this_node
            
            processed_count += 1
            pbar.set_postfix({
                "New Rels": new_relationships_added,
                "Batch": f"{processed_count}/{len(target_nodes)}"
            })
            
            # Save periodic checkpoint
            if processed_count % self.args.batch_size == 0:
                print(f"\n[Agent] Saving checkpoint at {processed_count} processed nodes...")
                self.save_graph()
                
        # Save final state
        print(f"\n[Agent] Finished run. Processed {processed_count} nodes. Added {new_relationships_added} new relationships.")
        self.save_graph()


def main():
    parser = argparse.ArgumentParser(description="Citation Extraction Agent with RAG few-shot prompting")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Common arguments
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("--input-json", default="/home/cambria/gram3/LawGraph/Spain/MN5/extracted_subgraph_large.json", help="Path to input graph JSON")
    parent_parser.add_argument("--output-json", default="/home/cambria/gram3/LawGraph/Spain/MN5/extracted_subgraph_large_updated.json", help="Path to output graph JSON")
    parent_parser.add_argument("--index-dir", default="/home/cambria/gram3/LawGraph/Spain/MN5/citation_agent/faiss_indexes", help="Directory to save/load indexes")
    parent_parser.add_argument("--model-name", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", help="SentenceTransformer model for embedding")
    parent_parser.add_argument("--device", default="cuda" if os.environ.get("CUDA_VISIBLE_DEVICES") or os.path.exists("/dev/nvidia0") else "cpu", help="Device for sentence transformer (cuda/cpu)")

    # Build index parser
    subparsers.add_parser("build-index", parents=[parent_parser], help="Build FAISS indexes for few-shot search and targets")
    
    # Pre-download parser
    subparsers.add_parser("pre-download", parents=[parent_parser], help="Download and cache the embedding model")

    # Run parser
    run_parser = subparsers.add_parser("run", parents=[parent_parser], help="Run the iterative extraction loop")
    run_parser.add_argument("--vllm-url", default="http://127.0.0.1:8000/v1", help="URL of vLLM Server")
    run_parser.add_argument("--vllm-model-name", default="/gpfs/projects/bsc100/models/DeepSeek-R1-Distill-Qwen-32B", help="Model path/name used on vLLM server")
    run_parser.add_argument("--batch-size", type=int, default=50, help="Checkpoints batch save size")
    run_parser.add_argument("--max-nodes", type=int, default=None, help="Max nodes to process (useful for testing)")
    run_parser.add_argument("--few-shot-k", type=int, default=2, help="Number of few-shot examples from RAG")
    run_parser.add_argument("--similarity-threshold", type=float, default=0.75, help="FAISS threshold for matching target titles")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
        
    agent = CitationAgent(args)
    
    if args.command == "pre-download":
        agent.get_embedding_model()
        print("Model downloaded successfully!")
    elif args.command == "build-index":
        agent.build_indexes()
    elif args.command == "run":
        agent.run_extraction_loop()


if __name__ == "__main__":
    main()
