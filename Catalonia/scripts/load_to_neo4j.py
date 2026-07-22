import os
import sys
import json
import argparse
from tqdm import tqdm
from neo4j import GraphDatabase

def create_constraints(tx):
    print("Creating database constraints and indices...")
    # Neo4j constraints syntax
    tx.run("CREATE CONSTRAINT dogc_unique IF NOT EXISTS FOR (g:DOGC) REQUIRE g.dogcNumber IS UNIQUE")
    tx.run("CREATE CONSTRAINT doc_unique IF NOT EXISTS FOR (d:Document) REQUIRE d.documentId IS UNIQUE")
    tx.run("CREATE CONSTRAINT sec_unique IF NOT EXISTS FOR (s:DocumentSection) REQUIRE s.sectionId IS UNIQUE")
    tx.run("CREATE CONSTRAINT desc_unique IF NOT EXISTS FOR (desc:Descriptor) REQUIRE desc.descriptorId IS UNIQUE")

def load_json_file(filepath):
    if not os.path.exists(filepath):
        print(f"Warning: File {filepath} not found. Skipping.")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def batch_query(session, query, batch, desc="Importing"):
    batch_size = 1000
    for i in range(0, len(batch), batch_size):
        chunk = batch[i:i + batch_size]
        session.execute_write(lambda tx: tx.run(query, batch=chunk))

