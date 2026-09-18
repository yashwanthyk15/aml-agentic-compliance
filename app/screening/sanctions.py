import zipfile
import lxml.etree as ET
from pathlib import Path
from pydantic import BaseModel, Field
import unicodedata
import string
import structlog
from rapidfuzz import fuzz

from app.orchestration.state import SanctionsCandidate, SanctionsMatchState

logger = structlog.get_logger(__name__)

def normalize_name(name: str) -> str:
    """Normalize a name for comparison."""
    if not name:
        return ""
    # Unicode NFKD normalization
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('utf-8')
    # Lowercase
    name = name.lower()
    # Strip whitespace
    name = name.strip()
    # Remove punctuation except spaces
    translator = str.maketrans('', '', string.punctuation.replace(' ', ''))
    name = name.translate(translator)
    # Collapse multiple spaces
    name = ' '.join(name.split())
    return name

class SanctionsEntry(BaseModel):
    entry_id: str
    primary_name: str
    normalized_name: str
    aliases: list[str] = Field(default_factory=list)
    normalized_aliases: list[str] = Field(default_factory=list)
    entity_type: str = "Unknown"
    programs: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    identifiers: list[dict[str, str]] = Field(default_factory=list)
    source_metadata: dict = Field(default_factory=dict)

class SanctionsScreener:
    def __init__(self, sanctions_data: list[SanctionsEntry]):
        self.entries = sanctions_data
        self.config = {
            "strong_match_threshold": 0.90,
            "potential_match_threshold": 0.85
        }
        self._build_lookup()
    
    def _build_lookup(self):
        self.exact_lookup = {}
        for entry in self.entries:
            if entry.normalized_name not in self.exact_lookup:
                self.exact_lookup[entry.normalized_name] = []
            self.exact_lookup[entry.normalized_name].append(entry)
            for alias in entry.normalized_aliases:
                if alias not in self.exact_lookup:
                    self.exact_lookup[alias] = []
                self.exact_lookup[alias].append(entry)

    @classmethod
    def from_xml(cls, xml_path: Path) -> 'SanctionsScreener':
        """Parse OFAC SDN Enhanced XML and build screener."""
        logger.info("loading_sanctions_xml", path=str(xml_path))
        
        xml_content = None
        if xml_path.suffix.lower() == '.zip':
            with zipfile.ZipFile(xml_path, 'r') as z:
                xml_filename = next((name for name in z.namelist() if name.lower().endswith('.xml')), None)
                if xml_filename:
                    xml_content = z.read(xml_filename)
                else:
                    logger.error("no_xml_in_zip", path=str(xml_path))
                    return cls([])
        else:
            with open(xml_path, 'rb') as f:
                xml_content = f.read()

        if not xml_content:
            return cls([])

        try:
            tree = ET.fromstring(xml_content)
        except ET.XMLSyntaxError as e:
            logger.error("xml_parse_error", error=str(e))
            return cls([])

        entries = []
        for elem in tree.iter():
            local_name = ET.QName(elem).localname
            if local_name in ('sdnEntry', 'DistinctParty', 'entity'):
                uid = (
                    _find_text(elem, 'uid')
                    or _find_text(elem, 'FixedRef')
                    or _find_text(elem, 'identityId')
                    or elem.get('id')
                    or "Unknown"
                )
                
                primary_name = ""
                aliases = []
                for name_elem in elem.iter():
                    name_type = ET.QName(name_elem).localname
                    if name_type in ('formattedFullName', 'lastName') and name_elem.text:
                        if name_type == 'formattedFullName' and not primary_name:
                            primary_name = name_elem.text
                        elif name_elem.text != primary_name:
                            aliases.append(name_elem.text)
                
                if not primary_name:
                    primary_name = "Unknown"

                entry = SanctionsEntry(
                    entry_id=uid,
                    primary_name=primary_name,
                    normalized_name=normalize_name(primary_name),
                    aliases=[a for a in aliases if a],
                    normalized_aliases=[normalize_name(a) for a in aliases if a],
                    entity_type=_find_text(elem, 'entityType') or "Unknown",
                    programs=[value for value in (_find_all_text(elem, 'sanctionsProgram')) if value],
                    countries=[value for value in (_find_all_text(elem, 'isoCode')) if value],
                    identifiers=[{"type": "document", "value": value} for value in _find_all_text(elem, 'documentNumber') if value],
                )
                entries.append(entry)
        
        logger.info("parsed_sanctions_entries", count=len(entries))
        return cls(entries)

    def screen_name(self, name: str) -> list[SanctionsCandidate]:
        """Check a name against the sanctions list."""
        norm_name = normalize_name(name)
        candidates = []
        
        for entry in self.entries:
            # Check exact match
            if norm_name == entry.normalized_name or norm_name in entry.normalized_aliases:
                candidates.append((1.0, entry, SanctionsMatchState.STRONG_POTENTIAL_MATCH))
                continue
                
            # Fuzzy match primary name
            primary_ratio = fuzz.ratio(norm_name, entry.normalized_name) / 100.0
            primary_token = fuzz.token_sort_ratio(norm_name, entry.normalized_name) / 100.0
            max_score = max(primary_ratio, primary_token)
            
            # Fuzzy match aliases
            for alias in entry.normalized_aliases:
                alias_ratio = fuzz.ratio(norm_name, alias) / 100.0
                alias_token = fuzz.token_sort_ratio(norm_name, alias) / 100.0
                max_score = max(max_score, alias_ratio, alias_token)
                
            if max_score >= self.config["potential_match_threshold"]:
                match_state = SanctionsMatchState.POTENTIAL_MATCH
                if max_score >= self.config["strong_match_threshold"]:
                    match_state = SanctionsMatchState.STRONG_POTENTIAL_MATCH
                    
                candidates.append((max_score, entry, match_state))

        candidates.sort(key=lambda x: x[0], reverse=True)
        top_candidates = []
        for score, entry, match_state in candidates[:5]:
            top_candidates.append(
                SanctionsCandidate(
                    entry_id=entry.entry_id,
                    matched_name=entry.primary_name,
                    query_name=name,
                    match_score=score,
                    match_state=match_state,
                    primary_name=entry.primary_name,
                    aliases=entry.aliases,
                    programs=entry.programs,
                    entity_type=entry.entity_type,
                    countries=entry.countries,
                    identifiers=entry.identifiers,
                )
            )
        
        return top_candidates

def _find_text(elem, name):
    for child in elem.iter():
        if ET.QName(child).localname == name:
            return child.text
    return None


def _find_all_text(elem, name):
    values = []
    for child in elem.iter():
        if ET.QName(child).localname == name and child.text:
            values.append(child.text)
    return values
