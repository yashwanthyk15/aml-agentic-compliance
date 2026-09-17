import json
from pathlib import Path
from typing import Optional

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

class GraphNode(BaseModel):
    node_id: str
    node_type: str
    label: str
    document_id: Optional[str] = None
    metadata: dict = {}

class GraphEdge(BaseModel):
    source_node_id: str
    target_node_id: str
    edge_type: str

class RegulatoryGraph:
    def __init__(self):
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        
    def add_document(self, doc_id: str, doc_name: str, jurisdiction: Optional[str] = None):
        self.nodes[doc_id] = GraphNode(
            node_id=doc_id,
            node_type="DOCUMENT",
            label=doc_name,
            document_id=doc_id,
            metadata={"jurisdiction": jurisdiction} if jurisdiction else {}
        )
        
    def add_section(self, section_id: str, label: str, doc_id: str):
        self.nodes[section_id] = GraphNode(
            node_id=section_id,
            node_type="SECTION",
            label=label,
            document_id=doc_id
        )
        self.add_edge(doc_id, section_id, "CONTAINS")
        
    def add_obligation(self, obligation_id: str, label: str, section_id: str, description: str = ''):
        self.nodes[obligation_id] = GraphNode(
            node_id=obligation_id,
            node_type="OBLIGATION",
            label=label,
            metadata={"description": description}
        )
        self.add_edge(section_id, obligation_id, "REQUIRES")
        
    def add_condition(self, condition_id: str, label: str, obligation_id: str):
        self.nodes[condition_id] = GraphNode(
            node_id=condition_id,
            node_type="CONDITION",
            label=label
        )
        self.add_edge(obligation_id, condition_id, "APPLIES_WHEN")
        
    def add_edge(self, source: str, target: str, edge_type: str):
        if source not in self.nodes:
            logger.warning("Source node not found for edge", source=source, target=target, type=edge_type)
        if target not in self.nodes:
            logger.warning("Target node not found for edge", source=source, target=target, type=edge_type)
            
        self.edges.append(GraphEdge(
            source_node_id=source,
            target_node_id=target,
            edge_type=edge_type
        ))
        
    def get_related(self, node_id: str, edge_type: Optional[str] = None) -> list[GraphNode]:
        """Traverse edges from a node."""
        if node_id not in self.nodes:
            return []
            
        related = []
        for edge in self.edges:
            if edge.source_node_id == node_id:
                if edge_type is None or edge.edge_type == edge_type:
                    if edge.target_node_id in self.nodes:
                        related.append(self.nodes[edge.target_node_id])
        return related
        
    def save(self, path: Path):
        """Serialize to JSON file."""
        logger.info("Saving RegulatoryGraph", path=str(path))
        data = {
            "nodes": {node_id: node.model_dump() for node_id, node in self.nodes.items()},
            "edges": [edge.model_dump() for edge in self.edges]
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
    @classmethod
    def load(cls, path: Path) -> 'RegulatoryGraph':
        """Load from JSON file."""
        logger.info("Loading RegulatoryGraph", path=str(path))
        if not path.exists():
            raise FileNotFoundError(f"Graph file not found: {path}")
            
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        graph = cls()
        for node_id, node_data in data.get("nodes", {}).items():
            graph.nodes[node_id] = GraphNode(**node_data)
            
        for edge_data in data.get("edges", []):
            graph.edges.append(GraphEdge(**edge_data))
            
        return graph
