#!/usr/bin/env python3
"""Compare local binary incident classifiers on supplied, root-separated labels.

Input JSONL: root_case_id, split (train/calibration/development/heldout), text,
optional boolean label, optional project. Labels are supplied references, not
independently verified by this script. Missing labels never enter metrics.
Model paths must be existing local directories; no model downloads or remote code.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import resource
import time

SPLITS = ('train', 'calibration', 'development', 'heldout')


def read_examples(path, limit=None):
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError('example limit must be a positive integer')
    rows, roots = [], {}
    with Path(path).open() as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            if limit is not None and len(rows) >= limit:
                raise ValueError('example limit exceeded')
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError()
                if any(not isinstance(row.get(k), str) or not row[k] for k in ('root_case_id', 'text')) or len(row['root_case_id']) > 256:
                    raise ValueError()
                if row.get('split') not in SPLITS or (row.get('label') is not None and type(row['label']) is not bool):
                    raise ValueError()
                if 'project' in row and (not isinstance(row['project'], str) or len(row['project']) > 256):
                    raise ValueError()
                root = row['root_case_id']
                if root in roots and roots[root] != row['split']:
                    raise ValueError('root case crosses splits')
                roots[root] = row['split']
            except (KeyError, TypeError, ValueError):
                raise ValueError(f'invalid example or root crossing splits at line {number}') from None
            rows.append(row)
    if not rows:
        raise ValueError('empty input')
    return rows


def wilson_lower(successes, count):
    if not count:
        return 0.0
    z = 1.96
    p = successes / count
    return (p + z*z/(2*count) - z*math.sqrt(p*(1-p)/count + z*z/(4*count*count))) / (1+z*z/count)


def measure(rows, probabilities, threshold):
    if len(rows) != len(probabilities):
        raise ValueError('prediction count mismatch')
    if any(not isinstance(p, (float, int)) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError('invalid probability')
    labeled = [(r, float(p)) for r, p in zip(rows, probabilities) if r.get('label') is not None]
    accepted = [(r, p, p >= .5) for r, p in labeled
                if threshold is not None and max(p, 1-p) >= threshold]
    tp = sum(pred and r['label'] for r, _, pred in accepted)
    fp = sum(pred and not r['label'] for r, _, pred in accepted)
    positives = sum(r['label'] for r, _ in labeled)
    root_correct = {}
    for r, _, pred in accepted:
        root_correct[r['root_case_id']] = root_correct.get(r['root_case_id'], True) and pred == r['label']
    return dict(examples=len(rows), labeled=len(labeled), unknown=len(rows)-len(labeled),
                accepted=len(accepted), coverage=len(accepted)/len(labeled) if labeled else None,
                precision=tp/(tp+fp) if tp+fp else None,
                recall=tp/positives if positives else None,
                accepted_label_accuracy=sum(pred == r['label'] for r, _, pred in accepted)/len(accepted) if accepted else None,
                accepted_roots=len(root_correct),
                root_accuracy_lower_95=wilson_lower(sum(root_correct.values()), len(root_correct)),
                brier=sum((p-r['label'])**2 for r, p in labeled)/len(labeled) if labeled else None)


def choose_threshold(rows, probabilities, target=.9):
    # Threshold selection sees calibration only; zero coverage is an explicit abstention.
    for step in range(50, 101):
        threshold = step/100
        m = measure(rows, probabilities, threshold)
        if m['accepted_roots'] and m['root_accuracy_lower_95'] >= target:
            return threshold
    return None


def evaluate_triage_policy(rows, probabilities, policy):
    """Check a frozen policy descriptively; supplied roots/labels grant no authority."""
    fields = {'schema_version', 'threshold', 'min_precision', 'min_recall', 'min_coverage'}
    if (not isinstance(policy, dict) or set(policy) != fields
            or type(policy['schema_version']) is not int or policy['schema_version'] != 1):
        raise ValueError('invalid triage policy schema')
    for name in fields-{'schema_version'}:
        value = policy[name]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('invalid triage policy constraint')
    if policy['threshold'] < .5:
        raise ValueError('triage confidence threshold must be at least 0.5')
    if any(not isinstance(row, dict) or row.get('split') != 'heldout'
           or not isinstance(row.get('root_case_id'), str) or not row['root_case_id']
           or row.get('label') is not None and type(row['label']) is not bool for row in rows):
        raise ValueError('triage validation requires heldout rows with declared root IDs')
    if any(type(p) not in (int, float) for p in probabilities):
        raise ValueError('invalid triage probability')
    measure(rows, probabilities, policy['threshold'])
    grouped = {}
    for row, probability in zip(rows, probabilities):
        root = row.get('root_case_id')
        predicted = probability >= .5 if max(probability, 1-probability) >= policy['threshold'] else None
        label, decisions = grouped.setdefault(root, (row.get('label'), set()))
        if label != row.get('label'):
            raise ValueError('conflicting labels within a declared validation root')
        decisions.add(predicted)
    counts = dict(tp=0, fp=0, fn=0, tn=0, abstained_positive=0, abstained_negative=0,
                  labeled_roots=0, unknown_roots=0)
    root_decisions = []
    for root, (label, decisions) in grouped.items():
        predicted = next(iter(decisions)) if len(decisions) == 1 else None
        root_decisions.append(dict(root_case_id=root, value=predicted, abstain=predicted is None))
        if label is None:
            counts['unknown_roots'] += 1
            continue
        counts['labeled_roots'] += 1
        if predicted is None:
            key = 'abstained_positive' if label else 'abstained_negative'
        else:
            key = ('tp' if label else 'fp') if predicted else ('fn' if label else 'tn')
        counts[key] += 1
    positives = counts['tp']+counts['fn']+counts['abstained_positive']
    negatives = counts['tn']+counts['fp']+counts['abstained_negative']
    accepted = counts['tp']+counts['fp']+counts['fn']+counts['tn']
    metrics = dict(counts, positive_roots=positives, negative_roots=negatives,
        precision=counts['tp']/(counts['tp']+counts['fp']) if counts['tp']+counts['fp'] else None,
        recall=counts['tp']/positives if positives else None,
        coverage=accepted/counts['labeled_roots'] if counts['labeled_roots'] else None)
    failed, missing = [], []
    for name in ('precision', 'recall', 'coverage'):
        if metrics[name] is None:
            missing.append(name+'_undefined')
        elif metrics[name] < policy['min_'+name]:
            failed.append(name+'_below_minimum')
    if not positives or not negatives:
        missing.append('both_validation_classes_required')
    return dict(schema_version=1, status='rejected' if failed else 'insufficient_evidence',
        policy_sha256=hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest(), metrics=metrics,
        reasons=failed+missing+['independent_validation_not_verified'],
        threshold=policy['threshold'], root_independence='unverified', label_authority='unverified',
        decisions=root_decisions, decision_threshold_source='frozen_policy', unit='declared_root',
        qualification='descriptive_only', automatic_promotion=False)


def model_digest(path):
    digest = hashlib.sha256()
    for file in sorted(path.rglob('*')):
        if file.is_file():
            digest.update(str(file.relative_to(path)).encode()+b'\0')
            with file.open('rb') as stream:
                for block in iter(lambda: stream.read(1024*1024), b''):
                    digest.update(block)
    return digest.hexdigest()


def model_predict(method, rows, model_path=None, revision=None, question=None, batch_size=8):
    train = [r for r in rows if r['split'] == 'train' and r.get('label') is not None]
    texts = [r['text'] for r in rows]
    if method == 'baseline':
        if not train:
            raise ValueError('baseline requires labeled training examples')
        return [sum(r['label'] for r in train)/len(train)] * len(rows)
    if method in ('tfidf', 'embeddings'):
        if len({r['label'] for r in train}) != 2:
            raise ValueError('supervised methods require both classes in train')
        from sklearn.linear_model import LogisticRegression
        if method == 'tfidf':
            from sklearn.feature_extraction.text import TfidfVectorizer
            encoder = TfidfVectorizer(ngram_range=(1, 2), max_features=20000)
            x_train = encoder.fit_transform([r['text'] for r in train])
            x_all = encoder.transform(texts)
        else:
            from sentence_transformers import SentenceTransformer
            encoder = SentenceTransformer(str(model_path), revision=revision, local_files_only=True,
                                          trust_remote_code=False, device='cpu')
            x_train = encoder.encode([r['text'] for r in train], batch_size=batch_size)
            x_all = encoder.encode(texts, batch_size=batch_size)
        classifier = LogisticRegression(random_state=0, max_iter=1000)
        classifier.fit(x_train, [r['label'] for r in train])
        return classifier.predict_proba(x_all)[:, list(classifier.classes_).index(True)].tolist()
    if method != 'nli' or not question:
        raise ValueError('NLI requires an atomic --question')
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    tokenizer = AutoTokenizer.from_pretrained(str(model_path), revision=revision, local_files_only=True, trust_remote_code=False)
    model = AutoModelForSequenceClassification.from_pretrained(str(model_path), revision=revision,
                local_files_only=True, trust_remote_code=False, use_safetensors=True).eval()
    labels = {str(v).lower(): int(k) for k, v in model.config.id2label.items()}
    if 'entailment' not in labels:
        raise ValueError('NLI model must declare entailment in id2label')
    probabilities = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start+batch_size]
        inputs = tokenizer(batch, [question]*len(batch), padding=True, truncation=True, return_tensors='pt')
        with torch.inference_mode():
            values = model(**inputs).logits.softmax(-1)[:, labels['entailment']]
        probabilities.extend(values.tolist())
    return probabilities


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Prints a JSON report with split metrics, abstentions and per-example predictions; treat it as private.\n'
        'Baseline uses only the standard library; other methods require optional ML packages.\n'
        'No downloads, remote model code or input edits. Exit 0: report printed; 2: invalid input/dependency.\n'
        'Example: ahl benchmark examples.jsonl --method baseline > /tmp/ahl-benchmark.json')
    parser.add_argument('input', type=Path, help='Existing JSONL examples: root_case_id, split and text; optional boolean label/project, roots must not cross splits')
    parser.add_argument('--method', choices=('baseline', 'tfidf', 'embeddings', 'nli'), required=True, help='Classifier: training-label prevalence, TF-IDF logistic regression, local embeddings, or local NLI entailment')
    parser.add_argument('--model-path', type=Path, help='Existing local model directory, required for embeddings/nli; models are never downloaded')
    parser.add_argument('--revision', help='Optional local model revision passed to embeddings/nli loaders and recorded in the JSON report')
    parser.add_argument('--question', help='Atomic hypothesis for NLI entailment; required with --method nli')
    parser.add_argument('--batch-size', type=int, default=8, help='Positive number of examples per embeddings/NLI batch (default: %(default)s)')
    parser.add_argument('--triage-policy', type=Path, help='Existing frozen JSON policy with schema_version, threshold, min_precision, min_recall and min_coverage; evaluate only heldout roots, with no certification authority')
    parser.add_argument('--max-examples', type=int, help='Optional positive input-row limit; the default reads every example without a row or evidence-text length cap')
    args = parser.parse_args()
    try:
        if args.batch_size <= 0:
            raise ValueError('batch size must be positive')
        if args.method in ('embeddings', 'nli') and (args.model_path is None or not args.model_path.is_dir()):
            raise ValueError('provide an existing local --model-path')
        rows = read_examples(args.input, limit=args.max_examples)
        started = time.monotonic()
        probabilities = model_predict(args.method, rows, args.model_path, args.revision, args.question, args.batch_size)
        elapsed = time.monotonic()-started
        calibration = [(r, p) for r, p in zip(rows, probabilities) if r['split'] == 'calibration']
        threshold = choose_threshold([r for r, _ in calibration], [p for _, p in calibration])
        result = dict(schema_version=1, method=args.method, model_path=str(args.model_path) if args.model_path else None,
                      revision=args.revision, question=args.question, input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
                      threshold=threshold, elapsed_seconds=elapsed, peak_rss_native_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                      threshold_authority='descriptive_smoke_only',
                      probability_semantics='model scores; calibration is measured, not assumed',
                      limitations=['Supplied labels require external verification.', 'No population guarantee under project shift.',
                                   'NLI scores mean hypothesis entailment, not factual truth; neutral mass is not evidence of falsity.',
                                   'NLI tokenization truncates overlong inputs; use bounded evidence windows.',
                                   'TF-IDF uses at most 20,000 vocabulary features.',
                                   'Root confidence interval assumes independent supplied root cases.'], splits={})
        if args.triage_policy is not None:
            policy = json.loads(args.triage_policy.read_text())
            heldout = [(r, p) for r, p in zip(rows, probabilities) if r['split'] == 'heldout']
            result['triage'] = evaluate_triage_policy([r for r, _ in heldout], [p for _, p in heldout], policy)
        for split in SPLITS:
            subset = [(r, p) for r, p in zip(rows, probabilities) if r['split'] == split]
            result['splits'][split] = measure([r for r, _ in subset], [p for _, p in subset], threshold)
        result['model_sha256'] = model_digest(args.model_path) if args.model_path else None
        result['packages'] = {}
        for package in ('scikit-learn', 'torch', 'transformers', 'sentence-transformers'):
            try:
                result['packages'][package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                pass
        result['predictions'] = [dict(root_case_id=r['root_case_id'], split=r['split'],
            probability=p, value=(p >= .5) if threshold is not None and max(p, 1-p) >= threshold else None,
            abstain=threshold is None or max(p, 1-p) < threshold) for r, p in zip(rows, probabilities)]
        result['projects'] = {}
        for project in sorted({str(r.get('project', 'unknown')) for r in rows}):
            subset = [(r, p) for r, p in zip(rows, probabilities)
                      if str(r.get('project', 'unknown')) == project and r['split'] == 'heldout']
            result['projects'][project] = measure([r for r, _ in subset], [p for _, p in subset], threshold)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, ImportError) as exc:
        parser.exit(2, f'benchmark error: {exc}\n')


if __name__ == '__main__':
    main()
