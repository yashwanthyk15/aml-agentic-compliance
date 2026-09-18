import os
import sys
import json
import re
from pathlib import Path
import structlog

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.rag.regulatory_graph import RegulatoryGraph

logger = structlog.get_logger(__name__)

def main():
    logger.info("Generating understanding artifacts")
    processed_dir = project_root / "data" / "processed"
    und_dir = project_root / "data" / "understanding"
    und_dir.mkdir(parents=True, exist_ok=True)
    
    chunks_file = processed_dir / "regulatory_chunks.jsonl"
    
    chunks = []
    if chunks_file.exists():
        with open(chunks_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    else:
        logger.warning("Chunks file not found", path=str(chunks_file))
        
    # Heuristics
    rule_summaries = []
    obligations = []
    thresholds = []
    
    rule_pattern = re.compile(r'(Recommendation \d+|Section \d+|Article \d+)', re.IGNORECASE)
    ob_keywords = ['shall', 'must', 'required', 'obligation', 'ensure']
    thresh_pattern = re.compile(r'(\$\d+[,\d]*|€\d+[,\d]*|£\d+[,\d]*|\d+ (?:days|months|years)|\d+%)', re.IGNORECASE)
    
    graph = RegulatoryGraph()
    documents = {}
    for chunk in chunks:
        text = chunk.get("text", "")
        document_id = chunk.get("document_id", "UNKNOWN")
        documents.setdefault(document_id, {
            "name": chunk.get("document_name", document_id),
            "jurisdiction": chunk.get("jurisdiction"),
        })
        
        # rules
        if rule_pattern.search(text):
            rule_summaries.append({
                "chunk_id": chunk.get("chunk_id"),
                "rule_ref": rule_pattern.search(text).group(1),
                "context": text[:200] + "..."
            })
            
        # obligations
        if any(kw in text.lower() for kw in ob_keywords):
            obligations.append({
                "chunk_id": chunk.get("chunk_id"),
                "snippet": text[:200] + "..."
            })
            
        # thresholds
        found_thresholds = thresh_pattern.findall(text)
        if found_thresholds:
            thresholds.append({
                "chunk_id": chunk.get("chunk_id"),
                "thresholds": list(set(found_thresholds))
            })
            
    with open(und_dir / "document_metadata.json", "w") as f:
        json.dump({"total_chunks": len(chunks)}, f, indent=2)
        
    with open(und_dir / "rule_summaries.json", "w") as f:
        json.dump(rule_summaries, f, indent=2)
        
    with open(und_dir / "obligations.json", "w") as f:
        json.dump(obligations, f, indent=2)
        
    with open(und_dir / "thresholds.json", "w") as f:
        json.dump(thresholds, f, indent=2)
        
    for document_id, metadata in documents.items():
        graph.add_document(document_id, metadata["name"], metadata["jurisdiction"])
    for chunk in chunks:
        document_id = chunk.get("document_id", "UNKNOWN")
        section_path = chunk.get("section_path", [])
        parent_id = document_id
        for index, section in enumerate(section_path):
            section_id = f"{document_id}:section:{index}:{section}"
            if section_id not in graph.nodes:
                graph.add_section(section_id, section, document_id)
            parent_id = section_id
        text = chunk.get("text", "")
        if parent_id and any(word in text.lower() for word in ob_keywords):
            obligation_id = f"{chunk.get('chunk_id', 'chunk')}:obligation"
            graph.add_obligation(
                obligation_id,
                f"Obligation from {chunk.get('chunk_id', 'chunk')}",
                parent_id,
                text[:300],
            )
    graph.save(und_dir / "regulatory_graph.json")
        
    with open(und_dir / "schema_notes.json", "w") as f:
        json.dump({"notes": "Generated heuristic extractions without LLM."}, f, indent=2)
        
    # also save a copy of chunks in understanding dir as requested
    chunks_out_file = und_dir / "regulatory_chunks.jsonl"
    if chunks_file.exists():
        with open(chunks_file, "r", encoding="utf-8") as src, open(chunks_out_file, "w", encoding="utf-8") as dst:
            dst.write(src.read())

    logger.info("Completed understanding generation")

if __name__ == "__main__":
    main()
