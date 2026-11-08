"""Tool 5: check experience scope, evidence references and review records."""
from nsbi_agent.artifact_readers import load_json, read_file
from nsbi_agent.tools.sample import file_record
from nsbi_agent.validation import Report, fingerprint
from nsbi_agent.validation_contracts import ExperienceRequest


def check_experience(request: ExperienceRequest):
    report, evidence, refs = Report('check_experience'), [], []
    for item in request.evidence:
        try:
            text = None
            if item.role == 'validation':
                text, digest = read_file(item.path)
                record = {'role': item.role, 'path': item.path, 'sha256': digest}
            else:
                _, record = file_record(item.path, item.role)
            evidence.append(record)
            report.check('evidence.file', True, 'readable evidence', record['path'])
            if item.sha256:
                report.check('evidence.hash', record['sha256'] == item.sha256, item.sha256, record['sha256'], 'EVIDENCE_CHANGED')
            refs.append({'path': item.path, 'role': item.role, 'sha256': record['sha256']})
            if item.role == 'validation':
                result = load_json(text)
                if not isinstance(result, dict): raise ValueError('Validation record must be a JSON object')
                status = result.get('status')
                if status == 'failed':
                    report.check('evidence.validation', False, 'succeeded', status, 'VALIDATION_FAILED')
                elif status == 'succeeded':
                    if 'passed' in result and type(result['passed']) is not bool:
                        report.unknown('validation', item.path, 'Review the passed field type')
                    else:
                        report.check('evidence.validation', result.get('passed') is not False,
                                     'consistent successful conclusion', {'status': status, 'passed': result.get('passed')}, 'VALIDATION_FAILED')
                elif 'status' in result:
                    report.unknown('validation', status, 'Review the primary status')
                elif type(result.get('passed')) is bool:
                    report.check('evidence.validation', result['passed'], 'passed=true', result['passed'], 'VALIDATION_FAILED')
                else:
                    report.unknown('validation', item.path, 'Review the validation result')
        except (OSError, ValueError, TypeError) as error:
            report.check('evidence.read', False, 'readable evidence', error, 'EVIDENCE_READ_FAILED')
    if request.review is None:
        report.unknown('review', 'review record requested', 'Add user or Agent review')
    elif request.review.verdict == 'revise':
        report.unknown('review', request.review.reason, 'Revise the lesson using this feedback')
    else:
        report.check('review.accepted', True, 'accept', request.review.by)
    entry = {'lesson': request.lesson, 'applies_to': request.applies_to, 'evidence': refs,
             'review': request.review.model_dump() if request.review else None}
    result = report.result({'tool_number': 5, 'scope': 'Experience fields, evidence fingerprints and review record.',
                            'entry': entry, 'entry_sha256': fingerprint(entry)}, evidence)
    result.data['admission'] = {'succeeded':'ready', 'failed':'revise', 'needs_review':'review'}[result.status]
    if request.review and request.review.verdict == 'revise':
        result.data['admission'] = 'revise'
    return result
