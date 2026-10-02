"""Prints a compact view of a results/<job-id>.json report."""

import json
import sys


def lines(report: dict) -> list[str]:
    runner = report.get("runner") or {}
    head = [
        f"{report['job_id']}  {report['machine']}{' spot' if report['spot'] else ''}  ssd={report['local_ssd_gb']}GB  {report['state']}",
        f"  lifetime {report['job_lifetime_s']}s  run {report['run_duration_s']}s",
        *(f"  t+{e['at_s']:>6}s  {e['description'].split(' for job')[0][:100]}" for e in report["timeline"]),
        f"  runner: {runner.get('status')}  {runner.get('seconds')}s  rss={runner.get('peak_rss_gb')}GB  spill={runner.get('peak_spill_gb')}GB  {runner.get('error') or ''}",
    ]
    steps = [f"  {s['seconds']:>8}s  {s['type']:<7} {' '.join(s['sql'].split())[:70]}  {str(s.get('preview', [])[:1])[:60]}"
             for s in runner.get("steps", ())]
    return head + steps


if __name__ == "__main__":
    print("\n".join(line for path in sys.argv[1:] for line in lines(json.load(open(path)))))
