"""The Wikidata schemas of the site are mined by rdfsolve around the Wikidata items of the site data.

Every statement of these items is read, and every statement about the authors of works (p:P50,
with the series ordinal), in the main graph and in the graph of scholarly works of the Wikidata
Query Service. Items are one class, wikibase:Item (the query service leaves out their type), so
that one model has every field; the classes that P31 gives stay values of instance_of. A new run
shows what changed in Wikidata, and whether the site can still read the fields that it needs.
"""

import json
import re
import sys

from rdfsolve import MinedSchema
from rdfsolve.mining.miner import SchemaMiner
from rdfsolve.mining.wikibase_strategy import WikibaseScopeStrategy

from cvdata import wikidata
from cvdata.config import ROOT

ENTITY = re.compile(r"http://www\.wikidata\.org/entity/[QP][0-9]+")


def seeds() -> list[str]:
    """The Wikidata items and properties that the site data links."""
    return sorted({iri for path in (ROOT / "_data").glob("*.yml") for iri in ENTITY.findall(path.read_text(encoding="utf-8"))})


def mine(scholarly: bool) -> MinedSchema:
    strategy = WikibaseScopeStrategy(
        seeds(),
        follow=[wikidata.P + "P50"],
        prefix_classes={wikidata.WD + "Q": str(wikidata.WIKIBASE.Item), wikidata.WD + "statement/": str(wikidata.WIKIBASE.Statement)},
        declarations=wikidata.ENDPOINTS[False] if scholarly else None,  # the scholarly graph declares no properties
    )
    with SchemaMiner(wikidata.ENDPOINTS[scholarly], strategy=strategy, counts=False, delay=1) as miner:
        schema = miner.mine(wikidata.SCHEMAS[scholarly].name.removesuffix(".schema.json"))
        print(f"{wikidata.ENDPOINTS[scholarly]}: {miner.last_report.completion_state}, {miner.last_report.config['scope']}")
    return schema


def rows(schema: MinedSchema) -> set[tuple[str, str, str]]:
    return {(p.subject_class, p.property_uri, p.object_class) for p in schema.patterns}


def main() -> None:
    failed = False
    for scholarly, path in wikidata.SCHEMAS.items():
        schema = mine(scholarly)
        old = rows(MinedSchema.from_json(path)) if path.exists() else set()
        new = rows(schema)
        print(f"{path.relative_to(ROOT)}: {len(new)} patterns, {len(new - old)} new, {len(old - new)} gone")
        missing = wikidata.missing(schema, scholarly)
        for model, fields in missing.items():
            print(f"  {model} has no {', '.join(sorted(fields))}; the site reads these fields")
        if missing:  # The site keeps the schema that it can read; the new one is kept for review.
            failed, path = True, ROOT / "output" / path.name
            path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(schema.to_dict(), indent=1) + "\n", encoding="utf-8")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
