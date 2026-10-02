# /// script
# requires-python = ">=3.13"
# dependencies = ["google-cloud-batch>=0.17", "google-cloud-storage>=2.18"]
# ///
"""Spike: submit a DuckLess job to Cloud Batch, follow it, report timings.

    uv run submit.py jobs/tpch_queries.sql --machine n2-highmem-32 --local-ssd 2 --spot --env TPCH_SF=100
    uv run submit.py jobs/customer_segments.py --machine n2-highmem-16 --env TPCH_SF=100
"""

import argparse
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import batch_v1, storage

SCRATCH = "/mnt/disks/scratch"
RUNNER_EXIT_CODES = (1, 2)  # job failed / bad usage — see duckless_runtime.__main__
DONE_STATES = frozenset({batch_v1.JobStatus.State.SUCCEEDED, batch_v1.JobStatus.State.FAILED})


@dataclass(frozen=True)
class JobSpec:
    project: str
    region: str
    job_id: str
    image: str
    mode: str  # sql | py
    source_uri: str
    bucket: str
    machine: str
    spot: bool
    local_ssd_count: int
    service_account: str
    network: str
    subnetwork: str
    env: tuple[tuple[str, str], ...]
    max_run_seconds: int


# ---------- pure ----------

# Allowed local SSD counts by vCPU range (N2 only for the spike; Batch fails late otherwise).
N2_LOCAL_SSD_COUNTS = (
    (range(2, 11), (1, 2, 4, 8, 16, 24)),
    (range(12, 21), (2, 4, 8, 16, 24)),
    (range(22, 41), (4, 8, 16, 24)),
    (range(42, 81), (8, 16, 24)),
    (range(82, 129), (16, 24)),
)


def allowed_local_ssd_counts(machine: str) -> tuple[int, ...] | None:
    """None = unknown family, let GCE decide."""
    family, _, vcpus, *_ = [*machine.split("-"), "", ""]
    if family != "n2" or not vcpus.isdigit():
        return None
    return next(((0, *counts) for cpus, counts in N2_LOCAL_SSD_COUNTS if int(vcpus) in cpus), None)

def job_id_for(source: Path, now: datetime, nonce: str) -> str:
    stem = "".join(c if c.isalnum() else "-" for c in source.stem.lower())[:30].strip("-")
    return f"dl-{stem}-{now:%Y%m%d-%H%M%S}-{nonce[:4]}"


def runner_env(spec: JobSpec) -> dict[str, str]:
    return {
        "DUCKLESS_JOB_ID": spec.job_id,
        "DUCKLESS_BUCKET": spec.bucket,
        "DUCKLESS_METRICS_URI": f"gs://{spec.bucket}/runs/{spec.job_id}/metrics.json",
        "GOOGLE_CLOUD_PROJECT": spec.project,
        **dict(spec.env),
    }


def build_job(spec: JobSpec) -> batch_v1.Job:
    # Local SSD is mounted by Batch on the host; the script runnable (root, on the host)
    # opens it to the non-root container user.
    prepare_scratch = batch_v1.Runnable(
        script=batch_v1.Runnable.Script(text=f"mkdir -p {SCRATCH} && chmod 1777 {SCRATCH}")
    )
    runner = batch_v1.Runnable(
        container=batch_v1.Runnable.Container(
            image_uri=spec.image,
            commands=[spec.mode, spec.source_uri],  # task volumes are bind-mounted at the same path by default
        ),
        environment=batch_v1.Environment(variables=runner_env(spec)),
    )
    task = batch_v1.TaskSpec(
        runnables=[prepare_scratch, runner] if spec.local_ssd_count else [runner],
        volumes=[batch_v1.Volume(device_name="scratch", mount_path=SCRATCH)] if spec.local_ssd_count else [],
        max_run_duration=f"{spec.max_run_seconds}s",
        # Batch allows a single lifecycle policy: fail fast on runner errors, so retries
        # are only spent on infra failures (Spot preemption = exit code 50001).
        max_retry_count=2,
        lifecycle_policies=[
            batch_v1.LifecyclePolicy(
                action=batch_v1.LifecyclePolicy.Action.FAIL_TASK,
                action_condition=batch_v1.LifecyclePolicy.ActionCondition(exit_codes=list(RUNNER_EXIT_CODES)),
            ),
        ],
    )
    disks = (
        [batch_v1.AllocationPolicy.AttachedDisk(
            new_disk=batch_v1.AllocationPolicy.Disk(type_="local-ssd", size_gb=375 * spec.local_ssd_count),
            device_name="scratch",
        )]
        if spec.local_ssd_count else []
    )
    policy = batch_v1.AllocationPolicy.InstancePolicy(
        machine_type=spec.machine,
        provisioning_model=(
            batch_v1.AllocationPolicy.ProvisioningModel.SPOT if spec.spot
            else batch_v1.AllocationPolicy.ProvisioningModel.STANDARD
        ),
        disks=disks,
    )
    allocation = batch_v1.AllocationPolicy(
        location=batch_v1.AllocationPolicy.LocationPolicy(allowed_locations=[f"regions/{spec.region}"]),
        instances=[batch_v1.AllocationPolicy.InstancePolicyOrTemplate(policy=policy)],
        service_account=batch_v1.ServiceAccount(email=spec.service_account),
        network=batch_v1.AllocationPolicy.NetworkPolicy(network_interfaces=[
            batch_v1.AllocationPolicy.NetworkInterface(
                network=spec.network, subnetwork=spec.subnetwork, no_external_ip_address=True
            )
        ]),
    )
    return batch_v1.Job(
        task_groups=[batch_v1.TaskGroup(task_spec=task, task_count=1)],
        allocation_policy=allocation,
        logs_policy=batch_v1.LogsPolicy(destination=batch_v1.LogsPolicy.Destination.CLOUD_LOGGING),
        labels={"app": "duckless", "spike": "true"},
    )


