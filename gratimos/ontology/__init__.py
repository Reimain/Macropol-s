"""The concept lattice — what code does, independent of what it is called."""

from __future__ import annotations

from .concepts import Concept, Domain, Ontology, OntologyError, base_ontology

__all__ = ["Ontology", "Concept", "Domain", "OntologyError", "base_ontology"]
