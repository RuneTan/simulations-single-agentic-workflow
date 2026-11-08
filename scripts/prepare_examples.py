"""Materialize portable checker examples with paths belonging to this checkout."""
import json
from pathlib import Path

from nsbi_agent.tools.sample import check_sample
from nsbi_agent.validation_contracts import SampleRequest

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT/'.cache/examples'
    output.mkdir(parents=True, exist_ok=True)
    fixture = ROOT/'tests/fixtures/sm_decay_cards'
    process = output/'proc_card.mg5'
    process.write_text((fixture/'proc_card.mg5').read_text().replace('{PROJECT_ROOT}', str(ROOT)))
    replacements = {'{PROJECT_ROOT}': str(ROOT), '{EXAMPLE_OUTPUT}': str(output)}
    for name in ('check_sample.json', 'check_existing_cards.json', 'check_experience.json'):
        text = (ROOT/'examples'/name).read_text()
        for token, value in replacements.items():
            text = text.replace(token, value)
        # Parse and encode to validate the resulting input.
        (output/name).write_text(json.dumps(json.loads(text), indent=2)+'\n')
    sample = json.loads((output/'check_sample.json').read_text())
    result = check_sample(SampleRequest.model_validate(sample))
    (output/'sample_report.json').write_text(result.model_dump_json(indent=2)+'\n')
    print(output)


if __name__ == '__main__':
    main()
