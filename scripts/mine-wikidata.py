"""The Wikidata schemas of the site are mined with rdfsolve around the Wikidata items of the site data."""

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


def mine(scholarly: bool) -> tuple[MinedSchema, str]:
    """The schema of one graph, and whether every query of the run gave its rows."""
    strategy = WikibaseScopeStrategy(
        seeds(),
        follow=[wikidata.P + "P50"],
        prefix_classes={wikidata.WD + "Q": str(wikidata.WIKIBASE.Item), wikidata.WD + "statement/": str(wikidata.WIKIBASE.Statement)},
        ignore_classes=[wikidata.P + "novalue/"],  # wdno:P
        declarations=wikidata.ENDPOINTS[False] if scholarly else None,  # the scholarly graph declares no properties
    )
    with SchemaMiner(wikidata.ENDPOINTS[scholarly], strategy=strategy, counts=False, delay=1) as miner:
        schema = miner.mine(wikidata.SCHEMAS[scholarly].name.removesuffix(".schema.json"))
        state = miner.last_report.completion_state
        print(f"{wikidata.ENDPOINTS[scholarly]}: {state}, {miner.last_report.config['scope']}")
    return schema, state


def rows(schema: MinedSchema) -> set[tuple[str, str, str]]:
    return {(p.subject_class, p.property_uri, p.object_class) for p in schema.patterns}


def main() -> None:
    failed = False
    for scholarly, path in wikidata.SCHEMAS.items():
        schema, state = mine(scholarly)
        old = rows(MinedSchema.from_json(path)) if path.exists() else set()
        new = rows(schema)
        print(f"{path.relative_to(ROOT)}: {len(new)} patterns, {len(new - old)} new, {len(old - new)} gone")
        missing = wikidata.missing(schema, scholarly)
        for model, fields in missing.items():
            print(f"  {model} has no {', '.join(sorted(fields))}; the site reads these fields")
        if state != "complete":
            print("  some queries failed; the schema can lack rows")
        if missing or state != "complete":  # The site keeps the schema that it can read; the new one goes to output/ for review.
            failed, path = True, ROOT / "output" / path.name
            path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(schema.to_dict(), indent=1) + "\n", encoding="utf-8")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
