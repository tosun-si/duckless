"""InfraDeployer on Infrastructure Manager (Terraform run by Google, state kept in the project)."""

import contextlib
from collections.abc import Mapping
from typing import Any

from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.cloud import config_v1

from duckless.core.infra import InfraStatus

APPLY_TIMEOUT_SECONDS = 30 * 60
LABELS = {"app": "duckless"}


def deployment_name(project: str, region: str, deployment_id: str) -> str:
    return f"projects/{project}/locations/{region}/deployments/{deployment_id}"


def build_deployment(
    name: str, source_uri: str, inputs: Mapping[str, Any], service_account: str
) -> config_v1.Deployment:
    return config_v1.Deployment(
        name=name,
        service_account=service_account
        if service_account.startswith("projects/")
        else f"projects/{name.split('/')[1]}/serviceAccounts/{service_account}",
        terraform_blueprint=config_v1.TerraformBlueprint(
            gcs_source=source_uri,
            input_values={k: config_v1.TerraformVariable(input_value=v) for k, v in inputs.items()},
        ),
        labels=LABELS,
    )


def failure_detail(deployment: config_v1.Deployment, revision: config_v1.Revision | None) -> str:
    """The most useful error Infra Manager kept: Terraform errors first, then the state detail."""
    tf_errors = [e.error_description or e.resource_address for e in (revision.tf_errors if revision else ())]
    tf_errors += [e.error_description or e.resource_address for e in deployment.tf_errors]
    logs = revision.logs if revision and revision.logs else deployment.error_logs
    parts = [
        *tf_errors[:5],
        deployment.state_detail or (revision.state_detail if revision else ""),
        logs and f"logs: {logs}",
    ]
    return "\n".join(p for p in parts if p) or deployment.state.name


class InfraManagerDeployer:
    def __init__(self, client: config_v1.ConfigClient) -> None:
        self._client = client

    def _revision(self, deployment: config_v1.Deployment) -> config_v1.Revision | None:
        return self._client.get_revision(name=deployment.latest_revision) if deployment.latest_revision else None

    def _status(self, deployment: config_v1.Deployment) -> InfraStatus:
        revision = self._revision(deployment)
        outputs = {k: o.value for k, o in revision.apply_results.outputs.items()} if revision else {}
        failed = deployment.state == config_v1.Deployment.State.FAILED
        return InfraStatus(
            deployment=deployment.name,
            state=deployment.state.name,
            outputs=outputs,
            error=failure_detail(deployment, revision) if failed else None,
        )

    def get(self, project: str, region: str, deployment_id: str) -> InfraStatus | None:
        try:
            return self._status(self._client.get_deployment(name=deployment_name(project, region, deployment_id)))
        except NotFound:
            return None

    def apply(
        self,
        project: str,
        region: str,
        deployment_id: str,
        source_uri: str,
        inputs: Mapping[str, Any],
        service_account: str,
    ) -> InfraStatus:
        name = deployment_name(project, region, deployment_id)
        deployment = build_deployment(name, source_uri, inputs, service_account)
        exists = self.get(project, region, deployment_id) is not None
        operation = (
            self._client.update_deployment(deployment=deployment)
            if exists
            else self._client.create_deployment(
                parent=f"projects/{project}/locations/{region}", deployment_id=deployment_id, deployment=deployment
            )
        )
        # On failure the deployment carries the details, read below.
        with contextlib.suppress(GoogleAPICallError):
            operation.result(timeout=APPLY_TIMEOUT_SECONDS)
        return self._status(self._client.get_deployment(name=name))

    def destroy(self, project: str, region: str, deployment_id: str) -> InfraStatus:
        name = deployment_name(project, region, deployment_id)
        if self.get(project, region, deployment_id) is None:
            return InfraStatus(name, "DELETED")
        try:
            # force: also delete the deployment's revisions (nested resources), else the call is refused.
            self._client.delete_deployment(
                request=config_v1.DeleteDeploymentRequest(
                    name=name, force=True, delete_policy=config_v1.DeleteDeploymentRequest.DeletePolicy.DELETE
                )
            ).result(timeout=APPLY_TIMEOUT_SECONDS)
        except GoogleAPICallError as e:
            current = self.get(project, region, deployment_id)
            return InfraStatus(
                name, current.state if current else "UNKNOWN", error=current.error if current else str(e)
            )
        return InfraStatus(name, "DELETED")