def main():
    parser = argparse.ArgumentParser(description="Load Catalonia Law Graph JSON outputs into Neo4j")
    parser.add_argument("--user", type=str, default="neo4j", help="Neo4j username")
    parser.add_argument("--password", type=str, default="mineGraphRule", help="Neo4j password")
    parser.add_argument("--dir", type=str, default="data/prepared_graph_data", help="Directory containing JSON files")
    args = parser.parse_args()

    # NOTE: The database URI is strictly hardcoded to port 23010 per user requirement.
    # Do NOT expose it as a CLI parameter or change it to another port.
    uri = "bolt://localhost:23010"
    
    # Resolve paths relative to the parent Catalonia root directory
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(script_dir, args.dir)

    print("=" * 60)
    print("NEO4J GRAPH LOADER: CATALONIA LAW GRAPH")
    print("=" * 60)
    print(f"Connecting to Neo4j at {uri}...")

    try:
        driver = GraphDatabase.driver(uri, auth=(args.user, args.password))
        driver.verify_connectivity()
        print("Connected successfully!")
    except Exception as e:
        print(f"Error: Failed to connect to Neo4j: {e}")
        sys.exit(1)

    with driver.session() as session:
        # 1. Create constraints
        session.execute_write(create_constraints)

        # 2. Load and Import DOGC Nodes
        dogc_nodes = load_json_file(os.path.join(data_dir, "dogc_nodes.json"))
        if dogc_nodes:
            print(f"Importing {len(dogc_nodes)} DOGC nodes...")
            query = """
            UNWIND $batch AS row
            MERGE (g:DOGC {dogcNumber: row.dogcNumber})
            SET g.dateDOGC = row.dateDOGC, g.year = row.year
            """
            batch_query(session, query, dogc_nodes, "DOGC Nodes")

        # 3. Load and Import Document Nodes (grouped by specificLabel)
        doc_nodes = load_json_file(os.path.join(data_dir, "document_nodes.json"))
        if doc_nodes:
            print(f"Importing {len(doc_nodes)} Document nodes...")
            # Group docs by specific label
            docs_by_label = {}
            for doc in doc_nodes:
                label = doc.get("specificLabel") or "Document"
                docs_by_label.setdefault(label, []).append(doc)
            
            for label, batch in docs_by_label.items():
                print(f"  - Importing {len(batch)} nodes with label :{label}...")
                label_clause = ""
                if label != "Other" and label != "Document":
                    label_clause = f"SET d:{label}"
                
                query = f"""
                UNWIND $batch AS row
                MERGE (d:Document {{documentId: row.documentId}})
                SET d.title = row.properties.title,
                    d.titleCa = row.properties.titleCa,
                    d.titleEs = row.properties.titleEs,
                    d.eliUri = row.properties.eliUri,
                    d.url = row.properties.url,
                    d.urlCa = row.properties.urlCa,
                    d.urlEs = row.properties.urlEs,
                    d.pdfUrl = row.properties.pdfUrl,
                    d.pdfUrlCa = row.properties.pdfUrlCa,
                    d.pdfUrlEs = row.properties.pdfUrlEs,
                    d.typeOfLaw = row.properties.typeOfLaw,
                    d.documentDate = row.properties.documentDate,
                    d.documentNumber = row.properties.documentNumber,
                    d.controlNumber = row.properties.controlNumber,
                    d.emittingOrganism = row.properties.emittingOrganism,
                    d.cve = row.properties.cve,
                    d.section = row.properties.section,
                    d.isBilingual = row.properties.isBilingual
                {label_clause}
                """
                batch_query(session, query, batch, f"Document Nodes ({label})")

        # 4. Load and Import DocumentSection Nodes
        section_nodes = load_json_file(os.path.join(data_dir, "document_sections.json"))
        if section_nodes:
            print(f"Importing {len(section_nodes)} DocumentSection nodes...")
            # Group by specificLabel (Article or not)
            secs_normal = []
            secs_article = []
            for sec in section_nodes:
                if sec.get("specificLabel") == "Article":
                    secs_article.append(sec)
                else:
                    secs_normal.append(sec)
                    
            if secs_normal:
                query_normal = """
                UNWIND $batch AS row
                MERGE (s:DocumentSection {sectionId: row.sectionId})
                SET s.title = row.properties.title,
                    s.titleCa = row.properties.titleCa,
                    s.titleEs = row.properties.titleEs,
                    s.heading = row.properties.heading,
                    s.headingCa = row.properties.headingCa,
                    s.headingEs = row.properties.headingEs,
                    s.text = row.properties.text,
                    s.textCa = row.properties.textCa,
                    s.textEs = row.properties.textEs,
                    s.type = row.properties.type,
                    s.isBilingual = row.properties.isBilingual
                """
                batch_query(session, query_normal, secs_normal, "DocumentSections")
                
            if secs_article:
                query_article = """
                UNWIND $batch AS row
                MERGE (s:DocumentSection {sectionId: row.sectionId})
                SET s.title = row.properties.title,
                    s.titleCa = row.properties.titleCa,
                    s.titleEs = row.properties.titleEs,
                    s.heading = row.properties.heading,
                    s.headingCa = row.properties.headingCa,
                    s.headingEs = row.properties.headingEs,
                    s.text = row.properties.text,
                    s.textCa = row.properties.textCa,
                    s.textEs = row.properties.textEs,
                    s.type = row.properties.type,
                    s.isBilingual = row.properties.isBilingual
                SET s:Article
                """
                batch_query(session, query_article, secs_article, "Article Sections")

        # 5. Load and Import Descriptor Nodes
        desc_nodes = load_json_file(os.path.join(data_dir, "descriptor_nodes.json"))
        if desc_nodes:
            print(f"Importing {len(desc_nodes)} Descriptor nodes (label: :Descriptor)...")
            query = """
            UNWIND $batch AS row
            MERGE (desc:Descriptor {descriptorId: row.descriptorId})
            SET desc.name = row.properties.name,
                desc.rawName = row.properties.rawName,
                desc.type = row.properties.type
            """
            batch_query(session, query, desc_nodes, "Descriptor Nodes")

        # 6. Import PUBLISHED_IN Relationships
        pub_rels = load_json_file(os.path.join(data_dir, "published_in_relationships.json"))
        if pub_rels:
            print(f"Importing {len(pub_rels)} PUBLISHED_IN relationships...")
            query = """
            UNWIND $batch AS row
            MATCH (d:Document {documentId: row.documentId})
            MATCH (g:DOGC {dogcNumber: row.dogcNumber})
            MERGE (d)-[r:PUBLISHED_IN]->(g)
            SET r.section = row.section
            """
            batch_query(session, query, pub_rels, "PUBLISHED_IN")

        # 7. Import HAS_SECTION Relationships
        sec_rels = load_json_file(os.path.join(data_dir, "has_section_relationships.json"))
        if sec_rels:
            print(f"Importing {len(sec_rels)} HAS_SECTION relationships...")
            query = """
            UNWIND $batch AS row
            MATCH (d:Document {documentId: row.documentId})
            MATCH (s:DocumentSection {sectionId: row.sectionId})
            MERGE (d)-[r:HAS_SECTION]->(s)
            SET r.order = row.order
            """
            batch_query(session, query, sec_rels, "HAS_SECTION")

        # 8. Import HAS_DESCRIPTOR Relationships
        desc_rels = load_json_file(os.path.join(data_dir, "has_descriptor_relationships.json"))
        if desc_rels:
            print(f"Importing {len(desc_rels)} HAS_DESCRIPTOR relationships...")
            query = """
            UNWIND $batch AS row
            MATCH (d:Document {documentId: row.documentId})
            MATCH (desc:Descriptor {descriptorId: row.descriptorId})
            MERGE (d)-[r:HAS_DESCRIPTOR]->(desc)
            SET r.type = row.type
            """
            batch_query(session, query, desc_rels, "HAS_DESCRIPTOR")

        # 9. Import Granular Affectation Relationships
        aff_rels = load_json_file(os.path.join(data_dir, "affectation_relationships.json"))
        if aff_rels:
            print(f"Importing {len(aff_rels)} Granular Affectation relationships...")
            # Group by (sourceType, targetType, relationshipType) to avoid dynamic MATCH / MERGE issues
            rels_grouped = {}
            for rel in aff_rels:
                key = (rel.get("sourceType"), rel.get("targetType"), rel.get("relationshipType"))
                rels_grouped.setdefault(key, []).append(rel)
                
            for (s_type, t_type, rel_type), batch in rels_grouped.items():
                print(f"  - Importing {len(batch)} relationships: ({s_type})-[:{rel_type}]->({t_type})...")
                
                # Match properties depending on types
                s_id_field = "sectionId" if s_type == "DocumentSection" else "documentId"
                t_id_field = "sectionId" if t_type == "DocumentSection" else "documentId"
                
                query = f"""
                UNWIND $batch AS row
                MATCH (source:{s_type} {{{s_id_field}: row.sourceId}})
                MATCH (target:{t_type} {{{t_id_field}: row.targetId}})
                MERGE (source)-[r:{rel_type}]->(target)
                SET r.text = row.properties.text,
                    r.type = row.properties.type,
                    r.action = row.properties.action,
                    r.affectedSections = row.properties.affectedSections,
                    r.mappedSection = row.properties.mappedSection
                """
                batch_query(session, query, batch, f"Affectation ({rel_type})")

        # 10. Import Citation Relationships
        cite_rels = load_json_file(os.path.join(data_dir, "citation_relationships.json"))
        if cite_rels:
            mapped_cites = [c for c in cite_rels if c.get("isMapped")]
            if mapped_cites:
                print(f"Importing {len(mapped_cites)} CITES relationships (skipping {len(cite_rels) - len(mapped_cites)} unmapped ones)...")
                query = """
                UNWIND $batch AS row
                MATCH (source:DocumentSection {sectionId: row.sourceId})
                MATCH (target:Document {documentId: row.targetId})
                MERGE (source)-[r:CITES]->(target)
                SET r.citedDocument = row.properties.citedDocument,
                    r.citedSection = row.properties.citedSection
                """
                batch_query(session, query, mapped_cites, "CITES")

    driver.close()
    print("\nGraph successfully loaded into Neo4j! 🚀")

if __name__ == "__main__":
    main()
