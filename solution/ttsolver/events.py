from dataclasses import dataclass, field, asdict
from .values import describe


@dataclass
class Event:
    kind: str
    scenario: str
    step: int
    method: str | None
    target: str | None = None
    site: str | None = None
    evidence: str = ''
    confidence: str = 'PROVEN'
    conditions: tuple = ()
    values: dict = field(default_factory=dict)

    def json(self):
        return describe(asdict(self))


class AnnotationPolicy:
    """Reference policy, with explicit, general framework boundaries.

    * Security filter is a modeled framework boundary; JWT utility internals
      are not reconstructed from a fabricated request/token.
    * Authentication callbacks have no synthetic direct caller.
    * ORM runs nonempty source constructors without a repository->ctor call.
    * Lazy SQL belongs to the repository operation that loaded the entity.
    No gold file or scenario ID is consulted here.
    """
    relations = {'MethodEntered': 'executes', 'DirectProjectCall': 'calls',
                 'HttpHandlerEntered': 'remote', 'MessageDelivered': 'remote',
                 'SqlAccess': 'data'}

    def project(self, events, catalog):
        edges = set()
        for e in events:
            relation = self.relations.get(e.kind)
            if not relation or e.confidence not in ('PROVEN', 'STRONG'):
                continue
            source, target = e.method, e.target
            if relation == 'executes':
                source, target = 'scenario:' + e.scenario, e.method
            if target not in catalog.nodes:
                raise ValueError('Noncanonical event target: ' + str(target))
            if relation == 'data' and (source, target) not in catalog.data_pairs:
                raise ValueError('SQL outside candidate whitelist')
            if relation == 'remote' and catalog.nodes[source]['module'] == catalog.nodes[target]['module']:
                continue
            edges.add((source, relation, target))
        return edges


def serialize(edges):
    return [dict(source=s, relation=r, target=t) for s, r, t in sorted(edges)]
