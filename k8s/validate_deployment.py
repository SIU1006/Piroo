"""Check placement independence and singleton ownership in both environment renders."""
import argparse
from pathlib import Path

import yaml


def validate(staging, production):
    environments = {"asyncvtp-staging": staging, "asyncvtp-prod": production}
    owners = []
    for namespace, resources in environments.items():
        resources = [resource for resource in resources if resource]
        assert not any(r["kind"] == "PersistentVolumeClaim" and r["metadata"]["name"] == "uploads-pvc"
                       for r in resources)
        deployments = {r["metadata"]["name"]: r for r in resources if r["kind"] == "Deployment"}
        for name in ("fastapi", "celery"):
            spec = deployments[name]["spec"]["template"]["spec"]
            assert spec["serviceAccountName"] == "asyncvtp-uploads"
            assert any("emptyDir" in volume for volume in spec["volumes"])
        for deployment in deployments.values():
            volumes = deployment["spec"]["template"]["spec"].get("volumes", [])
            assert not any(v.get("persistentVolumeClaim", {}).get("claimName") == "uploads-pvc"
                           for v in volumes)
        for r in resources:
            if r["kind"] == "APIService":
                assert r["metadata"]["name"] == "v1beta1.external.metrics.k8s.io"
                owners.append(r["spec"]["service"]["namespace"])
        if namespace == "asyncvtp-staging":
            assert "prometheus-adapter" not in deployments
    assert owners == ["asyncvtp-prod"], owners
    prod_maps = {r["metadata"]["name"]: r for r in production if r and r["kind"] == "ConfigMap"}
    prometheus = yaml.safe_load(prod_maps["prometheus-config"]["data"]["prometheus.yml"])
    assert any(config.get("static_configs", [{}])[0].get("labels", {}).get("namespace")
               == "asyncvtp-staging" for config in prometheus["scrape_configs"])
    adapter = yaml.safe_load(prod_maps["prometheus-adapter-config"]["data"]["config.yml"])
    query = adapter["externalRules"][0]["metricsQuery"]
    assert "<<.LabelMatchers>>" in query and "max by (namespace)" in query
    print("Independent upload scratch and one production-owned external-metrics API verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--production", type=Path, required=True)
    args = parser.parse_args()
    validate(list(yaml.safe_load_all(args.staging.read_text())),
             list(yaml.safe_load_all(args.production.read_text())))
