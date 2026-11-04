"""Bounded USB uploads into Zelda's app container; existing nonempty files are never replaced."""
import argparse
import asyncio
from contextlib import contextmanager, nullcontext
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import time

CHUNK = 1 << 20
REMOTE = '/Documents/work'
ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def source_file(path):
    with path.open('rb') as stream:
        # Large sequential resources should not displace the user's other files from the Mac page cache.
        if sys.platform == 'darwin' and os.fstat(stream.fileno()).st_size > 64 * CHUNK:
            fcntl.fcntl(stream.fileno(), fcntl.F_NOCACHE, 1)
        yield stream


@contextmanager
def save_source_lock(path):
    # Share the host filesystem's existing volume lock so a running game cannot commit midway through import.
    with path.open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def remote_path(path):
    parts = PurePosixPath(path).parts
    if not path.startswith(REMOTE + '/') or '..' in parts or '.' in path.split('/') or '\\' in path:
        raise ValueError('destination must stay inside Documents/work')
    return str(PurePosixPath(path))


def local_hash(path, length=None):
    digest = hashlib.sha256()
    with source_file(path) as source:
        while length is None or length:
            block = source.read(CHUNK if length is None else min(CHUNK, length))
            if not block:
                if length: raise ValueError('source ended before checkpoint')
                break
            digest.update(block)
            if length is not None: length -= len(block)
    return digest.hexdigest()


async def remote_hash(afc, path, size):
    digest = hashlib.sha256()
    total=size; next_report=time.monotonic()+10
    if total>64*CHUNK: print(json.dumps({'phase':'verify','bytes':0,'total':total}),flush=True)
    handle = await afc.fopen(path, 'r')
    try:
        while size:
            block = await afc.fread(handle, min(CHUNK, size))
            if not block or len(block) > min(CHUNK, size): raise ValueError('unexpected device read length')
            digest.update(block); size -= len(block)
            if total>64*CHUNK and time.monotonic()>=next_report:
                print(json.dumps({'phase':'verify','bytes':total-size,'total':total}),flush=True);next_report=time.monotonic()+10
    finally:
        await afc.fclose(handle)
    return digest.hexdigest()


async def directory(afc, path):
    if not hasattr(afc,'_zelda_directories'): afc._zelda_directories=set()
    current = ''
    for component in PurePosixPath(path).parts[1:]:
        current += '/' + component
        if current in afc._zelda_directories: continue
        if not await afc.exists(current): await afc.makedirs(current)
        if (await afc.stat(current))['st_ifmt'] != 'S_IFDIR':
            raise ValueError('device parent is not a directory')
        afc._zelda_directories.add(current)


class InterruptedUpload(Exception):
    pass


async def upload(afc, source, target, stop_after=None, *, verify=True):
    target = remote_path(target)
    if source.is_symlink() or not source.is_file(): raise ValueError('source must be a regular file')
    original = source.stat()
    if verify and original.st_size>64*CHUNK: print(json.dumps({'phase':'hash_source','bytes':original.st_size}),flush=True)
    expected = local_hash(source) if verify else None
    await directory(afc, str(PurePosixPath(target).parent))
    if await afc.exists(target):
        existing = await afc.stat(target)
        if existing['st_ifmt'] != 'S_IFREG': raise ValueError('device target is not a regular file')
        if existing['st_size'] == original.st_size and (not verify or await remote_hash(afc, target, original.st_size) == expected):
            return {'bytes': original.st_size, 'sha256': expected, 'verified': verify, 'skipped': True, 'resumed': 0}
        # The failed devicectl transfer created only this empty resource file.
        if existing['st_size'] == 0 and target == REMOTE + '/romfs-host.bin': await afc.rm(target)
        else: raise ValueError('refusing to replace existing device data')
    partial = target + '.uploading'
    offset = 0
    if await afc.exists(partial):
        info = await afc.stat(partial)
        if info['st_ifmt'] != 'S_IFREG' or info['st_size'] > original.st_size: raise ValueError('invalid upload checkpoint')
        offset = info['st_size']
        if verify and await remote_hash(afc, partial, offset) != local_hash(source, offset):
            raise ValueError('checkpoint content differs; leaving it untouched')
    free = int((await afc.get_device_info())['FSFreeBytes'])
    if free < original.st_size - offset + (2 << 30): raise ValueError('device needs more free storage (2 GiB reserve)')
    handle = await afc.fopen(partial, 'r+' if offset else 'w')
    sent = offset; next_report = time.monotonic() + 10
    try:
        await afc.fseek(handle, offset)
        with source_file(source) as stream:
            stream.seek(offset)
            while block := stream.read(CHUNK):
                await afc.fwrite(handle, block, chunk_size=CHUNK)
                sent += len(block)
                if time.monotonic() >= next_report:
                    print(json.dumps({'phase': 'write', 'bytes': sent, 'total': original.st_size}), flush=True)
                    next_report = time.monotonic() + 10
                if stop_after and sent >= stop_after: raise InterruptedUpload()
    finally:
        await afc.fclose(handle)
    now = source.stat()
    if (now.st_ino, now.st_size, now.st_mtime_ns) != (original.st_ino, original.st_size, original.st_mtime_ns):
        raise ValueError('source changed during transfer')
    if (await afc.stat(partial))['st_size'] != original.st_size:
        raise ValueError('device file size mismatch')
    if verify and await remote_hash(afc, partial, original.st_size) != expected:
        raise ValueError('device readback checksum mismatch')
    if await afc.exists(target): raise ValueError('device target appeared during transfer')
    await afc.rename(partial, target)
    return {'bytes': sent, 'sha256': expected, 'verified': verify, 'skipped': False, 'resumed': offset}


