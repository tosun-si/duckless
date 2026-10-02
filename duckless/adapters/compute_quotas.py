from collections.abc import Mapping

from google.cloud import compute_v1

from duckless.core.quota import Quota


class ComputeQuotaReader:
    def __init__(self, client: compute_v1.RegionsClient, project: str) -> None:
        self._client = client
        self._project = project

    def regional_quotas(self, region: str) -> Mapping[str, Quota]:
        regional = self._client.get(project=self._project, region=region)
        return {q.metric: Quota(usage=q.usage, limit=q.limit) for q in regional.quotas}
