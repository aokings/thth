#!/usr/bin/env python3
"""Opt-in Docker acceptance using synthetic data only; never reads live roots."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

REPO = Path(__file__).resolve().parents[1]
PROBE = r'''
import json, os
from pathlib import Path
from thth.report_isolation import validate_environment
from thth.report_service import ReportContext, execute_report
validate_environment('/tenant', {'demo': 'project'})
# Other tenant and host paths were never mounted.
assert not Path('/other-tenant').exists()
assert not Path('/var/run/docker.sock').exists()
try:
    Path('/tenant/write-probe').write_text('unexpected')
except OSError:
    pass
else:
    raise AssertionError('tenant mount is writable')
payload = execute_report(ReportContext({'demo':'project'}),
    {'operation':'operations_handoff','account':'demo'})
node = payload['reports']['demo']['by_account']['demo']
print(json.dumps({'count':node['queue']['counts']['unattributed_malformed'],
                  'uid':os.getuid()}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Locally available Python 3.10+ image (prefer digest)')
    args = parser.parse_args()
    # --pull never ensures this explicit integration test cannot silently fetch.
    uid = os.getuid()
    if uid == 0:
        raise SystemExit('Run as a non-root host user')
    with tempfile.TemporaryDirectory(prefix='thth-isolation-') as directory:
        roots = []
        for n in (1, 2):
            root = Path(directory) / f'tenant-{n}'
            root.mkdir(mode=0o700)
            (root / 'accounts').mkdir()
            queue = root / 'repos/demo/docs/sns/queue'
            queue.mkdir(parents=True)
            for i in range(n):
                (queue / f'{i}.md').write_text('synthetic malformed draft')
            # Full account definition, with no secrets or external resources.
            cfg = dict.fromkeys(['account','project','media','handle','repo_dir',
                'queue_dir','replies_dir','quiet_hours','min_interval_hours',
                'collect_days','hashtags','stale_days','env','token','ping',
                'timeout','dry_run_env','production'])
            cfg.update(account='demo', project='project', media='threads',
                repo_dir='/tenant/repos/demo', queue_dir='docs/sns/queue',
                replies_dir='data/sns/replies')
            (root / 'accounts/demo.json').write_text(json.dumps(cfg))
            roots.append(root)

        def run(root):
            container_name = 'thth-isolation-' + uuid.uuid4().hex
            command = ['docker','run','--name',container_name,'--rm','--pull','never','--network','none',
                '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
                '--pids-limit','32','--memory','128m','--cpus','1',
                '--user',f'{uid}:{os.getgid()}',
                '--mount',f'type=bind,src={REPO / "thth"},dst=/code/thth,readonly',
                '--mount',f'type=bind,src={root},dst=/tenant,readonly',
                '--env','PYTHONPATH=/code','--env','PYTHONDONTWRITEBYTECODE=1',
                '--env','THTH_ROOT=/tenant','--entrypoint','python3', args.image, '-c',PROBE]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            finally:
                subprocess.run(['docker','rm','-f',container_name],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if result.returncode:
                raise RuntimeError(result.stderr[-2000:])
            return json.loads(result.stdout)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, roots))
        assert [value['count'] for value in results] == [1,2], results
        assert all(value['uid'] == uid for value in results), results
        assert all(not (root / 'write-probe').exists() for root in roots)
        print(json.dumps({'result':'passed','parallel_tenants':2,
            'same_account_and_project':True, 'distinct_observations':[1,2],
            'read_only_mounts':True, 'network':'none', 'image':args.image}))


if __name__ == '__main__':
    main()