async def connect(device):
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.house_arrest import HouseArrestService
    client = await create_using_usbmux(serial=device, autopair=False, connection_type='USB',
        pairing_records_cache_folder=ROOT / 'work/ios/pairing')
    try: service = await HouseArrestService.create(client, os.environ.get('ZELDA_IOS_BUNDLE','local.zelda-mobile.ios'))
    except BaseException:
        await client.close(); raise
    return client, service


async def probe(device):
    source = ROOT / 'work/tmp/ios-transfer-probe.bin'
    if source.exists(): raise ValueError('probe source already exists')
    with source.open('xb') as stream:
        for _ in range(128): stream.write(os.urandom(CHUNK))
    target = REMOTE + '/ios-transfer-probe.bin'
    client, afc = await connect(device)
    try:
        if await afc.exists(target) or await afc.exists(target + '.uploading'): raise ValueError('device probe already exists')
        try: await upload(afc, source, target, stop_after=8 * CHUNK)
        except InterruptedUpload: pass
    finally:
        await afc.close(); await client.close()
    client, afc = await connect(device)
    try:
        result = await upload(afc, source, target)
        if result['resumed'] != 8 * CHUNK: raise ValueError('probe did not resume')
        repeated = await upload(afc, source, target)
        if not repeated['skipped']: raise ValueError('verified upload was not reused')
        await afc.rm(target)
        source.unlink()
        print(json.dumps({'probe': result, 'repeated': repeated}), flush=True)
    finally:
        await afc.close(); await client.close()


