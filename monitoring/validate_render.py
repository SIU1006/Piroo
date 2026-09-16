"""Extract and verify monitoring configuration from `helm template` output.

Usage: helm template asyncvtp k8s --namespace demo -f k8s/values-dev.yaml |
       python monitoring/validate_render.py --output-dir /tmp/monitoring
"""

import argparse
import sys
from pathlib import Path

import yaml


class UniqueKeyLoader(yaml.SafeLoader):
    """Fail on duplicate YAML keys, including nested Prometheus rule files."""


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _resource(resources, kind, name):
    return next(item for item in resources if item["kind"] == kind and item["metadata"]["name"] == name)


def validate(source, output_dir, expected_receiver, expected_namespace):
    resources = [item for item in yaml.load_all(source, Loader=UniqueKeyLoader) if item]
    prometheus_cm = _resource(resources, "ConfigMap", "prometheus-config")
    rules_cm = _resource(resources, "ConfigMap", "prometheus-alert-rules")
    alertmanager_cm = _resource(resources, "ConfigMap", "alertmanager-config")
    prometheus = yaml.load(prometheus_cm["data"]["prometheus.yml"], Loader=UniqueKeyLoader)
    rules = yaml.load(rules_cm["data"]["asyncvtp.rules.yml"], Loader=UniqueKeyLoader)
    alertmanager = yaml.load(alertmanager_cm["data"]["alertmanager.yml"], Loader=UniqueKeyLoader)

    deployment = _resource(resources, "Deployment", "prometheus")["spec"]["template"]
    volumes = {volume["name"]: volume for volume in deployment["spec"]["volumes"]}
    mounts = deployment["spec"]["containers"][0]["volumeMounts"]
    assert volumes["rules"]["configMap"]["name"] == "prometheus-alert-rules"
    assert any(mount["name"] == "rules" and mount["mountPath"] == "/etc/prometheus/rules" for mount in mounts)
    assert deployment["metadata"]["annotations"]["checksum/rules"]
    assert "/etc/prometheus/rules/*.yml" in prometheus["rule_files"]
    assert prometheus["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == ["alertmanager-service:9093"]
    assert any(rule["alert"] == "CanaryCheckNotRunning" for group in rules["groups"] for rule in group["rules"])
    assert any(rule["alert"] == "TaskFailureRateSLOBreach" for group in rules["groups"] for rule in group["rules"])
    assert any(
        label["source_labels"] == ["__meta_kubernetes_namespace"]
        and label["action"] == "keep" and label["regex"] == expected_namespace
        for label in prometheus["scrape_configs"][0]["relabel_configs"]
    )
    receiver = alertmanager["route"]["receiver"]
    assert receiver == expected_receiver
    assert receiver in {item["name"] for item in alertmanager["receivers"]}

    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in (
        ("prometheus.yml", prometheus_cm["data"]["prometheus.yml"]),
        ("asyncvtp.rules.yml", rules_cm["data"]["asyncvtp.rules.yml"]),
        ("alertmanager.yml", alertmanager_cm["data"]["alertmanager.yml"]),
    ):
        (output_dir / filename).write_text(content, encoding="utf-8")
    print(f"Monitoring configuration verified; Alertmanager receiver: {receiver}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--receiver", choices=("local", "slack"), required=True)
    parser.add_argument("--namespace", required=True)
    args = parser.parse_args()
    try:
        validate(sys.stdin, args.output_dir, args.receiver, args.namespace)
    except (AssertionError, KeyError, StopIteration, ValueError, yaml.YAMLError) as exc:
        sys.exit(f"Invalid monitoring configuration: {exc}")
