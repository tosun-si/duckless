class DucklessError(Exception):
    """Base class for every rule violation DuckLess reports to the user."""


class InvalidMachineTypeError(DucklessError):
    def __init__(self, name: str) -> None:
        super().__init__(f"'{name}' is not a Compute Engine machine type (expected <family>-<kind>-<vcpus>)")


class InvalidLocalSsdCountError(DucklessError):
    def __init__(self, machine: str, count: int, allowed: tuple[int, ...]) -> None:
        super().__init__(f"{machine} accepts {', '.join(map(str, allowed))} local SSDs, not {count}")


class InvalidJobError(DucklessError):
    pass


class JobNotFoundError(DucklessError):
    def __init__(self, job_id: str) -> None:
        super().__init__(f"job '{job_id}' not found")
