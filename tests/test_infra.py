from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from duckless import service
from duckless.adapters.gcp_bootstrap import GcpInfraBootstrap, is_unknown_member, module_files
from duckless.adapters.infra_manager import build_deployment, deployment_name
from duckless.cli import infra_lines
from duckless.core.infra import (
    IAM_ADMIN_ROLE,
    INFRA_SA_ROLES,
    RUNNER_GRANTS_ONLY,
    RUNNER_PROJECT_ROLES,
    InfraRequest,
    InfraStatus,
    InvalidInfraRequestError,
    default_runner_tag,
    deployment_inputs,
    envrc_lines,
    with_bindings,
    without_bindings,
)
from duckless.core.org_policy import SA_CREATION, OrgPolicy

REQUEST = InfraRequest(project="acme-data", region="europe-west1", data_buckets=("acme-lake",))
SA = "duckless-infra@acme-data.iam.gserviceaccount.com"


class FakeBootstrap:
    def __init__(
        self,
        policy_changes: bool = True,
        org_policies: dict[str, OrgPolicy] | None = None,
        network_peered: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.network_peered = network_peered
        self.policy_changes = policy_changes
        self.org_policies = org_policies or {}

    def effective_org_policy(self, project: str, constraint: str) -> OrgPolicy:
        return self.org_policies.get(constraint, OrgPolicy(constraint))

    def enable_apis(self, project: str, apis: tuple[str, ...]) -> None:
        self.calls.append("apis")

    def ensure_service_account(self, project: str, account_id: str, display_name: str) -> str:
        self.calls.append(f"sa:{account_id}")
        return f"{account_id}@{project}.iam.gserviceaccount.com"

    def grant_project_roles(self, project: str, member: str, roles: tuple[str, ...], condition=None) -> bool:
        self.calls.append(f"grant:{member}" + (f":if:{condition['title']}" if condition else ""))
        if condition is None:
            self.granted_roles = roles
        else:
            self.conditional_grants = (roles, condition)
        return self.policy_changes

    def ensure_bucket(self, project: str, region: str, bucket: str) -> None:
        self.calls.append(f"bucket:{bucket}")

    def upload_directory(self, local_dir: Path, bucket: str, prefix: str) -> str:
        self.calls.append(f"upload:{prefix}")
        return f"gs://{bucket}/{prefix}"

    def revoke_project_roles(self, project: str, member: str, roles: tuple[str, ...], condition=None) -> None:
        self.calls.append(f"revoke:{member}" + (f":if:{condition['title']}" if condition else ""))
        self.revoked_roles = getattr(self, "revoked_roles", ()) + tuple(roles)

    def has_private_service_access(self, project: str, network: str) -> bool:
        self.calls.append(f"psa?:{network}")
        return self.network_peered

    def create_private_service_access(self, project: str, network: str, range_name: str) -> None:
        self.calls.append(f"create-psa:{network}:{range_name}")

    def delete_cloud_run_jobs(self, project: str, region: str, service_account: str) -> int:
        self.calls.append(f"delete-cloud-run-jobs:{service_account}")
        return 2

    def delete_service_account(self, project: str, email: str) -> None:
        self.calls.append(f"delete-sa:{email}")

    def delete_bucket(self, bucket: str) -> None:
        self.calls.append(f"delete-bucket:{bucket}")

    def bucket_has_objects(self, bucket: str) -> bool:
        return getattr(self, "work_bucket_full", False)


class FakeDeployer:
    def __init__(self, destroy_error: str | None = None, current: InfraStatus | None = None) -> None:
        self.current = current
        self.applied: list[tuple[str, str, Mapping[str, Any], str]] = []
        self.destroyed: list[str] = []
        self.destroy_error = destroy_error

    def apply(self, project, region, deployment_id, source_uri, inputs, service_account) -> InfraStatus:
        self.applied.append((deployment_id, source_uri, inputs, service_account))
        return InfraStatus(deployment_id, "ACTIVE", {"envrc": "export DUCKLESS_PROJECT=acme-data\n"})

    def get(self, project, region, deployment_id) -> InfraStatus | None:
        return self.current

    def destroy(self, project, region, deployment_id) -> InfraStatus:
        self.destroyed.append(deployment_id)
        if self.destroy_error:
            return InfraStatus(deployment_id, "FAILED", error=self.destroy_error)
        return InfraStatus(deployment_id, "DELETED")


def init(
    bootstrap: FakeBootstrap, deployer: FakeDeployer, waits: list[int], request: InfraRequest = REQUEST
) -> InfraStatus:
    return service.init_infra(
        request,
        bootstrap=bootstrap,
        deployer=deployer,
        module_dir=Path("unused"),
        version="0.1.0",
        on_step=lambda step: None,
        wait_for_iam=lambda: waits.append(1),
    )


class TestInitInfra:
    def test_given_new_project_when_init_then_bootstraps_uploads_then_applies_with_infra_sa(self) -> None:
        # given
        bootstrap, deployer, waits = FakeBootstrap(), FakeDeployer(), []

        # when
        status = init(bootstrap, deployer, waits)

        # then
        assert bootstrap.calls == [
            "apis",
            "sa:duckless-infra",
            f"grant:serviceAccount:{SA}",
            f"grant:serviceAccount:{SA}:if:duckless-runner-roles-only",
            "bucket:acme-data-duckless-infra",
            "upload:module/0.1.0",
            # the infra account keeps no role once init is over
            f"revoke:serviceAccount:{SA}",
            f"revoke:serviceAccount:{SA}:if:duckless-runner-roles-only",
        ]
        deployment_id, source, inputs, service_account = deployer.applied[0]
        assert (deployment_id, source, service_account) == (
            "duckless",
            "gs://acme-data-duckless-infra/module/0.1.0",
            SA,
        )
        assert inputs["data_buckets"] == ["acme-lake"]
        assert status.ok
        assert waits == [1]

    def test_given_blocking_org_policy_when_init_then_stops_before_creating_anything(self) -> None:
        # given
        bootstrap = FakeBootstrap(org_policies={SA_CREATION: OrgPolicy(SA_CREATION, enforced=True)})
        deployer = FakeDeployer()

        # when
        status = init(bootstrap, deployer, [])

        # then
        assert status.state == "BLOCKED"
        assert "disableServiceAccountCreation" in (status.error or "")
        assert bootstrap.calls == ["apis"]
        assert deployer.applied == []

    def test_given_grants_already_in_place_when_init_then_does_not_wait_for_iam(self) -> None:
        # given
        waits: list[int] = []

        # when
        init(FakeBootstrap(policy_changes=False), FakeDeployer(), waits)

        # then
        assert waits == []

    def test_given_deployment_when_destroying_then_deletes_it_then_infra_sa_and_staging(self) -> None:
        # given
        bootstrap, deployer = FakeBootstrap(), FakeDeployer()

        # when
        status = service.destroy_infra(REQUEST, bootstrap=bootstrap, deployer=deployer, on_step=lambda step: None)

        # then
        assert deployer.destroyed == ["duckless"]
        assert bootstrap.calls == [
            "delete-cloud-run-jobs:duckless-runner@acme-data.iam.gserviceaccount.com",
            f"grant:serviceAccount:{SA}",
            f"grant:serviceAccount:{SA}:if:duckless-runner-roles-only",
            f"revoke:serviceAccount:{SA}",
            f"revoke:serviceAccount:{SA}:if:duckless-runner-roles-only",
            f"delete-sa:{SA}",
            "delete-bucket:acme-data-duckless-infra",
        ]
        assert status.state == "DELETED"

    def test_given_deletion_failure_when_destroying_then_keeps_infra_sa_for_a_retry(self) -> None:
        # given
        bootstrap, deployer = FakeBootstrap(), FakeDeployer(destroy_error="bucket not empty")

        # when
        status = service.destroy_infra(REQUEST, bootstrap=bootstrap, deployer=deployer, on_step=lambda step: None)

        # then: past runs are gone, the infra SA stays for a retry, without its roles
        assert not status.ok
        assert bootstrap.calls == [
            "delete-cloud-run-jobs:duckless-runner@acme-data.iam.gserviceaccount.com",
            f"grant:serviceAccount:{SA}",
            f"grant:serviceAccount:{SA}:if:duckless-runner-roles-only",
            f"revoke:serviceAccount:{SA}",
            f"revoke:serviceAccount:{SA}:if:duckless-runner-roles-only",
        ]


class TestInfraRequest:
    @pytest.mark.parametrize("name", ["Duck", "dl", "a-name-way-too-long-for-sa", "9lives"])
    def test_given_bad_name_when_building_then_raises(self, name: str) -> None:
        # when / then
        with pytest.raises(InvalidInfraRequestError):
            InfraRequest(project="p", region="r", name=name)

    def test_given_request_when_building_inputs_then_matches_module_variables(self) -> None:
        # given
        variables = (Path("duckless/terraform/variables.tf")).read_text()

        # when
        inputs = deployment_inputs(REQUEST)

        # then
        assert all(f'variable "{name}"' in variables for name in inputs)


class TestDefaultRunnerTag:
    @pytest.mark.parametrize(
        ("version", "tag"),
        [("0.1.0", "0.1.0"), ("1.2.3rc1", "1.2.3rc1"), ("0.1.0.dev0", "edge"), ("0.1.0+local", "edge")],
    )
    def test_given_cli_version_when_defaulting_then_released_versions_pin_their_own_image(
        self, version: str, tag: str
    ) -> None:
        # when / then
        assert default_runner_tag(version) == tag


class TestWithBindings:
    def test_given_missing_roles_when_merging_then_adds_member_and_keeps_others(self) -> None:
        # given
        policy = {
            "etag": "abc",
            "bindings": [
                {"role": "roles/storage.admin", "members": ["user:a@acme.com"]},
                {"role": "roles/storage.admin", "members": ["user:b@acme.com"], "condition": {"title": "t"}},
            ],
        }

        # when
        updated, changed = with_bindings(policy, "serviceAccount:x", ("roles/storage.admin", "roles/config.agent"))

        # then
        assert changed
        assert updated["etag"] == "abc"
        assert updated["bindings"][0]["members"] == ["user:a@acme.com", "serviceAccount:x"]
        assert updated["bindings"][1]["members"] == ["user:b@acme.com"]  # conditional binding untouched
        assert {"role": "roles/config.agent", "members": ["serviceAccount:x"]} in updated["bindings"]

    def test_given_all_roles_granted_when_merging_then_unchanged(self) -> None:
        # given
        policy = {"bindings": [{"role": r, "members": ["serviceAccount:x"]} for r in INFRA_SA_ROLES]}

        # when
        _, changed = with_bindings(policy, "serviceAccount:x", INFRA_SA_ROLES)

        # then
        assert not changed


class TestWithoutBindings:
    def test_given_member_in_roles_when_removing_then_drops_it_and_empty_bindings(self) -> None:
        # given
        policy = {
            "bindings": [
                {"role": "roles/storage.admin", "members": ["serviceAccount:x", "user:a@acme.com"]},
                {"role": "roles/config.agent", "members": ["serviceAccount:x"]},
                {"role": "roles/config.agent", "members": ["serviceAccount:x"], "condition": {"title": "t"}},
            ]
        }

        # when
        updated, changed = without_bindings(policy, "serviceAccount:x", ("roles/storage.admin", "roles/config.agent"))

        # then
        assert changed
        assert updated["bindings"] == [
            {"role": "roles/storage.admin", "members": ["user:a@acme.com"]},
            {"role": "roles/config.agent", "members": ["serviceAccount:x"], "condition": {"title": "t"}},
        ]

    def test_given_member_absent_when_removing_then_unchanged(self) -> None:
        # when / then
        assert without_bindings({"bindings": []}, "serviceAccount:x", INFRA_SA_ROLES)[1] is False


class TestInfraAdapters:
    def test_given_module_dir_with_local_state_when_listing_then_skips_terraform_cache(self, tmp_path: Path) -> None:
        # given
        (tmp_path / ".terraform" / "providers").mkdir(parents=True)
        (tmp_path / ".terraform" / "providers" / "x").write_text("bin")
        (tmp_path / ".terraform.lock.hcl").write_text("lock")
        (tmp_path / "main.tf").write_text("")

        # when / then
        assert [p.name for p in module_files(tmp_path)] == ["main.tf"]

    def test_given_inputs_when_building_deployment_then_gcs_source_inputs_and_full_sa_name(self) -> None:
        # when
        deployment = build_deployment(
            deployment_name("acme-data", "europe-west1", "duckless"), "gs://b/module/0.1.0", {"name": "duckless"}, SA
        )

        # then
        assert deployment.terraform_blueprint.gcs_source == "gs://b/module/0.1.0"
        assert deployment.terraform_blueprint.input_values["name"].input_value == "duckless"
        assert deployment.service_account == f"projects/acme-data/serviceAccounts/{SA}"

    def test_given_active_deployment_when_rendering_then_prints_envrc_lines(self) -> None:
        # given
        status = InfraStatus("duckless", "ACTIVE", {"envrc": "export A=1\n  export B=2\n"})

        # when / then
        assert envrc_lines(status) == ["export A=1", "export B=2"]
        assert infra_lines(status)[-2:] == ["export A=1", "export B=2"]


class TestUnknownMember:
    def test_given_fresh_service_account_error_when_checking_then_retryable(self) -> None:
        # given
        body = '{"error": {"message": "Service account x@p.iam.gserviceaccount.com does not exist."}}'

        # when / then
        assert is_unknown_member(400, body)

    def test_given_other_errors_when_checking_then_not_retryable(self) -> None:
        # when / then
        assert not is_unknown_member(403, "does not exist")
        assert not is_unknown_member(400, "etag mismatch")


class TestThrottling:
    def test_given_quota_errors_when_calling_then_retries_until_success(self, monkeypatch) -> None:
        # given: two 429s, then a success
        monkeypatch.setattr("duckless.adapters.gcp_bootstrap.time.sleep", lambda s: None)
        responses = iter([FakeResponse(429), FakeResponse(429), FakeResponse(200, {"ok": True})])
        session = type("Session", (), {"request": lambda self, method, url, **kw: next(responses)})()
        bootstrap = GcpInfraBootstrap(session, storage_client=None)

        # when
        body = bootstrap._call("GET", "https://example.test")

        # then
        assert body == {"ok": True}

    def test_given_client_error_when_calling_then_no_retry(self, monkeypatch) -> None:
        # given
        calls: list[int] = []
        monkeypatch.setattr("duckless.adapters.gcp_bootstrap.time.sleep", lambda s: None)
        session = type(
            "Session", (), {"request": lambda self, method, url, **kw: calls.append(1) or FakeResponse(403)}
        )()

        # when
        response = GcpInfraBootstrap(session, storage_client=None)._request("GET", "https://example.test")

        # then
        assert (response.status_code, len(calls)) == (403, 1)


class FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.content = b"x" if body else b""
        self.text = ""

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class TestLeastPrivilege:
    def test_given_init_when_granting_then_iam_admin_is_conditional_and_never_plain(self) -> None:
        # given
        bootstrap = FakeBootstrap()

        # when
        init(bootstrap, FakeDeployer(), [])

        # then
        assert IAM_ADMIN_ROLE not in bootstrap.granted_roles
        roles, condition = bootstrap.conditional_grants
        assert roles == (IAM_ADMIN_ROLE,) and condition == RUNNER_GRANTS_ONLY

    def test_given_apply_failure_when_init_then_roles_are_revoked_anyway(self) -> None:
        # given
        class FailingDeployer(FakeDeployer):
            def apply(self, *args, **kwargs):
                raise RuntimeError("apply failed")

        bootstrap = FakeBootstrap()

        # when
        with pytest.raises(RuntimeError):
            init(bootstrap, FailingDeployer(), [])

        # then
        assert bootstrap.calls[-2:] == [
            f"revoke:serviceAccount:{SA}",
            f"revoke:serviceAccount:{SA}:if:duckless-runner-roles-only",
        ]

    def test_given_older_install_when_revoking_then_plain_iam_admin_is_removed_too(self) -> None:
        # given: DuckLess 0.4.0 granted project IAM admin without a condition
        bootstrap = FakeBootstrap()

        # when
        init(bootstrap, FakeDeployer(), [])

        # then
        assert IAM_ADMIN_ROLE in bootstrap.revoked_roles

    def test_given_condition_when_reading_it_then_only_runner_roles_are_grantable(self) -> None:
        # when / then
        expression = RUNNER_GRANTS_ONLY["expression"]
        assert expression.startswith("api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly([")
        assert all(f"'{role}'" in expression for role in RUNNER_PROJECT_ROLES)
        assert "roles/owner" not in expression


class TestConditionalBindings:
    def test_given_conditional_grant_when_adding_then_separate_binding_and_policy_version_3(self) -> None:
        # given: the member already has the role without condition
        policy = {"version": 1, "bindings": [{"role": "roles/x", "members": ["user:a"]}]}

        # when
        updated, changed = with_bindings(policy, "user:a", ("roles/x",), RUNNER_GRANTS_ONLY)

        # then
        assert changed and updated["version"] == 3
        assert {"role": "roles/x", "members": ["user:a"], "condition": RUNNER_GRANTS_ONLY} in updated["bindings"]
        assert {"role": "roles/x", "members": ["user:a"]} in updated["bindings"]

    def test_given_conditional_binding_when_revoking_without_condition_then_left_alone(self) -> None:
        # given
        policy = {
            "version": 3,
            "bindings": [{"role": "roles/x", "members": ["user:a"], "condition": RUNNER_GRANTS_ONLY}],
        }

        # when
        _, changed = without_bindings(policy, "user:a", ("roles/x",))

        # then
        assert not changed

    def test_given_conditional_binding_when_revoking_it_then_binding_removed(self) -> None:
        # given
        policy = {
            "version": 3,
            "bindings": [{"role": "roles/x", "members": ["user:a"], "condition": RUNNER_GRANTS_ONLY}],
        }

        # when
        updated, changed = without_bindings(policy, "user:a", ("roles/x",), RUNNER_GRANTS_ONLY)

        # then
        assert changed and updated["bindings"] == []
