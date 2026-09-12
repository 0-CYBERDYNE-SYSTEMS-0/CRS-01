"""Deep research package — recursive lineage discovery across providers.

Phase 1: built around the existing WikipediaSearchProvider. Tavily and
Perplexity providers activate automatically when their keys are present in
the environment. The lineage extractor parses parent-strain tuples from
snippet text using multiple regex patterns; the orchestrator fans out across
providers, dedupes by URL within a session, persists claims to the existing
JSONL ledger, and returns a ReactFlow-shaped lineage graph.
"""
from .providers import get_research_providers, ProviderResult
from .extractor import extract_lineage, LineageClaim
from .orchestrator import ResearchOrchestrator, ResearchRun
from .wikipedia_full import fetch_wikipedia_full

__all__ = [
    "get_research_providers",
    "ProviderResult",
    "extract_lineage",
    "LineageClaim",
    "ResearchOrchestrator",
    "ResearchRun",
    "fetch_wikipedia_full",
]