def timeline(job: batch_v1.Job) -> list[dict]:
    """Status events as (state, seconds since job creation)."""
    created = job.create_time.timestamp()
    return [
        {"state": e.task_state.name, "type": e.type_,
         "at_s": round(e.event_time.timestamp() - created, 1), "description": e.description[:160]}
        for e in job.status.status_events
    ]


# ---------- effects ----------

def upload_source(client: storage.Client, bucket: str, job_id: str, source: Path) -> str:
    blob = client.bucket(bucket).blob(f"runs/{job_id}/{source.name}")
    blob.upload_from_filename(str(source))
    return f"gs://{bucket}/runs/{job_id}/{source.name}"


def follow(client: batch_v1.BatchServiceClient, name: str, every_s: int = 10) -> batch_v1.Job:
    job = client.get_job(name=name)
    last = None
    while job.status.state not in DONE_STATES:
        if job.status.state != last:
            print(f"{datetime.now(UTC):%H:%M:%S}  {job.status.state.name}", flush=True)
            last = job.status.state
        time.sleep(every_s)
        job = client.get_job(name=name)
    return job


def read_metrics(client: storage.Client, bucket: str, job_id: str) -> dict | None:
    blob = client.bucket(bucket).blob(f"runs/{job_id}/metrics.json")
    return json.loads(blob.download_as_text()) if blob.exists() else None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", type=Path, nargs="?", help="job.sql or job.py")
    p.add_argument("--attach", metavar="JOB_ID", help="follow/report an existing job instead of submitting")
    p.add_argument("--project", default=os.environ.get("DUCKLESS_PROJECT"))
    p.add_argument("--region", default=os.environ.get("DUCKLESS_REGION", "europe-west1"))
    p.add_argument("--bucket", default=os.environ.get("DUCKLESS_BUCKET"))
    p.add_argument("--image", default=os.environ.get("DUCKLESS_IMAGE"))
    p.add_argument("--service-account", default=os.environ.get("DUCKLESS_SA"))
    p.add_argument("--network", default=os.environ.get("DUCKLESS_NETWORK"))
    p.add_argument("--subnetwork", default=os.environ.get("DUCKLESS_SUBNETWORK"))
    p.add_argument("--machine", default="n2-highmem-16")
    p.add_argument("--local-ssd", type=int, default=1, help="number of 375 GB local SSDs (0 = none)")
    p.add_argument("--spot", action="store_true")
    p.add_argument("--env", action="append", default=[], help="KEY=VALUE passed to the runner")
    p.add_argument("--max-run-seconds", type=int, default=3 * 3600)
    return p.parse_args()


def build_report(job: batch_v1.Job, metrics: dict | None) -> dict:
    policy = job.allocation_policy.instances[0].policy
    return {
        "job_id": job.name.rsplit("/", 1)[-1],
        "machine": policy.machine_type,
        "spot": policy.provisioning_model == batch_v1.AllocationPolicy.ProvisioningModel.SPOT,
        "local_ssd_gb": sum(d.new_disk.size_gb for d in policy.disks),
        "state": job.status.state.name,
        "job_lifetime_s": round(job.update_time.timestamp() - job.create_time.timestamp(), 1),
        "run_duration_s": job.status.run_duration.total_seconds() if job.status.run_duration else None,
        "timeline": timeline(job),
        "runner": metrics,
    }


def submit(args: argparse.Namespace, gcs: storage.Client, batch: batch_v1.BatchServiceClient) -> str:
    job_id = job_id_for(args.source, datetime.now(UTC), uuid.uuid4().hex)
    spec = JobSpec(
        project=args.project, region=args.region, job_id=job_id, image=args.image,
        mode="sql" if args.source.suffix == ".sql" else "py",
        source_uri=upload_source(gcs, args.bucket, job_id, args.source),
        bucket=args.bucket, machine=args.machine, spot=args.spot, local_ssd_count=args.local_ssd,
        service_account=args.service_account, network=args.network, subnetwork=args.subnetwork,
        env=tuple(tuple(kv.split("=", 1)) for kv in args.env), max_run_seconds=args.max_run_seconds,
    )
    created = batch.create_job(
        parent=f"projects/{spec.project}/locations/{spec.region}", job_id=job_id, job=build_job(spec)
    )
    print(f"submitted {created.name}  ({spec.machine}{' spot' if spec.spot else ''}, {spec.local_ssd_count}x local SSD)")
    return created.name


def main() -> None:
    args = parse_args()
    allowed = allowed_local_ssd_counts(args.machine)
    if not args.attach and allowed and args.local_ssd not in allowed:
        raise SystemExit(f"{args.machine} accepts {allowed} local SSDs, not {args.local_ssd}")
    gcs, batch = storage.Client(project=args.project), batch_v1.BatchServiceClient()
    name = (
        f"projects/{args.project}/locations/{args.region}/jobs/{args.attach}" if args.attach
        else submit(args, gcs, batch)
    )
    job = follow(batch, name)
    report = build_report(job, read_metrics(gcs, args.bucket, name.rsplit("/", 1)[-1]))

    out = Path(__file__).parent / "results" / f"{report['job_id']}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "runner"}, indent=2, default=str))
    print(f"report: {out}")


if __name__ == "__main__":
    main()
