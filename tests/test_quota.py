from duckless.core.machine import MachineType
from duckless.core.quota import Quota, check_quotas, required_quotas

N2_32 = MachineType.parse("n2-highmem-32")


class TestRequiredQuotas:
    def test_given_spot_without_preemptible_quota_when_computing_then_falls_back_on_standard_quotas(self) -> None:
        # given: a new project, PREEMPTIBLE_CPUS limit 0 (seen in the spike)
        quotas = {"PREEMPTIBLE_CPUS": Quota(0, 0)}

        # when
        needed = required_quotas(N2_32, 4, spot=True, quotas=quotas)

        # then
        assert needed == {"N2_CPUS": 32, "CPUS": 32, "LOCAL_SSD_TOTAL_GB": 1500}

    def test_given_spot_with_preemptible_quota_when_computing_then_uses_preemptible_quotas(self) -> None:
        # given
        quotas = {"PREEMPTIBLE_CPUS": Quota(0, 100), "PREEMPTIBLE_LOCAL_SSD_GB": Quota(0, 3000)}

        # when
        needed = required_quotas(N2_32, 4, spot=True, quotas=quotas)

        # then
        assert needed == {"PREEMPTIBLE_CPUS": 32, "PREEMPTIBLE_LOCAL_SSD_GB": 1500}

    def test_given_no_local_ssd_when_computing_then_no_ssd_quota(self) -> None:
        # when / then
        assert "LOCAL_SSD_TOTAL_GB" not in required_quotas(N2_32, 0, spot=False, quotas={})


class TestCheckQuotas:
    def test_given_unknown_metric_when_checking_then_not_blocking(self) -> None:
        # when
        (check,) = check_quotas({"C4_CPUS": 8}, {})

        # then
        assert check.ok
        assert check.available is None

    def test_given_insufficient_quota_when_checking_then_not_ok(self) -> None:
        # when
        (check,) = check_quotas({"N2_CPUS": 32}, {"N2_CPUS": Quota(usage=180, limit=200)})

        # then
        assert not check.ok
        assert check.available == 20
