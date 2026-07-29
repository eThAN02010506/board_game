"""Source providers used by the canonical AI KP context assembler."""

from ai_kp.director.context_sources.investigator import InvestigatorContextProvider
from ai_kp.director.context_sources.module import ModuleContextProvider, ModuleScope

__all__ = [
    "InvestigatorContextProvider",
    "ModuleContextProvider",
    "ModuleScope",
]