async def run(args):
    if args.mode == 'probe': return await probe(args.device)
    if args.no_verify:
        print(json.dumps({'verification': 'size_only', 'mode': args.mode}), flush=True)
    client, afc = await connect(args.device)
    try:
        if args.mode == 'status':
            print(await afc.get_device_info()); print(await afc.listdir(REMOTE)); return
        chain = re.search(r'#define DMSH_CHAIN "([0-9a-f]+)"', (ROOT / 'work/build/dmsh_chain.h').read_text())[1]
        shaders = ROOT / 'work/shaders'
        if args.mode == 'ready':
            folder=shaders/('b-'+chain)
            expected={f.name for f in folder.iterdir() if f.suffix in ('.metallib','.json')}
            observed=set(await afc.listdir(REMOTE+'/shaders/b-'+chain))
            size=(ROOT/'work/romfs-host.bin').stat().st_size
            if not expected.issubset(observed) or (await afc.stat(REMOTE+'/romfs-host.bin'))['st_size']!=size:
                raise ValueError('resource transfer is incomplete')
            marker=ROOT/'work/ios/resources-ready.json'
            marker.write_text(json.dumps({'chain':chain,'romfs_bytes':size,'libraries':sum(n.endswith('.metallib') for n in expected)})+'\n')
            # The marker only records the verified state above; a newer verification replaces it.
            if await afc.exists(REMOTE+'/shaders/ios-ready.json'): await afc.rm(REMOTE+'/shaders/ios-ready.json')
            print(await upload(afc,marker,REMOTE+'/shaders/ios-ready.json'));return
        if args.mode == 'romfs': pairs = [(ROOT / 'work/romfs-host.bin', REMOTE + '/romfs-host.bin')]
        elif args.mode == 'shaders':
            folder = shaders / ('b-' + chain)
            pairs = [(f, REMOTE + '/shaders/' + str(f.relative_to(shaders))) for f in sorted(folder.iterdir()) if f.suffix in ('.metallib', '.json')]
            pairs += [(shaders / 'shader_map.tsv', REMOTE + '/shaders/shader_map.tsv')]
            # Each device learns its own pipeline list while playing; the Mac's only seeds a new install.
            if not await afc.exists(REMOTE + '/shaders/pso_user.tsv'):
                pairs += [(shaders / 'pso_user.tsv', REMOTE + '/shaders/pso_user.tsv')]
            # The on-device compiler (ios/shaders) translates new variant requests from the indexed shader code.
            pairs += [(shaders / name, REMOTE + '/shaders/' + name) for name in ('index.json', 'build-failures.json')]
            pairs += [(f, REMOTE + '/shaders/code/' + f.name) for f in sorted((shaders / 'code').iterdir()) if f.suffix == '.bin']
        elif args.mode == 'ir':
            from build_shader_ir import cache_directory
            folder = cache_directory(shaders / ('b-' + chain))
            pairs = [(f, REMOTE + '/shaders/' + str(f.relative_to(shaders))) for f in sorted(folder.iterdir()) if f.suffix in ('.metallib', '.json')]
            if not pairs: raise ValueError('build offline Metal IR before transferring its cache')
        elif args.mode == 'seed':
            saved=ROOT/'work/save'
            pairs=[(f,REMOTE+'/save/'+str(f.relative_to(saved))) for f in sorted(saved.rglob('*')) if f.is_file()]
            settings=json.loads((ROOT/'work/settings.json').read_text())
            settings.update(target_fps=60,render_resolution='1080p',sound=True,fullscreen=False)
            seed=ROOT/'work/ios/settings-seed.json';seed.write_text(json.dumps(settings,indent=2)+'\n')
            pairs.append((seed,REMOTE+'/settings.json'))
            pairs.append((ROOT/'script/scenes/first-save.input',REMOTE+'/first-save.input'))
        else: raise ValueError('unsupported transfer mode')
        total = sum(source.stat().st_size for source, _ in pairs)
        free = int((await afc.get_device_info())['FSFreeBytes'])
        # Per-file checks also account for checkpoints. This first-pass guard avoids partially filling a new install.
        missing = total
        # If the entire batch fits, per-file remote size queries cannot improve the space decision.
        if missing + (2 << 30) > free:
            missing = 0
            for source, target in pairs:
                if not await afc.exists(target):
                    partial = target + '.uploading'
                    checkpoint = (await afc.stat(partial))['st_size'] if await afc.exists(partial) else 0
                    missing += max(0, source.stat().st_size - checkpoint)
        if missing + (2 << 30) > free: raise ValueError('device needs more free storage for this transfer')
        for parent in sorted({str(PurePosixPath(target).parent) for _,target in pairs}): await directory(afc,parent)
        done = 0; started = time.monotonic()
        with open(args.report, 'x') as report:
            iterator = iter(pairs)
            while batch := list(itertools.islice(iterator, 4)):
                async with asyncio.TaskGroup() as group:
                    tasks=[group.create_task(upload(afc,source,target,verify=not args.no_verify)) for source,target in batch]
                results=[task.result() for task in tasks]
                for (_, target), result in zip(batch, results):
                    report.write(json.dumps({'target': target, **result}) + '\n')
                report.flush(); done += len(batch)
                if done % 400 == 0 or done == len(pairs):
                    print(json.dumps({'files': done, 'total_files': len(pairs), 'total_bytes': total, 'elapsed': time.monotonic()-started}), flush=True)
    finally:
        await afc.close(); await client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['status', 'probe', 'romfs', 'shaders', 'ir', 'seed', 'ready'])
    parser.add_argument('--device', required=True)
    parser.add_argument('--report')
    parser.add_argument('--no-verify', action='store_true', help='Skip content hashes and readback for game assets; keep size checks')
    args = parser.parse_args()
    if args.no_verify and args.mode not in ('romfs', 'shaders', 'ir'): parser.error('--no-verify applies only to game assets')
    if args.mode in ('romfs', 'shaders', 'ir', 'seed') and not args.report: parser.error('--report is required for transfers')
    with save_source_lock(ROOT/'work/save.lock') if args.mode=='seed' else nullcontext():
        asyncio.run(run(args))
