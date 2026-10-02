import pytest

from duckless.core.errors import InvalidLocalSsdCountError, InvalidMachineTypeError
from duckless.core.machine import (
    MachineType,
    allowed_local_ssd_counts,
    default_local_ssd_count,
    validate_local_ssd_count,
)


class TestMachineType:
    def test_given_highmem_name_when_parsing_then_family_and_vcpus_are_extracted(self) -> None:
        # when
        machine = MachineType.parse("N2-HighMem-32")

        # then
        assert (machine.name, machine.family, machine.kind, machine.vcpus) == ("n2-highmem-32", "n2", "highmem", 32)
        assert machine.cpu_quota_metric == "N2_CPUS"
        assert not machine.bundled_local_ssd

    def test_given_lssd_variant_when_parsing_then_local_ssd_is_bundled(self) -> None:
        # when / then
        assert MachineType.parse("c3-standard-22-lssd").bundled_local_ssd

    @pytest.mark.parametrize("name", ["", "n2", "n2-highmem", "highmem-32-n2", "n2-highmem-x"])
    def test_given_malformed_name_when_parsing_then_raises(self, name: str) -> None:
        # when / then
        with pytest.raises(InvalidMachineTypeError):
            MachineType.parse(name)


class TestLocalSsdRules:
    @pytest.mark.parametrize(
        ("name", "allowed"),
        [
            ("n2-highmem-8", (0, 1, 2, 4, 8, 16, 24)),
            ("n2-highmem-16", (0, 2, 4, 8, 16, 24)),  # measured in the spike: 1 is rejected
            ("n2-highmem-32", (0, 4, 8, 16, 24)),
            ("n2-highmem-64", (0, 8, 16, 24)),
            ("c3-standard-22-lssd", (0,)),
            ("n2d-highmem-32", None),
        ],
    )
    def test_given_machine_when_listing_allowed_counts_then_matches_gce_rules(
        self, name: str, allowed: tuple[int, ...] | None
    ) -> None:
        # when / then
        assert allowed_local_ssd_counts(MachineType.parse(name)) == allowed

    def test_given_disallowed_count_when_validating_then_raises_with_allowed_values(self) -> None:
        # when / then
        with pytest.raises(InvalidLocalSsdCountError, match="0, 2, 4, 8, 16, 24"):
            validate_local_ssd_count(MachineType.parse("n2-highmem-16"), 1)

    def test_given_unknown_family_when_validating_then_lets_gce_decide(self) -> None:
        # when / then
        validate_local_ssd_count(MachineType.parse("n2d-highmem-32"), 3)

    @pytest.mark.parametrize(
        ("name", "count"), [("n2-highmem-16", 2), ("n2-highmem-32", 4), ("c3-standard-22-lssd", 0)]
    )
    def test_given_machine_when_defaulting_then_smallest_non_zero_count(self, name: str, count: int) -> None:
        # when / then
        assert default_local_ssd_count(MachineType.parse(name)) == count
