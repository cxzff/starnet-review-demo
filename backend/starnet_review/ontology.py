"""Read explicit review dimensions and document constraints from RDF, not UI constants."""
import hashlib
import os
from pathlib import Path

from rdflib import Graph, Namespace, RDF, RDFS

ROOT = Path(__file__).resolve().parents[2]
RV = Namespace('https://starnet.local/review/vocab#')


def local_name(value):
    return str(value).rsplit('/', 1)[-1].rsplit('#', 1)[-1]


def label(graph, node):
    values = list(graph.objects(node, RDFS.label))
    return str(next((v for v in values if v.language == 'zh'), values[0] if values else local_name(node)))


def configuration_snapshot(profile, role):
    return {'profile': {k: profile[k] for k in ('id', 'title', 'filename', 'version')},
            'dimension': {**{k: role[k] for k in ('id', 'uri', 'label')}, 'standards': list(role['standards'])}}


def read_profile(path=None):
    path = Path(path or os.getenv('REVIEW_ONTOLOGY_PATH') or ROOT / 'resources/review-ontology.ttl')
    raw = path.read_bytes()
    graph = Graph().parse(data=raw, format='turtle')
    profile = next(graph.subjects(RDF.type, RV.ReviewProfile))
    dimensions = {}
    for node in sorted(graph.objects(profile, RV.dimension), key=lambda n: (int(graph.value(n, RV.order) or 0), str(n))):
        concepts = sorted(map(str, graph.objects(node, RV.reviewsConcept)))
        dimensions[local_name(node)] = {
            'id': local_name(node), 'uri': str(node), 'label': label(graph, node),
            'name': str(graph.value(node, RV.agentName)), 'scope': str(graph.value(node, RV.scope)),
            'concepts': [local_name(v) for v in concepts], 'concept_uris': concepts,
            'target_types': sorted(local_name(v) for v in graph.objects(node, RV.targetDocumentType)),
            'selection': str(graph.value(node, RV.selection) or 'document'),
            'standards': sorted(map(str, graph.objects(node, RV.standardClause))),
        }
    dependencies = {local_name(n): sorted(local_name(v) for v in graph.objects(n, RV.constrainedBy))
                    for n in graph.subjects(RDF.type, RV.DocumentType)}
    return {'id': hashlib.sha256(raw).hexdigest(), 'title': label(graph, profile),
            'uri': str(profile), 'version': str(graph.value(profile, RV.version)), 'filename': path.name,
            'dimensions': dimensions, 'dependencies': dependencies,
            'document_types': {local_name(n): label(graph, n) for n in graph.subjects(RDF.type, RV.DocumentType)}}
